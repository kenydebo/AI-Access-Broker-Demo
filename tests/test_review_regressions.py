"""Independent-review regressions: mocked provider, actual OPA, offline DOM harness."""
import copy
import http.client
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unittest
from broker_lab.core import find_opa
from broker_lab.hosted_demo import create_server
import test_gemini_evidence as evidence_fixtures
from test_gemini_agent import reply, answer


@unittest.skipUnless(find_opa(), 'Actual OPA review regression tests NOT RUN: OPA missing')
class ReviewRegressions(unittest.TestCase):
    def test_thought_call_rejected_without_execution_or_disclosure(self):
        fixture=evidence_fixtures.GeminiEvidenceTests();response=reply('A');response['candidates'][0]['content']['parts'][0]['thought']=True
        result,opener=fixture.run_demo([response], 'read-a')
        self.assertEqual(result['model_outcome'],'thought_tool_call_rejected')
        self.assertEqual(result['host_outcome'],'rejected');self.assertEqual(result['downstream_reads'],0)
        self.assertIsNone(result['broker_request']);self.assertEqual(result['model_response']['tool_calls'],[])
        self.assertNotIn('test-signature',json.dumps(result));self.assertEqual(len(opener.requests),1)

    def test_invalid_thought_markers_cannot_project_text_or_tools(self):
        fixture=evidence_fixtures.GeminiEvidenceTests()
        for marker in (None,0,'',[],{},1):
            for part in ({'text':'PRIVATE MALFORMED TEXT','thought':marker},
                         {'functionCall':{'name':'read_record','args':{'record':'A','fields':['Id','Name']}},'thought':marker}):
                with self.subTest(marker=marker,part=part):
                    result,_=fixture.run_demo([reply(parts=[part])], 'read-a')
                    self.assertEqual(result['model_outcome'],'invalid_response')
                    self.assertEqual(result['downstream_reads'],0);self.assertEqual(result['model_response']['tool_calls'],[])
                    self.assertNotIn('PRIVATE MALFORMED TEXT',json.dumps(result))
                    self.assertIsNone(result['model_response']['non_thought_text_untrusted'])

    def test_scripted_after_rotations_and_true_stale_page_rejected(self):
        server=create_server('127.0.0.1',0,'https://demo.onrender.com',model_runner=lambda *args: {},public_demo=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,value=None,auth=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,json.dumps(value) if value is not None else None,{'Host':'demo.onrender.com',**(auth or {})})
            r=conn.getresponse();result=(r.status,dict(r.headers),r.read());conn.close();return result
        try:
            _,headers,body=request('GET','/')
            auth={'Cookie':headers['Set-Cookie'].split(';')[0],'X-Lab-CSRF':re.search(b'nonce="([A-Za-z0-9_-]+)"',body).group(1).decode(),'Origin':'https://demo.onrender.com','Content-Type':'application/json'}
            self.assertEqual(request('POST','/api/run',{'scenario':'allow-a'},auth)[0],200)
            for customer in ('A','B'):
                stale=dict(auth)
                status,headers,body=request('POST','/api/demo/start',{'customer':customer},auth)
                self.assertEqual(status,200);data=json.loads(body)
                auth={**auth,'Cookie':headers['Set-Cookie'].split(';')[0],'X-Lab-CSRF':data['csrf']}
                self.assertEqual(request('POST','/api/run',{'scenario':'allow-a'},auth)[0],200)
                status,_,body=request('POST','/api/run',{'scenario':'allow-a'},{**stale,'Cookie':auth['Cookie']})
                self.assertEqual(status,403);self.assertEqual(json.loads(body)['reason_code'],'page_out_of_sync')
        finally:server.shutdown();server.server_close();thread.join(2)

    @unittest.skipUnless(shutil.which('node') or Path('/opt/homebrew/bin/node').exists(),'Node unavailable: UI regressions NOT RUN')
    def test_ui_pending_and_lost_response_after_actual_offline_read(self):
        result,_=evidence_fixtures.GeminiEvidenceTests().run_demo([reply('A'),answer('Visible model explanation, untrusted.')], 'read-a')
        self.assertEqual(result['downstream_reads'],1)
        root=Path(__file__).resolve().parent.parent
        checked=subprocess.run([shutil.which('node') or '/opt/homebrew/bin/node',str(root/'tests/ui_review_harness.js'),str(root)],input=json.dumps(result),text=True,capture_output=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
