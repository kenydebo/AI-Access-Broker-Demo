"""Broker core: caller identities arrive only from a trusted transport."""
import json
import math
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

FIELDS = frozenset(('Id', 'Name', 'StageName', 'CloseDate', 'Amount', 'NextStep'))
AUDIENCE = 'salesforce-read'

class Denied(Exception):
    pass

@dataclass(frozen=True)
class Caller:
    subject: str

@dataclass(frozen=True)
class Request:
    record: str
    action: str
    fields: tuple
    task: str
    audience: str

    def as_dict(self):
        return dict(record=self.record, action=self.action, fields=list(self.fields),
                    task=self.task, audience=self.audience)

def normalize(value):
    if not isinstance(value, dict) or set(value) != {'type','record','action','fields','task','audience'}:
        raise Denied('invalid authorization details')
    if value['type'] != 'salesforce_record' or value['record'] not in ('A','B'):
        raise Denied('unknown resource')
    if value['action'] != 'read' or value['audience'] != AUDIENCE:
        raise Denied('action or audience denied')
    fields = value['fields']
    if not isinstance(fields, list) or not fields or any(not isinstance(f,str) for f in fields):
        raise Denied('invalid fields')
    if len(set(fields)) != len(fields) or not set(fields) <= FIELDS:
        raise Denied('fields denied')
    if not isinstance(value['task'], str) or value['task'] != 'opportunity-summary':
        raise Denied('task denied')
    return Request(value['record'],value['action'],tuple(sorted(fields)),value['task'],value['audience'])

class FixturePolicy:
    """Offline test fixture. This is NOT an OPA evaluator."""
    def __init__(self):
        self.enabled = True
    def allow(self, attributes):
        r = attributes['request']
        return self.enabled and attributes['agent'] in ('AgentA','AgentB') and r['record'] == attributes['allowed_record'] and r['task'] == attributes['allowed_task'] and r['action'] == 'read' and r['audience'] == AUDIENCE and set(r['fields']) <= FIELDS

def find_opa():
    local = Path(__file__).resolve().parent.parent / '.tools' / 'opa'
    if local.is_file() and os.access(str(local),os.X_OK):
        return str(local)
    return shutil.which('opa')

class OPAPolicy:
    def __init__(self, policy_path=None, executable=None, query='data.broker.allow', data_path=None):
        self.path = str(policy_path or Path(__file__).resolve().parent.parent / 'policy' / 'broker.rego')
        self.executable = executable or find_opa() or 'opa'
        self.query = query
        self.data_path = str(data_path or Path(__file__).resolve().parent.parent / 'examples' / 'lab.example.json')
    def decide(self, attributes):
        try:
            result=subprocess.run([self.executable,'eval','--format=json','--data',self.path,
                '--data',self.data_path,'--stdin-input','data.broker.decision'],
                input=json.dumps(attributes),text=True,capture_output=True,timeout=3,check=True)
            value=json.loads(result.stdout)['result'][0]['expressions'][0]['value']
            if not isinstance(value,dict) or set(value)!={'allow','reasons'} or type(value['allow']) is not bool or not isinstance(value['reasons'],list) or any(not isinstance(r,str) or len(r)>100 for r in value['reasons']):
                raise ValueError('invalid decision')
            return value
        except (OSError,subprocess.SubprocessError,ValueError,KeyError,TypeError,IndexError):
            return {'allow':False,'reasons':['opa_unavailable_or_invalid']}

    def allow(self, attributes):
        try:
            result = subprocess.run([self.executable,'eval','--format=json','--data',self.path,
                '--data',self.data_path,'--stdin-input',self.query],input=json.dumps(attributes),text=True,
                capture_output=True,timeout=3,check=True)
            parsed = json.loads(result.stdout)
            expressions = parsed['result']
            return len(expressions) == 1 and len(expressions[0]['expressions']) == 1 and expressions[0]['expressions'][0]['value'] is True
        except (OSError,subprocess.SubprocessError,ValueError,KeyError,TypeError,IndexError):
            return False

# Trusted broker-side assignment. Never populated from caller request fields.
TEST_IDENTITIES = {'injected-test-A': ('AgentA','A','opportunity-summary'),
                   'injected-test-B': ('AgentB','B','opportunity-summary')}

class Broker:
    def __init__(self, policy, downstream, identities, ttl=60, clock=time.monotonic, context_provider=None):
        if not isinstance(ttl,(float,int)) or not math.isfinite(ttl) or ttl <= 0 or ttl > 300:
            raise ValueError('lab TTL must be >0 and <=300 seconds')
        self.policy,self.downstream,self.identities = policy,downstream,dict(identities)
        self.ttl,self.clock = ttl,clock
        self.context_provider = context_provider
        self.grants = {}
        self.lock = threading.Lock()

    def attributes(self, caller, request):
        identity = self.identities.get(caller.subject)
        if identity is None:
            raise Denied('unknown identity')
        agent,record,task = identity
        attributes = dict(agent=agent,allowed_record=record,allowed_task=task,request=request.as_dict())
        if self.context_provider is not None:
            context = self.context_provider()  # broker-owned verified session, refreshed for each PDP call
            if not isinstance(context,dict) or set(context) & set(attributes):
                raise Denied('invalid trusted context')
            attributes.update(context)
        return attributes

    def permitted(self, caller, request):
        try:
            return self.policy.allow(self.attributes(caller,request)) is True
        except Exception:
            return False

    def issue(self, caller, details):
        request = normalize(details)
        with self.lock:
            try:
                attributes=self.attributes(caller,request)
                binding=json.dumps(attributes,sort_keys=True,separators=(',',':')) if self.context_provider is not None else None
                if self.policy.allow(attributes) is not True:
                    raise Denied('policy denied')
            except Exception:
                raise Denied('policy denied') from None
            now = self.clock()
            self.grants = {k:v for k,v in self.grants.items() if v[2] > now}
            if len(self.grants) >= 1024:
                raise Denied('grant capacity reached')
            grant = secrets.token_urlsafe(32)
            self.grants[grant] = (caller.subject,request,now+self.ttl,binding)
            return dict(grant=grant,expires_in=self.ttl,audience=AUDIENCE)

    def redeem(self, caller, grant, details):
        request = normalize(details)
        if not isinstance(grant,str):
            raise Denied('invalid grant')
        with self.lock:
            stored = self.grants.get(grant)
            if stored is None:
                raise Denied('unknown or consumed grant')
            subject,original,expiry,binding = stored
            if self.clock() >= expiry:
                del self.grants[grant]
                raise Denied('expired grant')
            if subject != caller.subject or original != request:
                raise Denied('grant binding mismatch')
            try:
                attributes=self.attributes(caller,request)
                current_binding=json.dumps(attributes,sort_keys=True,separators=(',',':')) if self.context_provider is not None else None
                if current_binding!=binding or self.policy.allow(attributes) is not True:
                    raise Denied('policy or session binding denied')
            except Exception:
                raise Denied('policy denied on redemption') from None
            del self.grants[grant]  # Atomic consume BEFORE downstream read, even on read failure.
        return self.downstream.read(request)

class MockSalesforce:
    def __init__(self):
        self.calls = 0
    def read(self, request):
        self.calls += 1
        record = dict(Id='mock-'+request.record,Name='Synthetic '+request.record,
                      StageName='Prospecting',CloseDate='2026-12-31',Amount=100,NextStep='Lab only')
        return {f:record[f] for f in request.fields}
