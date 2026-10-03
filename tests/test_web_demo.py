"""UI security/lifecycle tests on new loopback sockets; injected runner only."""
import asyncio
import http.client
import json
import threading
import time
import unittest
from broker_lab.web_demo import DemoState,create_server

class WebDemoTests(unittest.TestCase):
    def setUp(self):
        self.entered=threading.Event();self.release=threading.Event()
        async def runner(value):
            self.entered.set()
            await asyncio.to_thread(self.release.wait,2)
            return {'authorized_reads':[],'decision':'deny','model_answer':'<script>untrusted text</script>'}
        self.state=DemoState(runner=runner)
        self.server=create_server(0,self.state)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,path='/',method='GET',body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        default={'Host':'127.0.0.1:'+str(self.port)}
        if headers:default.update(headers)
        conn.request(method,path,body,default)
        response=conn.getresponse();value=(response.status,dict(response.getheaders()),response.read())
        conn.close();return value
    def auth(self):return {'X-Lab-CSRF':self.state.nonce,'Origin':'http://127.0.0.1:'+str(self.port),'Content-Type':'application/json'}
    def test_root_security_headers_and_text_only_rendering(self):
        status,headers,body=self.request()
        self.assertEqual(status,200)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertIn(b'textContent',body);self.assertNotIn(b'innerHTML',body)
        self.assertNotIn(b'access_token',body)
    def test_cross_origin_bad_host_and_missing_nonce_denied(self):
        for headers in ({'Host':'attacker.invalid'},{'Origin':'https://attacker.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request(headers=headers)[0],403)
        self.assertEqual(self.request('/api/status')[0],403)
        self.assertEqual(self.request('/api/run','POST','{}',{'Content-Type':'application/json'})[0],403)
    def test_unknown_routes_and_invalid_request_shape(self):
        self.assertEqual(self.request('/api/other',headers=self.auth())[0],404)
        for body in ('{}','{"customer":"A","record":"A","attack":false,"command":"anything"}',
                '{"customer":"A","customer":"B","record":"A","attack":false}',
                '{"customer":"A","record":"A","attack":"yes"}'):
            self.assertEqual(self.request('/api/run','POST',body,self.auth())[0],400)
        self.assertEqual(self.state.snapshot()['status'],'idle')
    def test_running_repeated_click_conflict_and_completion(self):
        body=json.dumps({'customer':'A','record':'B','attack':True})
        self.assertEqual(self.request('/api/run','POST',body,self.auth())[0],202)
        self.assertTrue(self.entered.wait(1))
        self.assertEqual(self.state.snapshot()['status'],'running')
        self.assertEqual(self.request('/api/run','POST',body,self.auth())[0],409)
        self.release.set()
        for _ in range(100):
            if self.state.snapshot()['status']=='complete':break
            time.sleep(.01)
        self.assertEqual(self.state.snapshot()['status'],'complete')
        status,headers,value=self.request('/api/status',headers=self.auth())
        self.assertEqual(status,200);self.assertEqual(json.loads(value)['result']['decision'],'deny')
    def test_execution_error_suppresses_internal_details(self):
        async def fail(value):raise RuntimeError('injected sensitive detail')
        state=DemoState(runner=fail)
        self.assertTrue(state.start({'customer':'A','record':'A','attack':False}))
        for _ in range(100):
            if state.snapshot()['status']=='error':break
            time.sleep(.01)
        self.assertEqual(state.snapshot()['status'],'error')
        self.assertNotIn('sensitive detail',json.dumps(state.snapshot()))
    def test_live_mode_does_not_authenticate_selected_customer(self):
        state=DemoState(live_socket='/tmp/example-user-owned-broker.sock')
        self.assertEqual(state.snapshot()['mode'],'live_salesforce')
        self.assertIsNone(state.snapshot()['result'])

class PublicConfigTests(unittest.TestCase):
    def test_example_not_usable_as_live_config(self):
        from broker_lab.configuration import EXAMPLE_PATH,load_live_config
        from broker_lab.core import Denied
        with self.assertRaises(Denied):load_live_config(EXAMPLE_PATH)
    def test_invalid_config_or_credential_members_refused(self):
        from broker_lab.configuration import load_config
        from broker_lab.core import Denied
        from pathlib import Path
        import tempfile
        config=load_config();config['client_secret']='injected-rejected-value'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.json';path.write_text(json.dumps({'lab':config}))
            with self.assertRaises(Denied):load_config(path)
