import copy
import http.client
import io
import json
from pathlib import Path
import re
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
from broker_lab.core import find_opa
from broker_lab.gemini_agent import Budget, CASES, ENDPOINT, GeminiAgent, GeminiClient, MODEL, ModelUnavailable, NoRedirect
from broker_lab.hosted_demo import Sessions, create_server

KEY = 'injected-placeholder-not-a-real-key'


def reply(record='A', fields=None, parts=None):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model',
        'parts': parts or [{'functionCall': {'name': 'read_record', 'args': {'record': record, 'fields': fields or ['Id', 'Name']}, 'id': 'test-call'}, 'thoughtSignature': 'test-signature'}]}}]}


def answer(text='Synthetic A was returned.'):
    return reply(parts=[{'text': text}])


class Response:
    status = 200
    def __init__(self, value, url=ENDPOINT):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.url = url
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self, limit):return self.data[:limit]
    def geturl(self):return self.url


class MockOpener:
    def __init__(self, values):self.values=list(values);self.requests=[]
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.values:raise AssertionError('Unexpected provider request')
        value=self.values.pop(0)
        if isinstance(value, Exception):raise value
        return value if isinstance(value, Response) else Response(value)


class TransportTests(unittest.TestCase):
    def test_fixed_tls_endpoint_header_and_limits(self):
        opener=MockOpener([answer()]);client=GeminiClient(KEY,opener=opener)
        client.generate({'contents':[]})
        request,timeout=opener.requests[0]
        self.assertEqual(request.full_url,ENDPOINT);self.assertEqual(timeout,12)
        self.assertNotIn(KEY,request.full_url);self.assertEqual(request.get_header('X-goog-api-key'),KEY)
        self.assertNotIn('http://',request.full_url)
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://example.invalid'))
        with self.assertRaises(ValueError):GeminiClient(KEY,model='models/evil')
        with self.assertRaises(ModelUnavailable):client.generate({'text':'x'*70000})
        self.assertEqual(client.attempts,1)
        import ssl
        actual=GeminiClient(KEY)  # Constructs handlers only; makes no request.
        tls=[handler for handler in actual.opener.handlers if isinstance(handler, __import__('urllib.request',fromlist=['HTTPSHandler']).HTTPSHandler)][0]
        self.assertTrue(tls._context.check_hostname)
        self.assertEqual(tls._context.verify_mode,ssl.CERT_REQUIRED)

    def test_provider_timeout_quota_redirect_and_invalid_body_safe(self):
        quota=urllib.error.HTTPError(ENDPOINT,429,'SECRET PROMPT',{},io.BytesIO(b'PRIVATE KEY BODY'))
        cases=[(TimeoutError(KEY),'provider_unavailable'),(quota,'provider_quota'),
               (Response(answer(),url='https://example.invalid'),'provider_unavailable'),
               (Response(b'bad json'),'provider_unavailable'),(Response(b'{"a":1,"a":2}'),'provider_unavailable'),
               (Response(b'x'*65537),'response_limit'),(Response([]),'invalid_response')]
        for value,reason in cases:
            client=GeminiClient(KEY,opener=MockOpener([value]))
            with self.assertRaises(ModelUnavailable) as caught:client.generate({'contents':[]})
            self.assertEqual(str(caught.exception),reason)
            self.assertNotIn(KEY,str(caught.exception))

    def test_budget_reserves_two_attempt_runs_no_retry_and_window(self):
        now=[10];budget=Budget(clock=lambda:now[0],daily=5)
        for _ in range(4):self.assertTrue(budget.reserve())
        self.assertFalse(budget.reserve());now[0]+=61
        self.assertTrue(budget.reserve());self.assertFalse(budget.reserve())
        now[0]+=86400;self.assertTrue(budget.reserve())


