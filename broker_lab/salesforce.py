"""Read-only adapter. Constructed only inside broker; never in MCP boundary."""
import json
import re
import ssl
import urllib.parse
import urllib.request
from .core import Denied, FIELDS

HOST = 'example.my.salesforce.com'
ORG = '00D000000000001AAA'
USER = '005000000000003AAA'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Denied('Salesforce redirect refused')


def same_id(value, expected):
    return isinstance(value,str) and value in (expected,expected[:15])

def validated_oauth_token(value, configuration=None):
    from .configuration import load_config
    configuration=configuration or load_config()
    host,org,user=(configuration[k] for k in ('salesforce_host','expected_org_id','runtime_user_id'))
    if not isinstance(value,dict):
        raise Denied('invalid authentication response')
    instance = urllib.parse.urlsplit(value.get('instance_url',''))
    if instance.scheme != 'https' or instance.netloc != host or instance.path not in ('','/') or instance.query or instance.fragment:
        raise Denied('Salesforce instance mismatch')
    identity = urllib.parse.urlsplit(value.get('id',''))
    match = re.fullmatch(r'/id/([A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)/([A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?)',identity.path)
    if identity.scheme != 'https' or identity.netloc not in (host,'login.salesforce.com') or identity.query or identity.fragment or not match or not same_id(match[1],org) or not same_id(match[2],user):
        raise Denied('Salesforce OAuth identity mismatch')
    token = value.get('access_token')
    if not isinstance(token,str) or not token or any(ord(c)<33 or ord(c)>126 for c in token) or str(value.get('token_type','')).lower() != 'bearer':
        raise Denied('invalid bearer authentication response')
    return token

class SalesforceReadOnly:
    def __init__(self, token, records, api_version, configuration=None):
        from .configuration import load_config
        self.configuration=configuration or load_config()
        self.host,self.org,self.user=(self.configuration[k] for k in ('salesforce_host','expected_org_id','runtime_user_id'))
        if not isinstance(token,str) or not token or any(c.isspace() for c in token):
            raise ValueError('invalid token')
        if set(records) != {'A','B'} or len(set(records.values())) != 2 or any(not re.fullmatch(r'006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?', r) for r in records.values()):
            raise ValueError('two distinct Opportunity IDs required')
        if not re.fullmatch(r'v[0-9]{2,3}\.0',api_version):
            raise ValueError('explicit supported API version required')
        self._token, self._records, self._version = token,dict(records),api_version
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
        self._validated = False
        self._identity = None

    @classmethod
    def from_client_credentials(cls, client_id, client_secret, records, api_version='v67.0', configuration=None):
        if not isinstance(client_id,str) or not client_id or not isinstance(client_secret,str) or not client_secret:
            raise Denied('missing client credentials')
        # A temporary token-free adapter validates record/version config and builds
        # the same strict HTTPS/no-proxy/no-redirect opener; no credential persists.
        temporary = cls('offline-placeholder-not-sent',records,api_version,configuration)
        form = urllib.parse.urlencode({'grant_type':'client_credentials',
                'client_id':client_id,'client_secret':client_secret}).encode()
        del client_id,client_secret
        request = urllib.request.Request('https://'+temporary.host+'/services/oauth2/token',data=form,
            headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},method='POST')
        try:
            with temporary._opener.open(request,timeout=10) as response:
                if response.status != 200 or response.geturl() != request.full_url:
                    raise Denied('unexpected authentication response')
                raw = response.read(1024*1024+1)
                if len(raw)>1024*1024:
                    raise Denied('authentication response too large')
                oauth = json.loads(raw)
            token = validated_oauth_token(oauth,temporary.configuration)
        except Exception:
            raise Denied('Salesforce authentication failed') from None
        finally:
            del request,form
        adapter = cls(token,records,api_version,temporary.configuration)
        adapter._validated = True  # Verified OAuth id and instance from pinned TLS endpoint.
        adapter._identity = {'org_id':adapter.org,'user_id':adapter.user}
        del token,oauth,raw,temporary
        return adapter

    def _get(self, path):
        url = 'https://' + self.host + path
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != self.host or parsed.fragment:
            raise Denied('invalid Salesforce domain')
        req = urllib.request.Request(url, headers={'Authorization':'Bearer '+self._token,'Accept':'application/json'})
        try:
            with self._opener.open(req,timeout=10) as response:
                if response.status != 200 or response.geturl() != url:
                    raise Denied('unexpected Salesforce response')
                raw = response.read(1024*1024+1)
                if len(raw)>1024*1024:
                    raise Denied('Salesforce response too large')
                return json.loads(raw)
        except Exception:
            raise Denied('Salesforce read failed') from None

    def validate_identity(self):
        self._validated = False
        identity = self._get('/services/oauth2/userinfo')
        if not isinstance(identity,dict) or not same_id(identity.get('organization_id'),self.org) or not same_id(identity.get('user_id'),self.user):
            raise Denied('Salesforce identity mismatch')
        self._validated = True
        self._identity = {'org_id':self.org,'user_id':self.user}

    def read(self, request):
        if not self._validated or request.action != 'read' or request.record not in self._records or not set(request.fields) <= FIELDS:
            raise Denied('Salesforce read not authorized')
        fields = ','.join(sorted(set(request.fields) | {'Id'}))
        path = '/services/data/'+self._version+'/sobjects/Opportunity/'+self._records[request.record]+'?'+urllib.parse.urlencode({'fields':fields})
        record = self._get(path)
        if not isinstance(record,dict) or record.get('Id') != self._records[request.record]:
            raise Denied('unexpected Salesforce record')
        return {f:record.get(f) for f in request.fields}
