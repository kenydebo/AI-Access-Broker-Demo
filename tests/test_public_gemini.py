import http.client
import json
import re
import threading
import unittest
from unittest.mock import patch
from broker_lab.core import Broker, Caller, Denied, MockSalesforce, OPAPolicy, find_opa
from broker_lab.gemini_agent import GeminiAgent, GeminiClient
from broker_lab.gemini_runtime import optional_model
from broker_lab.hosted_demo import Sessions, create_server
from test_gemini_agent import KEY, MockOpener, reply, answer


class ActivationTests(unittest.TestCase):
    def test_default_disabled_never_reads_key(self):
        class Environment(dict):
            def get(self,key,*args):
                if key=='GEMINI_API_KEY':raise AssertionError('Disabled configuration must not inspect key')
                return super().get(key,*args)
        self.assertIsNone(optional_model(Environment()))
        self.assertIsNone(optional_model(Environment({'BROKER_GEMINI_ENABLED':'0'})))
        with self.assertRaises(SystemExit):optional_model(Environment({'BROKER_GEMINI_ENABLED':'1'}))
        with self.assertRaises(SystemExit):optional_model(Environment({'BROKER_GEMINI_ENABLED':'yes'}))

    def test_enabled_configuration_constructs_only_no_api_request(self):
        with patch('broker_lab.gemini_runtime.GeminiClient') as client:
            client.return_value.model='gemini-3.5-flash-lite';client.return_value.attempts=0
            runner=optional_model({'BROKER_GEMINI_ENABLED':'1','BROKER_GEMINI_ACTIVATION_APPROVED':'synthetic-audience-reviewed','GEMINI_API_KEY':KEY})
            self.assertTrue(callable(runner));client.return_value.generate.assert_not_called()


