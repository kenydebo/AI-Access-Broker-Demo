import base64
import hashlib
import hmac
import json
import unittest
from urllib.parse import parse_qs,urlsplit
from broker_lab.core import Denied, normalize
from broker_lab.user_oauth import UserOAuth, SessionSalesforce, CALLBACK
from broker_lab.salesforce import HOST,ORG

SUBJECT='005000000000001AAA'
SECRET='injected-offline-secret'

class Reply:
    status=200
    def __init__(self,request,value): self.request,self.value=request,value
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def geturl(self): return self.request.full_url
    def read(self,size): return json.dumps(self.value).encode()

class FakeOpener:
    def __init__(self, token_changes=None,identity_changes=None):
        self.calls=[]
        self.token_changes=token_changes or {}
        self.identity_changes=identity_changes or {}
    def open(self,request,timeout):
        self.calls.append(request)
        if request.get_method()=='POST':
            value={'instance_url':'https://'+HOST,'id':'https://login.salesforce.com/id/'+ORG+'/'+SUBJECT,
                'access_token':'opaque-injected-test-token','token_type':'Bearer','issued_at':'1791050000000'}
            value.update(self.token_changes)
            value['signature']=base64.b64encode(hmac.new(SECRET.encode(),(value['id']+value['issued_at']).encode(),hashlib.sha256).digest()).decode()
            if 'signature' in self.token_changes: value['signature']=self.token_changes['signature']
        else:
            value={'organization_id':ORG,'user_id':SUBJECT,'active':True}
            value.update(self.identity_changes)
        return Reply(request,value)

class UserOAuthTests(unittest.TestCase):
    def make(self,**kwargs):
        self.now=100
        flow=UserOAuth('offline-client',SECRET,clock=lambda:self.now)
        flow._opener=FakeOpener(**kwargs)
        query=parse_qs(urlsplit(flow.begin()).query)
        target='/OauthRedirect?state='+query['state'][0]+'&code=injected-code'
        return flow,query,target
    def test_pkce_confidential_exchange_and_identity_endpoint(self):
        flow,query,target=self.make()
        session=flow.callback(target,'localhost:1717','127.0.0.1')
        self.assertEqual(session.assignment.salesforce_user_id,SUBJECT)
        self.assertEqual(session.assignment.intended_records,frozenset({'A'}))
        self.assertNotIn(session.token,repr(session))
        self.assertEqual(query['scope'],['api id'])
        self.assertEqual(query['redirect_uri'],[CALLBACK])
        self.assertEqual(query['code_challenge_method'],['S256'])
        post,identity=flow._opener.calls
        form=parse_qs(post.data.decode())
        challenge=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge,query['code_challenge'][0])
        self.assertEqual(form['client_secret'],[SECRET])
        self.assertEqual(identity.full_url,'https://'+HOST+'/id/'+ORG+'/'+SUBJECT)
        self.assertEqual(identity.get_header('Authorization'),'Bearer opaque-injected-test-token')
        self.assertEqual(flow._secret,'')
    def test_spoofed_state_no_exchange(self):
        flow,query,target=self.make()
        with self.assertRaises(Denied): flow.callback(target.replace(query['state'][0],'forged'),'localhost:1717','127.0.0.1')
        self.assertEqual(flow._opener.calls,[])
    def test_host_peer_path_duplicate_rejected(self):
        for change in ('host','peer','path','duplicate','absolute'):
            with self.subTest(change=change):
                flow,query,target=self.make()
                host,peer='localhost:1717','127.0.0.1'
                if change=='host': host='attacker.invalid:1717'
                if change=='peer': peer='192.0.2.1'
                if change=='path': target=target.replace('/OauthRedirect','/OauthRedirect/extra')
                if change=='duplicate': target+='&state='+query['state'][0]
                if change=='absolute': target='http://localhost:1717'+target
                with self.assertRaises(Denied): flow.callback(target,host,peer)
                self.assertEqual(flow._opener.calls,[])
    def test_expired_and_replayed_state(self):
        flow,query,target=self.make()
        self.now=400
        with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
        self.assertEqual(flow._opener.calls,[])
        flow,query,target=self.make()
        flow.callback(target,'localhost:1717','127.0.0.1')
        with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
        self.assertEqual(len(flow._opener.calls),2)
    def test_cancel_consumes_state(self):
        flow,query,target=self.make()
        cancel='/OauthRedirect?state='+query['state'][0]+'&error=access_denied'
        with self.assertRaises(Denied): flow.callback(cancel,'localhost:1717','127.0.0.1')
        with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
        self.assertEqual(flow._opener.calls,[])
    def test_token_instance_subject_org_and_signature_denials(self):
        for changes in ({'instance_url':'https://attacker.invalid'},
                {'id':'https://login.salesforce.com/id/'+ORG+'/005000000000003AAA'},
                {'id':'https://attacker.invalid/id/'+ORG+'/'+SUBJECT},
                {'id':'https://login.salesforce.com/id/00D000000000000AAA/'+SUBJECT},
                {'signature':'forged'},{'token_type':'Basic'},{'access_token':'token\nheader'}):
            with self.subTest(changes=changes):
                flow,query,target=self.make(token_changes=changes)
                with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
                self.assertEqual(len(flow._opener.calls),1)
                self.assertEqual(flow._secret,'')
    def test_authenticated_identity_must_match_and_be_active(self):
        for changes in ({'user_id':'005000000000002AAA'},{'organization_id':'00D000000000000AAA'}, {'active':False}):
            flow,query,target=self.make(identity_changes=changes)
            with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
            self.assertEqual(len(flow._opener.calls),2)
    def test_no_session_on_exchange_failure_and_no_reuse(self):
        flow,query,target=self.make()
        def fail(*args,**kwargs): raise RuntimeError('must not expose test token')
        flow._opener.open=fail
        with self.assertRaisesRegex(Denied,'^Salesforce user authentication failed$'):
            flow.callback(target,'localhost:1717','127.0.0.1')
        with self.assertRaises(Denied): flow.callback(target,'localhost:1717','127.0.0.1')
    def test_expired_session_never_calls_downstream(self):
        flow,query,target=self.make()
        session=flow.callback(target,'localhost:1717','127.0.0.1')
        adapter=SessionSalesforce(session,{'A':'006000000000001AAA','B':'006000000000002AAA'},clock=lambda:self.now)
        def fail(*args): self.fail('expired session reached Salesforce')
        adapter.adapter.read=fail
        self.now=session.expires_at
        with self.assertRaises(Denied): adapter.read(normalize(dict(type='salesforce_record',record='A',action='read',fields=['Id'],task='opportunity-summary',audience='salesforce-read')))
        adapter.close()
        self.assertEqual(adapter.adapter._token,'')
