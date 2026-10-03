"""USER-RUN after ECA/listener/OAuth approval. No listener or requests at import."""
import argparse
import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
import time
import warnings
from .configuration import load_live_config,LOCAL_PATH
from .human_registry import HumanRegistry
from .core import Broker, Denied, OPAPolicy, find_opa
from .transport import serve, check_socket_target, SocketUnavailable
from .user_oauth import UserOAuth, SessionSalesforce, HumanBoundRuntime


def login(flow):
    result=[]
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(3)
            super().setup()
        def log_message(self, *args): pass  # never log authorization code/state/query
        def do_GET(self):
            try:
                if len(self.headers.get_all('Host',[]))!=1:
                    raise Denied('invalid callback host')
                session=flow.callback(self.path,self.headers.get('Host'),self.client_address[0])
                result.append(session)
                status,body=200,b'Login verified. Close this tab and return to the terminal.'
            except Denied:
                status,body=400,b'Login was not accepted. Return to the terminal.'
            self.send_response(status)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
    try:
        # Bind before showing login URL; never fall back to another host/port.
        with HTTPServer(('127.0.0.1',1717),Callback) as listener:
            listener.timeout=1
            url=flow.begin()
            print('Open this URL privately and sign in as CustomerA or CustomerB:\n'+url,flush=True)
            deadline=time.monotonic()+300
            while not result and flow._pending is not None and time.monotonic()<deadline:
                listener.handle_request()
        if not result: raise Denied('login not completed')
        return result[0]
    finally:
        flow.close()


def main():
    parser=argparse.ArgumentParser(description='User-controlled Salesforce human OAuth → broker read session')
    parser.add_argument('--socket',default='.run/broker.sock')
    parser.add_argument('--config',default=str(LOCAL_PATH),help='ignored non-secret server configuration')
    parser.add_argument('--executor',choices=['human','runtime'],default='human',help='explicit runtime selects intended split identity; default human preserves initial validation semantics')
    parser.add_argument('--approved-setup',action='store_true',help='operator acknowledgement of approved ECA, local listener, OAuth exchange/identity calls and read-only synthetic fixture access; not authentication')
    args=parser.parse_args()
    if not args.approved_setup: parser.error('approve exact setup in HUMAN_LOGIN_SETUP.md before running')
    check_socket_target(args.socket)  # fail before prompts/login when path is occupied
    if not sys.stdin.isatty(): parser.error('private credential entry requires an interactive terminal')
    if find_opa() is None: parser.error('actual OPA required')
    configuration=load_live_config(args.config)
    records=configuration['records']
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        client_id=getpass.getpass('Human-login ECA consumer key (hidden): ')
        secret=getpass.getpass('Human-login ECA consumer secret (hidden): ')
    flow=UserOAuth(client_id,secret,registry=HumanRegistry(configuration=configuration))
    del client_id,secret
    session=login(flow)
    allowed=session.assignment.intended_records
    if allowed not in (frozenset({'A'}),frozenset({'B'})): raise Denied('ambiguous human task mapping')
    alias=next(iter(allowed))
    context_provider=None
    if args.executor=='runtime':
        from .salesforce import SalesforceReadOnly
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            runtime_id=getpass.getpass('RUNTIME app consumer key (hidden; separate from Human Login app): ')
            runtime_secret=getpass.getpass('RUNTIME app consumer secret (hidden; broker memory only): ')
        try:
            runtime=SalesforceReadOnly.from_client_credentials(runtime_id,runtime_secret,records,configuration['api_version'],configuration=configuration)
        finally:
            del runtime_id,runtime_secret
        downstream=HumanBoundRuntime(session,runtime)
        context_provider=downstream.context
        print('Executor: verified integration runtime. Human session authorizes only.',flush=True)
    else:
        downstream=SessionSalesforce(session,records,configuration=configuration)
        print('Executor: HUMAN token (initial validation mode; not split identity). Use --executor runtime for the intended architecture.',flush=True)
    del session,flow
    # Authenticated human determines trusted task scope; callers cannot choose it.
    mapping={'uid:'+str(os.getuid()):('Agent'+alias,alias,'opportunity-summary')}
    print('Verified Salesforce human; trusted lab task scope '+alias+'. Session expires in 15 minutes. Ctrl-C logs out locally.',flush=True)
    try: serve(args.socket,Broker(OPAPolicy(data_path=args.config),downstream,mapping,60,context_provider=context_provider))
    finally: downstream.close()

if __name__=='__main__':
    try: main()
    except SocketUnavailable as error: raise SystemExit(str(error))
    except Denied: raise SystemExit('Login/access denied; no credentials or downstream data printed.')
    except OSError: raise SystemExit('Local listener or transport unavailable; no fallback listener started.')
    except KeyboardInterrupt: pass