@unittest.skipUnless(find_opa(),'Public Gemini actual OPA tests NOT RUN: OPA missing')
class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.now=[100]
        self.sessions=Sessions(clock=lambda:self.now[0])
        self.opener=MockOpener([])
        self.client=GeminiClient(KEY,opener=self.opener)
        self.agent=GeminiAgent(self.client,clock=lambda:self.now[0])
        self.origin='https://demo.onrender.com'
        self.server=create_server('127.0.0.1',0,self.origin,self.sessions,model_runner=self.agent.run_demo,public_demo=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        status,headers,html=self.request('GET','/')
        self.assertEqual(status,200)
        self.auth={'Cookie':headers['Set-Cookie'].split(';')[0],'Origin':self.origin,'Content-Type':'application/json',
                   'X-Lab-CSRF':re.search(b'nonce="([A-Za-z0-9_-]+)"',html).group(1).decode()}

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,method,path,value=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        connection.request(method,path,json.dumps(value) if value is not None else None,{'Host':'demo.onrender.com',**(headers or {})})
        response=connection.getresponse();result=(response.status,dict(response.headers),response.read());connection.close();return result

    def start(self,customer):
        status,headers,body=self.request('POST','/api/demo/start',{'customer':customer},self.auth)
        self.assertEqual(status,200)
        data=json.loads(body);self.assertEqual(data['scope'],customer)
        self.assertFalse(data['real_human_authentication'])
        self.auth={**self.auth,'Cookie':headers['Set-Cookie'].split(';')[0],'X-Lab-CSRF':data['csrf']}
        return data

    def model(self,scenario,record):
        self.opener.values.extend([reply(record),answer('untrusted')])
        status,_,body=self.request('POST','/api/gemini',{'scenario':scenario},self.auth)
        self.assertEqual(status,200)
        return json.loads(body)

    def test_four_ab_combinations_rotated_token_and_no_llm_token_disclosure(self):
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a'},self.auth)[0],409)
        initial=self.auth.copy();self.start('A');a=self.auth.copy()
        self.assertNotEqual(initial['Cookie'],a['Cookie'])
        for scenario,record,allowed in [('read-a','A',True),('read-b','B',False)]:
            result=self.model(scenario,record)
            self.assertEqual(result['broker_outcome'],'allow' if allowed else 'deny')
            self.assertEqual(result['downstream_reads'],int(allowed));self.assertEqual(result['policy_context']['allowed_record'],'A')
        self.start('B');b=self.auth.copy()
        self.now[0]+=61  # Four cases span the intentional 3/min per-browser limit.
        self.assertNotEqual(a['Cookie'],b['Cookie']);self.assertNotEqual(a['X-Lab-CSRF'],b['X-Lab-CSRF'])
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a'},a)[0],403)
        for scenario,record,allowed in [('read-a','A',False),('read-b','B',True)]:
            result=self.model(scenario,record)
            self.assertEqual(result['broker_outcome'],'allow' if allowed else 'deny')
            self.assertEqual(result['downstream_reads'],int(allowed));self.assertEqual(result['policy_context']['allowed_record'],'B')
        payloads=''.join(request.data.decode() for request,_ in self.opener.requests)
        for credentials in (initial,a,b):
            self.assertNotIn(credentials['Cookie'].split('=',1)[1],payloads)
            self.assertNotIn(credentials['X-Lab-CSRF'],payloads)
        for session in self.sessions.items.values():self.assertNotIn(session['demo_context']['id'],payloads)

    def test_tampered_unknown_expired_tokens_no_model_requests(self):
        self.start('A')
        unknown={**self.auth,'Cookie':'__Host-broker-demo='+'a'*43}
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a'},unknown)[0],403)
        self.now[0]+=300
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a'},self.auth)[0],409)
        self.now[0]+=600
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a'},self.auth)[0],403)
        self.assertEqual(self.opener.requests,[])

    def test_customer_claim_only_start_strict_scope_binding_afterwards(self):
        for value in ({'customer':'C'},{'customer':'A','scope':'B'},{'customer':'A','principal':'AgentB'}):
            self.assertEqual(self.request('POST','/api/demo/start',value,self.auth)[0],400)
        self.start('A')
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-a','customer':'B'},self.auth)[0],400)
        self.opener.values.append(reply('B'))
        status,_,body=self.request('POST','/api/gemini',{'scenario':'prompt-override'},self.auth)
        self.assertEqual(status,200);result=json.loads(body)
        self.assertEqual(result['model_outcome'],'host_request_binding_denied')
        self.assertEqual(result['downstream_reads'],0);self.assertEqual(result['policy_context']['allowed_record'],'A')

    def test_token_expiry_during_model_proposal_no_read_or_second_api_request(self):
        self.start('A');self.opener.values.append(reply('A'));original=self.opener.open
        def expire(request,timeout):
            self.now[0]+=301
            return original(request,timeout)
        self.opener.open=expire
        status,_,body=self.request('POST','/api/gemini',{'scenario':'read-a'},self.auth)
        self.assertEqual(status,200);result=json.loads(body)
        self.assertEqual(result['broker_outcome'],'deny');self.assertEqual(result['downstream_reads'],0)
        self.assertEqual(result['model_outcome'],'demo_token_expired_or_replaced')
        self.assertEqual(len(self.opener.requests),1)

    def test_switch_invalidates_old_grant_context_and_preserves_browser_budget(self):
        self.start('A');token=self.auth['Cookie'].split('=',1)[1];context=self.sessions.resolve_demo(token)
        downstream=MockSalesforce()
        # Same production resolver primitive in a broker context provider.
        broker=Broker(OPAPolicy(),downstream,{context['id']:('AgentA','A','opportunity-summary')},
                      context_provider=lambda:{'demo':self.sessions.resolve_demo(token)})
        details={'type':'salesforce_record','record':'A','action':'read','fields':['Id','Name'],'task':'opportunity-summary','audience':'salesforce-read'}
        grant=broker.issue(Caller(context['id']),details)['grant']
        old_session=self.sessions.items[token];old_session['model_total']=4
        self.start('B')
        with self.assertRaises(Denied):self.sessions.resolve_demo(token)
        with self.assertRaises(Denied):broker.redeem(Caller(context['id']),grant,details)
        self.assertEqual(downstream.calls,0)
        self.assertEqual(self.request('POST','/api/gemini',{'scenario':'read-b'},self.auth)[0],429)
        self.assertEqual(self.opener.requests,[])