@unittest.skipUnless(find_opa(),'Actual Gemini broker OPA tests NOT RUN: OPA missing')
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.now=[100]
        self.visitor={'id':'injected-synthetic-visitor-identity','expires':1000}

    def run_model(self, values, case='allow-a', **kwargs):
        opener=MockOpener(values);client=GeminiClient(KEY,opener=opener)
        agent=GeminiAgent(client,clock=lambda:self.now[0],**kwargs)
        result=agent.run(case,self.visitor)
        self.assertNotIn(KEY,json.dumps(result));self.assertNotIn('"grant":',json.dumps(result))
        self.assertNotIn(self.visitor['id'],json.dumps(result))
        return result,opener

    def test_manual_real_opa_happy_path_and_signature_roundtrip(self):
        result,opener=self.run_model([reply(),answer('Made-up extra fields are not access evidence.')])
        self.assertEqual(result['broker_outcome'],'allow');self.assertEqual(result['downstream_reads'],1)
        self.assertEqual(result['authorized_data'],{'Id':'mock-A','Name':'Synthetic A'})
        self.assertEqual(result['model_api_attempts'],2)
        self.assertEqual(len([e for e in result['events'] if e['stage']=='opa']),2)
        import hashlib
        policy_hash=hashlib.sha256((Path(__file__).resolve().parent.parent/'policy'/'broker.rego').read_bytes()).hexdigest()
        self.assertEqual([e['policy_source_sha256_observed_before_eval'] for e in result['events'] if e['stage']=='opa'],[policy_hash,policy_hash])
        first=json.loads(opener.requests[0][0].data);second=json.loads(opener.requests[1][0].data)
        self.assertEqual(first['tools'][0]['functionDeclarations'][0]['name'],'read_record')
        self.assertNotIn('googleSearch',json.dumps(first));self.assertEqual(second['contents'][1],reply()['candidates'][0]['content'])
        self.assertEqual(second['toolConfig']['functionCallingConfig']['mode'],'NONE')
        function=second['contents'][2]['parts'][0]['functionResponse']
        self.assertEqual(function['id'],'test-call');self.assertEqual(function['response']['data'],result['authorized_data'])
        self.assertNotIn(self.visitor['id'],json.dumps(first))

    def test_four_customer_combinations(self):
        for case,record,allowed in [('allow-a','A',True),('allow-b','B',True),('cross-a','B',False),('cross-b','A',False)]:
            with self.subTest(case=case):
                result,opener=self.run_model([reply(record),answer('untrusted answer')],case)
                self.assertEqual(result['broker_outcome'],'allow' if allowed else 'deny')
                self.assertEqual(result['downstream_reads'],int(allowed))
                self.assertEqual(result['authorized_data'] is not None,allowed)
                if not allowed:
                    body=json.loads(opener.requests[1][0].data)
                    self.assertNotIn('mock-',json.dumps(body));self.assertEqual(next(event for event in result['events'] if event['stage']=='opa')['reasons'],['target_outside_task_scope'])

    def test_prompt_scope_override_never_changes_identity_or_request(self):
        result,opener=self.run_model([reply('A')],'prompt-override')
        self.assertEqual(result['model_outcome'],'host_request_binding_denied')
        self.assertEqual(result['model_proposal_summary'][0]['record'],'A')
        self.assertEqual(result['broker_outcome'],'not_evaluated');self.assertEqual(result['downstream_reads'],0)
        self.assertEqual(len(opener.requests),1)
        # If the model resists the attempted override, the original B/B request may succeed.
        result,_=self.run_model([reply('B'),answer('Scope B only')],'prompt-override')
        self.assertEqual(result['authorized_data']['Id'],'mock-B')

    def test_parallel_calls_invalid_tool_and_field_action_escalation(self):
        bad=reply();base=bad['candidates'][0]['content']['parts'][0]
        values=[reply(parts=[copy.deepcopy(base),copy.deepcopy(base)]),reply(fields=['Id','Secret__c']),reply(fields=['Id','Id'])]
        for field,value in [('name','arbitrary_http'),('args',{'record':'A','fields':['Id','Name'],'agent':'AgentA'}),
                            ('args',{'record':'A','fields':['Id','Name'],'action':'update'}),('args',{'record':'A','fields':['Id','Name'],'task':'admin'})]:
            item=copy.deepcopy(bad);item['candidates'][0]['content']['parts'][0]['functionCall'][field]=value;values.append(item)
        for item in values:
            result,opener=self.run_model([item]);self.assertEqual(result['downstream_reads'],0)
            self.assertEqual(result['broker_outcome'],'not_evaluated');self.assertEqual(len(opener.requests),1)

    def test_refusal_text_safety_and_malformed_no_access(self):
        values=[answer('I refuse.'),{'promptFeedback':{'blockReason':'SAFETY'}},
                {'candidates':[{'finishReason':'SAFETY'}]}, {'candidates':[]}, {'candidates':[{},{}]},
                {'promptFeedback':None}, {'candidates':[{'finishReason':'MAX_TOKENS'}]},
                reply(parts=[{'text':'x'*4097}]), reply(parts=[{'executableCode':{'code':'bad'}}])]
        for item in values:
            result,_=self.run_model([item]);self.assertEqual(result['downstream_reads'],0)
            self.assertIsNone(result['authorized_data']);self.assertEqual(result['broker_outcome'],'not_evaluated')
        result,_=self.run_model([answer('I refuse.')]);self.assertEqual(result['model_outcome'],'no_tool_call')
        self.assertIsNone(result['model_answer_untrusted'])
        result,_=self.run_model([{'promptFeedback':{'blockReason':'SAFETY'}}]);self.assertEqual(result['model_outcome'],'model_blocked')

    def test_second_model_failure_preserves_actual_read_evidence(self):
        result,opener=self.run_model([reply(),TimeoutError('private')])
        self.assertEqual(result['model_outcome'],'provider_unavailable');self.assertEqual(result['downstream_reads'],1)
        self.assertEqual(result['broker_outcome'],'allow');self.assertIsNotNone(result['authorized_data'])
        self.assertIsNone(result['model_answer_untrusted']);self.assertEqual(len(opener.requests),2)
        result,_=self.run_model([reply(),reply()]);self.assertEqual(result['model_outcome'],'extra_tool_call_rejected')
        self.assertEqual(result['downstream_reads'],1)

    def test_visitor_expires_during_model_request_policy_rechecks(self):
        opener=MockOpener([reply(),answer('Denied')]);original=opener.open
        def expire(request,timeout):
            self.now[0]=1001
            return original(request,timeout)
        opener.open=expire
        agent=GeminiAgent(GeminiClient(KEY,opener=opener),clock=lambda:self.now[0])
        result=agent.run('allow-a',self.visitor)
        self.assertEqual(result['broker_outcome'],'deny');self.assertEqual(result['downstream_reads'],0)

    def test_local_budget_and_invalid_visitor_never_request(self):
        result,opener=self.run_model([],budget=Budget(daily=0));self.assertEqual(result['model_outcome'],'local_budget_exhausted')
        self.assertEqual(opener.requests,[])
        agent=GeminiAgent(GeminiClient(KEY,opener=MockOpener([])),clock=lambda:self.now[0])
        for visitor in ({}, {'id':'AgentA','expires':1000}, {**self.visitor,'expires':99}, {**self.visitor,'agent':'AgentA'}):
            with self.assertRaises(ValueError):agent.run('allow-a',visitor)


