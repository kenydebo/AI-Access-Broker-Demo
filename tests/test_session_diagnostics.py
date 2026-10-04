import http.client
import json
import re
import threading
import unittest
from broker_lab.hosted_demo import Sessions, create_server


class AdmissionDiagnosticsTests(unittest.TestCase):
    def test_reason_classes_and_busy_rate_recovery_without_limit_changes(self):
        now=[100];state=Sessions(clock=lambda:now[0]);key,session=state.page(None)
        def reason(token=key,csrf=None,**kwargs):return state.begin(token,csrf or session['csrf'],explain=True,**kwargs)[2]
        self.assertEqual(reason(token='unknown'),'session_unavailable')
        self.assertEqual(reason(csrf='stale'),'page_out_of_sync')
        self.assertEqual(reason(model=True,demo_required=True),'demo_not_started')
        session['demo_context']={'scope':'A','expires':99,'id':'test'}
        self.assertEqual(reason(model=True,demo_required=True),'demo_expired')
        session['busy']=True;self.assertEqual(reason(),'session_busy');session['busy']=False
        session['model_total']=4;self.assertEqual(reason(model=True),'visitor_budget_exhausted');session['model_total']=0
        session['model_runs']=[100]*3;self.assertEqual(reason(model=True),'model_rate_limited')
        now[0]+=61
        status,admitted,_=state.begin(key,session['csrf'],model=True,explain=True)
        self.assertEqual(status,200);state.finish(admitted)
        other,another=state.page(None);status,admitted=state.begin(other,another['csrf']);self.assertEqual(status,200)
        self.assertEqual(reason(),'demo_busy');state.finish(admitted)
        status,admitted=state.begin(key,session['csrf']);self.assertEqual(status,200);state.finish(admitted)
        session['runs']=[now[0]]*12;self.assertEqual(reason(),'visitor_rate_limited');session['runs']=[]
        state.runs=[now[0]]*60;self.assertEqual(reason(),'service_rate_limited')

    def test_shared_browser_cookie_rotation_and_stale_page_recovery_before_model(self):
        calls=[]
        server=create_server('127.0.0.1',0,'https://demo.onrender.com',model_runner=lambda *args:calls.append(args) or {'ok':True},public_demo=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,json.dumps(body) if body is not None else None,{'Host':'demo.onrender.com',**(headers or {})})
            response=conn.getresponse();result=response.status,dict(response.headers),response.read();conn.close();return result
        try:
            _,headers,body=request('GET','/')
            auth={'Cookie':headers['Set-Cookie'].split(';')[0],'X-Lab-CSRF':re.search(b'nonce="([A-Za-z0-9_-]+)"',body).group(1).decode(),'Origin':'https://demo.onrender.com','Content-Type':'application/json'}
            _,headers,body=request('POST','/api/demo/start',{'customer':'A'},auth)
            first={**auth,'Cookie':headers['Set-Cookie'].split(';')[0],'X-Lab-CSRF':json.loads(body)['csrf']}
            _,headers,body=request('POST','/api/demo/start',{'customer':'B'},first)
            rotated=headers['Set-Cookie'].split(';')[0]
            status,_,body=request('POST','/api/gemini',{'scenario':'read-a'},{**first,'Cookie':rotated})
            self.assertEqual(status,403);failure=json.loads(body);self.assertEqual(failure['reason_code'],'page_out_of_sync');self.assertEqual(failure['model_api_attempts'],0)
            self.assertEqual(calls,[])
            _,_,body=request('GET','/',headers={'Cookie':rotated})
            refreshed={**first,'Cookie':rotated,'X-Lab-CSRF':re.search(b'nonce="([A-Za-z0-9_-]+)"',body).group(1).decode()}
            self.assertEqual(request('POST','/api/gemini',{'scenario':'read-b'},refreshed)[0],200)
            self.assertEqual(len(calls),1)
        finally:server.shutdown();server.server_close();thread.join(2)
