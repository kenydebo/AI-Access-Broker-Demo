"""Loopback-only local demo. No credentials, live login or arbitrary commands."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from .demo_runner import synthetic,live

class DemoState:
    def __init__(self, model='llama3.2:3b', live_socket=None, runner=None):
        self.model,self.live_socket=model,live_socket
        self.nonce=secrets.token_urlsafe(32)
        self.lock=threading.Lock()
        self.state={'status':'idle','mode':'live_salesforce' if live_socket else 'synthetic','result':None,'error':None}
        self.runner=runner
    def snapshot(self):
        with self.lock:return dict(self.state)
    def start(self,value):
        if not isinstance(value,dict) or set(value)!={'customer','record','attack'} or value['customer'] not in ('A','B') or value['record'] not in ('A','B') or type(value['attack']) is not bool:
            raise ValueError('Only the bounded A/B read scenario is accepted.')
        with self.lock:
            if self.state['status']=='running':return False
            self.state.update(status='running',result=None,error=None)
        def work():
            try:
                if self.runner: result=asyncio.run(self.runner(value))
                elif self.live_socket:result=asyncio.run(live(value['record'],self.live_socket,self.model,value['attack']))
                else:result=asyncio.run(synthetic(value['customer'],value['record'],self.model,value['attack']))
                with self.lock:self.state.update(status='complete',result=result)
            except Exception:
                with self.lock:self.state.update(status='error',error='Execution failed closed. Check installed Ollama/model and the selected broker; no authorized data returned.')
        threading.Thread(target=work,daemon=True).start()
        return True

class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    def handle_error(self,*args):pass  # no request payload/credential traceback logs

def create_server(port,state):
    expected_host='127.0.0.1:'+str(port)
    origin='http://'+expected_host
    class Handler(BaseHTTPRequestHandler):
        def setup(self):self.request.settimeout(3);super().setup()
        def log_message(self,*args):pass
        def reply(self,status,body,kind='application/json'):
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'nonce-"+state.nonce+"'; style-src 'nonce-"+state.nonce+"'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(body)
        def gate(self,api=False,post=False):
            if self.headers.get_all('Host',[])!=[expected_host]:return False
            provided=self.headers.get('Origin')
            if (post and provided!=origin) or (provided is not None and provided!=origin):return False
            if api and self.headers.get('X-Lab-CSRF')!=state.nonce:return False
            if self.headers.get('Sec-Fetch-Site')=='cross-site':return False
            return True
        def do_GET(self):
            if not self.gate(api=self.path.startswith('/api/')):self.reply(403,b'{"error":"local same-origin request required"}');return
            if self.path=='/':
                body=(Path(__file__).resolve().parent.parent/'web'/'index.html').read_text().replace('__NONCE__',state.nonce)
                self.reply(200,body.encode(),'text/html; charset=utf-8')
            elif self.path=='/api/status':self.reply(200,json.dumps(state.snapshot()).encode())
            else:self.reply(404,b'{"error":"not found"}')
        def do_POST(self):
            if not self.gate(api=True,post=True):self.reply(403,b'{"error":"local same-origin request required"}');return
            if self.path!='/api/run':self.reply(404,b'{"error":"not found"}');return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<1 or size>4096 or self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):
                    raise ValueError('invalid body')
                def unique(pairs):
                    result={}
                    for key,value in pairs:
                        if key in result:raise ValueError('duplicate key')
                        result[key]=value
                    return result
                value=json.loads(self.rfile.read(size),object_pairs_hook=unique)
                started=state.start(value)
                self.reply(202 if started else 409,json.dumps({'started':started,'error':None if started else 'One run is already in progress.'}).encode())
            except (ValueError,TypeError,KeyError):self.reply(400,b'{"error":"invalid bounded scenario"}')
    server=LocalServer(('127.0.0.1',port),Handler)
    expected_host='127.0.0.1:'+str(server.server_address[1])
    origin='http://'+expected_host
    return server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--model',default='llama3.2:3b')
    parser.add_argument('--live-broker-socket',help='USER-RUN only: connect to a privately authenticated split broker; no synthetic identity selector')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('unprivileged local port required')
    state=DemoState(args.model,args.live_broker_socket)
    with create_server(args.port,state) as server:
        print('Local '+state.state['mode']+' demo: http://127.0.0.1:'+str(args.port)+'. Ctrl-C stops this UI.',flush=True)
        server.serve_forever()

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except OSError:raise SystemExit('Local UI port unavailable; no fallback or external listener started.')
