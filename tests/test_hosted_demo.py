import http.client
import json
from pathlib import Path
import re
import threading
import unittest
from broker_lab.core import OPAPolicy, find_opa
from broker_lab.hosted_demo import COOKIE, SCENARIOS, Sessions, create_server, run_scenario


@unittest.skipUnless(find_opa(), 'Actual hosted OPA tests NOT RUN: OPA missing')
class HostedActualOPATests(unittest.TestCase):
    def test_all_fixed_scenarios(self):
        expected = {'allow-a': ('allow', 1), 'allow-b': ('allow', 1), 'cross-a': ('deny', 0),
                    'cross-b': ('deny', 0), 'expired': ('deny', 0),
                    'replay': ('replay_denied_after_one_read', 1)}
        for name, pair in expected.items():
            with self.subTest(name=name):
                result = run_scenario(name)
                self.assertEqual((result['decision'], result['downstream_reads']), pair)
                self.assertGreaterEqual(len([e for e in result['events'] if e['stage']=='opa']), 1)
                serialized = json.dumps(result)
                self.assertNotIn('session_id', serialized)
                self.assertNotIn('"grant":', serialized)
                self.assertIn('not authentication', result['identity'])

    def test_real_opa_scenarios_through_http(self):
        origin='https://integration.onrender.com'
        with create_server('127.0.0.1',0,origin) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,body=None,headers=None):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                conn.request(method,path,body,{'Host':'integration.onrender.com',**(headers or {})})
                response=conn.getresponse();result=(response.status,dict(response.getheaders()),response.read());conn.close();return result
            try:
                status,headers,body=request('GET','/')
                self.assertEqual(status,200)
                cookie=headers['Set-Cookie'].split(';')[0]
                csrf=re.search(b'nonce="([A-Za-z0-9_-]+)"',body).group(1).decode()
                for name in SCENARIOS:
                    status,_,body=request('POST','/api/run',json.dumps({'scenario':name}),
                        {'Cookie':cookie,'Origin':origin,'X-Lab-CSRF':csrf,'Content-Type':'application/json'})
                    self.assertEqual(status,200)
                    result=json.loads(body)
                    self.assertEqual(result['downstream_reads'],int(name in ('allow-a','allow-b','replay')))
                    self.assertEqual(result['scenario'],name)
            finally:
                server.shutdown();thread.join(2)

    def test_outage_never_reads(self):
        result = run_scenario('allow-a', OPAPolicy(executable='/not-installed/opa'))
        self.assertEqual(result['downstream_reads'], 0)
        self.assertEqual(result['decision'], 'deny')
        self.assertEqual(result['events'][0]['reasons'], ['opa_unavailable_or_invalid'])

    def test_timeout_is_bounded_and_local_default_preserved(self):
        self.assertEqual(OPAPolicy().timeout, 3)
        self.assertEqual(OPAPolicy(timeout=10).timeout, 10)
        for value in (0, 11, float('inf'), float('nan')):
            with self.assertRaises(ValueError):OPAPolicy(timeout=value)

    def test_unknown_scenario_rejected(self):
        for value in ('live', 'allow-a; curl example.com', 'AgentA'):
            with self.assertRaises(ValueError):run_scenario(value)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.sessions = Sessions(clock=lambda:self.now)

    def test_separate_csrf_and_absolute_expiry_cleanup(self):
        a, first = self.sessions.page(None)
        b, second = self.sessions.page(a+'tampered')
        self.assertNotEqual(a, b)
        self.assertNotEqual(first['csrf'], second['csrf'])
        self.assertEqual(self.sessions.begin(a, second['csrf'])[0], 403)
        self.now += 899
        self.assertEqual(self.sessions.page(a)[0], a)
        self.now += 1
        self.sessions.cleanup()
        self.assertNotIn(a, self.sessions.items)
        self.assertEqual(self.sessions.begin(a, first['csrf'])[0], 403)

    def test_run_and_concurrency_limits_recover(self):
        handles = [self.sessions.page(None) for _ in range(3)]
        key, session = handles[0]
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],200)
        key, session = handles[0]
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],429)
        key, session = handles[2]
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],503)
        self.sessions.finish(handles[0][1])
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],200)
        self.sessions.finish(session)
        for _ in range(11):
            self.assertEqual(self.sessions.begin(key,session['csrf'])[0],200)
            self.sessions.finish(session)
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],429)
        self.now += 61
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],200)
        self.sessions.finish(session)

    def test_global_run_budget(self):
        handles=[self.sessions.page(None) for _ in range(6)]
        for key, session in handles:
            for _ in range(10):
                self.assertEqual(self.sessions.begin(key,session['csrf'])[0],200)
                self.sessions.finish(session)
        key, session=handles[0]
        self.assertEqual(self.sessions.begin(key,session['csrf'])[0],429)

    def test_creation_capacity_and_rate(self):
        for _ in range(20):self.assertIsNotNone(self.sessions.page(None)[0])
        self.assertIsNone(self.sessions.page(None)[0])
        self.now+=61
        self.sessions.capacity=20
        self.assertIsNone(self.sessions.page(None)[0])
        self.now+=900
        self.assertIsNotNone(self.sessions.page(None)[0])
        self.assertEqual(len(self.sessions.items),1)


class HostedHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin='https://test-demo.onrender.com'
        cls.calls=[]
        def runner(name):
            cls.calls.append(name)
            if name=='expired':raise RuntimeError('PRIVATE ERROR MUST NOT APPEAR')
            return {'scenario':name,'synthetic':True}
        cls.server=create_server('127.0.0.1',0,cls.origin,runner=runner)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2)

    def setUp(self):
        self.calls.clear()
        self.server.sessions.items.clear();self.server.sessions.created.clear();self.server.sessions.runs.clear()

    def request(self, method, path='/', body=None, headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request(method,path,body,{'Host':'test-demo.onrender.com',**(headers or {})})
        response=connection.getresponse();data=response.read();result=(response.status,dict(response.getheaders()),data)
        connection.close();return result

    def visitor(self):
        status,headers,body=self.request('GET')
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        token=re.search(b'nonce="([A-Za-z0-9_-]+)"',body).group(1).decode()
        return {'Cookie':cookie,'X-Lab-CSRF':token,'Origin':self.origin,'Content-Type':'application/json'}

    def test_two_visitor_isolation_and_security_headers(self):
        first=self.visitor();second=self.visitor()
        self.assertNotEqual(first['Cookie'],second['Cookie'])
        mixed={**first,'X-Lab-CSRF':second['X-Lab-CSRF']}
        self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',mixed)[0],403)
        self.assertEqual(self.calls,[])
        status,headers,_=self.request('GET')
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('Secure',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie']);self.assertTrue(headers['Set-Cookie'].startswith(COOKIE+'='))
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'],'no-store')

    def test_strict_fixed_requests(self):
        headers=self.visitor()
        for value in ('{"scenario":"live"}', '{"scenario":"allow-a","agent":"AgentA"}',
                      '{"scenario":"allow-a","scenario":"allow-b"}', '{"scenario":[]}', '[]', 'null', 'x'*129):
            self.assertEqual(self.request('POST','/api/run',value,headers)[0],400)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',headers)[0],200)
        self.assertEqual(self.calls,['allow-a'])

    def test_host_origin_csrf_and_cookie_gates(self):
        headers=self.visitor()
        attacks=[{**headers,'Host':'evil.example'}, {**headers,'Origin':'https://evil.example'},
                 {key:value for key,value in headers.items() if key!='Origin'},
                 {**headers,'X-Lab-CSRF':'wrong'}, {**headers,'Sec-Fetch-Site':'cross-site'},
                 {**headers,'Cookie':headers['Cookie']+'; '+headers['Cookie']}]
        for attack in attacks:
            self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',attack)[0],403)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.request('GET',headers={'Host':'evil.example'})[0],403)

    def test_no_status_or_arbitrary_routes_and_generic_errors(self):
        headers=self.visitor()
        self.assertEqual(self.request('GET','/api/status',headers=headers)[0],404)
        self.assertEqual(self.request('POST','/api/upload','{}',headers)[0],404)
        status,_,body=self.request('POST','/api/run','{"scenario":"expired"}',headers)
        self.assertEqual(status,503);self.assertNotIn(b'PRIVATE',body)
        self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',headers)[0],200)

    def test_expired_cookie_cannot_execute(self):
        headers=self.visitor()
        for session in self.server.sessions.items.values():session['expires']=0
        self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',headers)[0],403)
        self.assertEqual(self.calls,[])

    def test_health_without_creating_session(self):
        self.assertEqual(self.request('GET','/health')[0],200)
        self.assertEqual(len(self.server.sessions.items),0)

    def test_body_type_and_transfer_encoding_rejected(self):
        headers=self.visitor()
        for extra in ({'Content-Type':'text/plain'},{'Transfer-Encoding':'chunked'}):
            self.assertEqual(self.request('POST','/api/run','{"scenario":"allow-a"}',{**headers,**extra})[0],400)
        self.assertEqual(self.calls,[])


class ContainerSourceTests(unittest.TestCase):
    def test_runtime_context_excludes_live_modules_and_dependencies(self):
        root=Path(__file__).resolve().parent.parent
        docker=(root/'Dockerfile').read_text();ignore=(root/'.dockerignore').read_text()
        self.assertIn('USER 10001:10001',docker)
        self.assertEqual(docker.count('@sha256:'),2)
        self.assertNotIn('pip install',docker)
        self.assertNotIn('COPY . ',docker)
        self.assertNotIn('!broker_lab/salesforce.py',ignore)
        self.assertNotIn('!.local',ignore)
        self.assertNotIn('!.git',ignore)
        self.assertNotIn('!release',ignore)
        self.assertIn('**\n',ignore)
        for folder in ('broker_lab','policy','examples','web'):
            self.assertIn('!'+folder+'/\n'+folder+'/**\n',ignore)
