"""Nonexecuting approval-state prototype. Not exposed to MCP, CLI or Salesforce.
No write executor exists: redemption returns a dry-run preview and never performs DML.
Human/workload authentication is injected through trusted interfaces, test-only today.
"""
import hashlib
import json
import math
from pathlib import Path
import secrets
import threading
import time
from dataclasses import dataclass
from .core import Denied, OPAPolicy
from .trusted_context import AuthenticationNotConfigured

PREVIEW_AUDIENCE='salesforce-write-preview'
PREVIEW_FIELDS=frozenset(('NextStep','Amount'))

@dataclass(frozen=True)
class Delegation:
    id: str
    workload: str
    human_user_id: str
    task: str
    records: frozenset
    fields: frozenset
    expires: float
    parent: str = None

@dataclass
class Pending:
    agent_principal: str
    human_user_id: str
    delegation_id: str
    canonical_request: str
    request_hash: str
    expires: float
    status: str = 'pending'
    approved_by: str = None

class ApprovalPreview:
    def __init__(self, records, resolver=None, policy=None, ttl=60, clock=time.monotonic):
        if not isinstance(ttl,(int,float)) or not math.isfinite(ttl) or ttl<=0 or ttl>300:
            raise ValueError('invalid preview TTL')
        self.records=dict(records)
        self.resolver=resolver or AuthenticationNotConfigured()
        self.policy=policy or OPAPolicy(Path(__file__).resolve().parent.parent/'policy'/'approval_preview.rego',query='data.approval_preview.allow')
        self.ttl,self.clock=ttl,clock
        self.lock=threading.RLock()
        self.delegations={}
        self.revoked=set()
        self.pending={}

    def add_delegation(self, delegation):
        """Trusted launcher-only interface, not a tool or public request route."""
        with self.lock:
            if delegation.id in self.delegations or not delegation.workload or not delegation.human_user_id or not delegation.records or not delegation.records<=set(self.records) or not delegation.fields or not delegation.fields<=PREVIEW_FIELDS or not math.isfinite(delegation.expires) or delegation.expires<=self.clock():
                raise Denied('invalid delegation')
            if delegation.parent:
                parent=self.active(delegation.parent)
                if delegation.human_user_id!=parent.human_user_id or delegation.task!=parent.task or not delegation.records<=parent.records or not delegation.fields<=parent.fields or delegation.expires>parent.expires:
                    raise Denied('child delegation broadens parent')
            self.delegations[delegation.id]=delegation

    def revoke(self, delegation_id):
        with self.lock:self.revoked.add(delegation_id)

    def active(self, delegation_id):
        current=delegation_id;seen=set();leaf=None
        while current:
            if current in seen or current in self.revoked:
                raise Denied('revoked delegation')
            seen.add(current)
            delegation=self.delegations.get(current)
            if delegation is None or self.clock()>=delegation.expires:
                raise Denied('unknown or expired delegation')
            leaf=leaf or delegation
            current=delegation.parent
        return leaf

    def canonical(self, agent, request):
        delegation=self.active(agent.delegation_id)
        if agent.principal!=delegation.workload or agent.human_user_id!=delegation.human_user_id:
            raise Denied('agent delegation mismatch')
        if not isinstance(request,dict) or set(request)!={'type','record','action','changes','task','audience'} or request['type']!='salesforce_record_change' or request['action']!='update' or request['audience']!=PREVIEW_AUDIENCE or request['task']!=delegation.task or not isinstance(request['record'],str) or request['record'] not in delegation.records:
            raise Denied('write preview scope denied')
        changes=request['changes']
        if not isinstance(changes,dict) or not changes or not set(changes)<=delegation.fields:
            raise Denied('write fields denied')
        for field,value in changes.items():
            if field=='NextStep' and (not isinstance(value,str) or len(value)>2000):
                raise Denied('invalid NextStep preview')
            if field=='Amount' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise Denied('invalid Amount preview')
        bound={'human_user_id':agent.human_user_id,'workload':agent.principal,
            'delegation_id':delegation.id,'task':delegation.task,'action':'update',
            'audience':PREVIEW_AUDIENCE,'record_alias':request['record'],
            'record_id':self.records[request['record']],'changes':changes}
        canonical=json.dumps(bound,sort_keys=True,separators=(',',':'),allow_nan=False)
        return delegation,canonical,hashlib.sha256(canonical.encode()).hexdigest()

    def permitted(self, agent, delegation, canonical):
        bound=json.loads(canonical)
        try:
            return self.policy.allow({'preview_only':True,'human_user_id':agent.human_user_id,
                'workload':agent.principal,'delegated_human_user_id':delegation.human_user_id,
                'delegated_workload':delegation.workload,'delegated_task':delegation.task,
                'allowed_records':[self.records[a] for a in sorted(delegation.records)],
                'allowed_fields':sorted(delegation.fields),'request':bound}) is True
        except Exception:return False

    def propose(self, agent_transport, request):
        with self.lock:
            agent=self.resolver.agent(agent_transport)
            delegation,canonical,digest=self.canonical(agent,request)
            if not self.permitted(agent,delegation,canonical):raise Denied('preview policy denied')
            self.pending={k:v for k,v in self.pending.items() if v.expires>self.clock()}
            if len(self.pending)>=1024:raise Denied('preview capacity')
            key=secrets.token_urlsafe(32)
            self.pending[key]=Pending(agent.principal,agent.human_user_id,delegation.id,
                                      canonical,digest,self.clock()+self.ttl)
            return {'proposal':key,'request_hash':digest,'expires_in':self.ttl,'status':'pending','executed':False}

    def checked(self, key):
        pending=self.pending.get(key)
        if pending is None or self.clock()>=pending.expires:
            raise Denied('unknown, expired or consumed preview')
        delegation=self.active(pending.delegation_id)
        return pending,delegation

    def human_preview(self, human_transport, key):
        with self.lock:
            human=self.resolver.human(human_transport)
            pending,_=self.checked(key)
            if human.salesforce_user_id!=pending.human_user_id or human.principal==pending.agent_principal:
                raise Denied('independent authorized human required')
            return {'request':json.loads(pending.canonical_request),'request_hash':pending.request_hash,
                    'status':pending.status,'executed':False}

    def approve(self, human_transport, key, displayed_hash):
        with self.lock:
            preview=self.human_preview(human_transport,key)
            pending,delegation=self.checked(key)
            if pending.status!='pending' or displayed_hash!=pending.request_hash:
                raise Denied('exact approval hash required')
            from .trusted_context import AgentContext
            agent=AgentContext(pending.agent_principal,pending.human_user_id,pending.delegation_id)
            if not self.permitted(agent,delegation,pending.canonical_request):raise Denied('policy changed')
            human=self.resolver.human(human_transport)
            if human.salesforce_user_id!=pending.human_user_id or human.principal==pending.agent_principal:
                raise Denied('independent authorized human required')
            pending.approved_by=human.principal
            pending.status='approved'
            return {'status':'approved','request_hash':preview['request_hash'],'executed':False}

    def redeem_preview(self, agent_transport, key, request):
        with self.lock:
            agent=self.resolver.agent(agent_transport)
            delegation,canonical,digest=self.canonical(agent,request)
            pending,_=self.checked(key)
            if pending.status!='approved' or not pending.approved_by or pending.approved_by==pending.agent_principal or pending.agent_principal!=agent.principal or pending.human_user_id!=agent.human_user_id or pending.delegation_id!=agent.delegation_id or pending.canonical_request!=canonical or pending.request_hash!=digest:
                raise Denied('approval binding mismatch')
            if not self.permitted(agent,delegation,canonical):raise Denied('policy changed')
            del self.pending[key]  # Atomic one-use consumption; NO write follows.
            return {'executed':False,'dry_run':True,'approved_change':json.loads(canonical),'request_hash':digest,'approved_by':pending.approved_by}
