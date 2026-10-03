"""Broker-owned authorization-code + S256 PKCE. No network/bind at import.
Access tokens are opaque. Identity is verified by authenticated Salesforce response,
not JWT decoding, a selected user label, or a caller-supplied user ID.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
import ssl
import threading
import time
import urllib.parse
import urllib.request
from .core import Denied
from .human_registry import HumanRegistry
from .salesforce import HOST, ORG, USER, NoRedirect, SalesforceReadOnly, same_id

CALLBACK = 'http://localhost:1717/OauthRedirect'

@dataclass(frozen=True)
class HumanSession:
    assignment: object
    expires_at: float
    token: str = field(repr=False)
    session_id: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)

class UserOAuth:
    def __init__(self, client_id, client_secret, registry=None, clock=time.monotonic):
        if not all(isinstance(v,str) and v and len(v)<=4096 and not any(ord(c)<33 or ord(c)>126 for c in v)
                   for v in (client_id,client_secret)):
            raise Denied('private client credentials required')
        self.client_id, self._secret = client_id,client_secret
        self.registry, self.clock = registry or HumanRegistry(),clock
        self.host,self.org=self.registry.host,self.registry.expected_org_id
        self._pending = None
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect())

    def begin(self):
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._pending = (state,verifier,self.clock()+300)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return 'https://'+self.host+'/services/oauth2/authorize?'+urllib.parse.urlencode({
            'response_type':'code','client_id':self.client_id,'redirect_uri':CALLBACK,
            'scope':'api id','state':state,'code_challenge':challenge,
            'code_challenge_method':'S256','prompt':'login'})

    def callback(self, target, host, peer):
        # Pure callback validation before any token request. Never trust Forwarded headers.
        if host != 'localhost:1717' or peer != '127.0.0.1' or not isinstance(target,str) or len(target)>8192:
            raise Denied('invalid local callback')
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.path != '/OauthRedirect' or parsed.fragment:
            raise Denied('invalid callback target')
        try:
            pairs = urllib.parse.parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True,max_num_fields=8)
        except ValueError:
            raise Denied('invalid callback parameters') from None
        values = dict(pairs)
        if len(values)!=len(pairs) or set(values)-{'state','code','error','error_description'}:
            raise Denied('invalid callback parameters')
        state = values.get('state','')
        with self._lock:
            pending = self._pending
            if not pending or not isinstance(state,str) or not hmac.compare_digest(state,pending[0]):
                raise Denied('unknown callback state')
            self._pending = None  # one use, including cancellation/failed exchange
        if self.clock()>=pending[2] or 'error' in values or not values.get('code'):
            raise Denied('login cancelled or expired')
        return self._exchange(values['code'],pending[1])

    def _json(self, request):
        try:
            with self._opener.open(request,timeout=10) as response:
                if response.status!=200 or response.geturl()!=request.full_url:
                    raise Denied('unexpected OAuth response')
                raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:
                    raise Denied('OAuth response too large')
                def unique(pairs):
                    result={}
                    for k,v in pairs:
                        if k in result: raise ValueError('duplicate JSON member')
                        result[k]=v
                    return result
                value=json.loads(raw,object_pairs_hook=unique)
                if not isinstance(value,dict): raise ValueError('invalid JSON object')
                return value
        except Exception:
            raise Denied('Salesforce user authentication failed') from None

    def _exchange(self, code, verifier):
        form=urllib.parse.urlencode({'grant_type':'authorization_code','client_id':self.client_id,
            'client_secret':self._secret,'redirect_uri':CALLBACK,'code':code,'code_verifier':verifier}).encode()
        request=urllib.request.Request('https://'+self.host+'/services/oauth2/token',data=form,
            headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},method='POST')
        try:
            result=self._json(request)
            instance=urllib.parse.urlsplit(result.get('instance_url',''))
            identity=urllib.parse.urlsplit(result.get('id',''))
            match=re.fullmatch(r'/id/([A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)/([A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)',identity.path)
            if instance.scheme!='https' or instance.netloc!=self.host or instance.path not in ('','/') or instance.query or instance.fragment:
                raise Denied('OAuth instance mismatch')
            if identity.scheme!='https' or identity.netloc not in (self.host,'login.salesforce.com') or identity.query or identity.fragment or not match or not same_id(match[1],self.org):
                raise Denied('OAuth org mismatch')
            subjects=[s for s in self.registry.assignments if same_id(match[2],s)]
            if len(subjects)!=1: raise Denied('OAuth subject not registered')
            subject=subjects[0]
            token=result.get('access_token')
            if not isinstance(token,str) or not token or len(token)>16384 or any(ord(c)<33 or ord(c)>126 for c in token) or str(result.get('token_type','')).lower()!='bearer':
                raise Denied('invalid OAuth token')
            issued=result.get('issued_at')
            if not isinstance(issued,str) or not re.fullmatch(r'[0-9]{10,16}',issued):
                raise Denied('invalid OAuth issuance')
            expected=base64.b64encode(hmac.new(self._secret.encode(),(result['id']+issued).encode(),hashlib.sha256).digest()).decode()
            if not isinstance(result.get('signature'),str) or not hmac.compare_digest(expected,result['signature']):
                raise Denied('invalid OAuth response signature')
            # Fixed org endpoint: never follow the returned identity URL with a bearer.
            verified=self._json(urllib.request.Request('https://'+self.host+'/id/'+self.org+'/'+subject,
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'}))
            if not same_id(verified.get('organization_id'),self.org) or not same_id(verified.get('user_id'),subject) or verified.get('active') is not True:
                raise Denied('authenticated identity mismatch or inactive user')
            assignment=self.registry.for_verified_subject(self.org,subject)
            return HumanSession(assignment,self.clock()+900,token)
        except Exception:
            raise Denied('Salesforce user authentication failed') from None
        finally:
            self._secret=''
            del request,form

    def close(self):
        with self._lock: self._pending=None
        self._secret=''

class SessionSalesforce:
    def __init__(self, session, records, clock=time.monotonic, configuration=None):
        self.session,self.clock=session,clock
        self.adapter=SalesforceReadOnly(session.token,records,'v67.0',configuration)
        self.adapter._validated=True  # constructed exclusively after verified OAuth identity

    def read(self, request):
        if self.clock()>=self.session.expires_at:
            raise Denied('human session expired')
        return self.adapter.read(request)

    def close(self):
        self.adapter._token=''
        self.session=None


class HumanBoundRuntime:
    """Verified human authorizes; separately verified integration runtime executes.
    Construct only inside the broker. No caller-supplied token/context route exists.
    """
    def __init__(self, session, adapter, clock=time.monotonic):
        self.session,self.adapter,self.clock=session,adapter,clock
        self.configuration=adapter.configuration
        self.context()  # Reject incomplete identities before starting any server.

    def context(self):
        session=self.session
        if not isinstance(session,HumanSession) or self.clock()>=session.expires_at:
            raise Denied('human session unavailable or expired')
        assignment=HumanRegistry(configuration=self.configuration).for_verified_subject(
            session.assignment.expected_org_id,session.assignment.salesforce_user_id)
        if assignment!=session.assignment or assignment.intended_records not in (frozenset({'A'}),frozenset({'B'})) or not session.session_id:
            raise Denied('human task binding mismatch')
        if not isinstance(self.adapter,SalesforceReadOnly) or self.adapter._validated is not True or self.adapter._identity!={'org_id':self.configuration['expected_org_id'],'user_id':self.configuration['runtime_user_id']}:
            raise Denied('runtime identity not verified')
        alias=next(iter(assignment.intended_records))
        return {'human_session_required':True,'runtime_executor_required':True,
            'human':{'org_id':self.configuration['expected_org_id'],'user_id':assignment.salesforce_user_id,
                'session_id':session.session_id,'allowed_record':alias,
                'allowed_task':'opportunity-summary','active':True},
            'executor':{'org_id':self.configuration['expected_org_id'],'user_id':self.configuration['runtime_user_id'],'validated':True}}

    def read(self, request):
        context=self.context()
        if request.record!=context['human']['allowed_record'] or request.task!=context['human']['allowed_task']:
            raise Denied('human task mismatch')
        return self.adapter.read(request)

    def close(self):
        self.adapter._token=''
        self.adapter._validated=False
        self.adapter._identity=None
        self.session=None