class ModelHTTPTests(unittest.TestCase):
    def test_public_entrypoint_disabled_and_loopback_ui_is_opt_in(self):
        with self.assertRaises(ValueError):create_server('0.0.0.0',0,'https://demo.onrender.com',model_runner=lambda *args:{})
        with self.assertRaises(ValueError):create_server('127.0.0.1',0,'https://demo.onrender.com',model_runner=lambda *args:{})
        calls=[]
        server=create_server('127.0.0.1',0,'http://127.0.0.1:0',secure=False,model_runner=lambda name,visitor:calls.append((name,visitor)) or {'ok':True})
        origin='http://127.0.0.1:0'  # Factory test uses exact origin as supplied; network port is ephemeral.
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,body,{'Host':'127.0.0.1:0',**(headers or {})})
            r=conn.getresponse();result=(r.status,dict(r.headers),r.read());conn.close();return result
        try:
            _,headers,html=request('GET','/')
            self.assertIn(b'Gemini agent demo',html);self.assertNotIn(b'__GEMINI_CONTROLS__',html)
            csrf=re.search(b'nonce="([A-Za-z0-9_-]+)"',html).group(1).decode()
            auth={'Cookie':headers['Set-Cookie'].split(';')[0],'Origin':origin,'X-Lab-CSRF':csrf,'Content-Type':'application/json'}
            self.assertEqual(request('POST','/api/gemini','{"scenario":"allow-a","prompt":"scope override"}',auth)[0],400)
            for _ in range(3):self.assertEqual(request('POST','/api/gemini','{"scenario":"allow-a"}',auth)[0],200)
            self.assertEqual(request('POST','/api/gemini','{"scenario":"allow-a"}',auth)[0],429)
            for session in server.sessions.items.values():session['model_runs']=[]
            self.assertEqual(request('POST','/api/gemini','{"scenario":"allow-a"}',auth)[0],200)
            self.assertEqual(request('POST','/api/gemini','{"scenario":"allow-a"}',auth)[0],429)
            self.assertEqual(len(calls),4);self.assertEqual(set(calls[0][1]),{'id','expires'})
            self.assertNotIn(calls[0][1]['id'].encode(),html)
        finally:server.shutdown();server.server_close();thread.join(2)

    @unittest.skipUnless(find_opa(),'Actual HTTP model OPA test NOT RUN: OPA missing')
    def test_http_mocked_model_manual_tool_real_broker_opa_roundtrip(self):
        opener=MockOpener([reply('B'),answer('Access was denied; no data')])
        agent=GeminiAgent(GeminiClient(KEY,opener=opener))
        server=create_server('127.0.0.1',0,'http://127.0.0.1:0',secure=False,model_runner=agent.run)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,body,{'Host':'127.0.0.1:0',**(headers or {})})
            response=conn.getresponse();result=(response.status,dict(response.headers),response.read());conn.close();return result
        try:
            _,headers,html=request('GET','/')
            csrf=re.search(b'nonce="([A-Za-z0-9_-]+)"',html).group(1).decode()
            status,_,body=request('POST','/api/gemini','{"scenario":"cross-a"}',
                {'Cookie':headers['Set-Cookie'].split(';')[0],'Origin':'http://127.0.0.1:0','X-Lab-CSRF':csrf,'Content-Type':'application/json'})
            self.assertEqual(status,200)
            data=json.loads(body);self.assertEqual(data['broker_outcome'],'deny')
            self.assertEqual(data['downstream_reads'],0);self.assertIsNone(data['authorized_data'])
            self.assertEqual(len(opener.requests),2)
            self.assertFalse(next(event for event in data['events'] if event['stage']=='opa')['allow'])
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_public_mode_does_not_render_controls_or_accept_model_route(self):
        server=create_server('127.0.0.1',0,'https://demo.onrender.com')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            conn.request('GET','/',headers={'Host':'demo.onrender.com'})
            r=conn.getresponse();body=r.read();self.assertNotIn(b'gemini-run',body);self.assertNotIn(b'__GEMINI_CONTROLS__',body)
            conn.close();conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/api/gemini','{}',headers={'Host':'demo.onrender.com','Origin':'https://demo.onrender.com','Content-Type':'application/json'})
            r=conn.getresponse();self.assertEqual(r.status,404);r.read()
        finally:conn.close();server.shutdown();server.server_close();thread.join(2)

    def test_user_run_cli_rejects_public_render_before_key_read(self):
        from broker_lab.gemini_demo import main
        with patch('sys.argv',['gemini_demo','--approved-private-test']),patch.dict('os.environ',{'RENDER':'true'},clear=True):
            with self.assertRaisesRegex(SystemExit,'Public Render Gemini activation is disabled'):main()
