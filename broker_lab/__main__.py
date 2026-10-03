import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import warnings
from .configuration import load_live_config,LOCAL_PATH
from .core import Broker, Caller, Denied, FixturePolicy, MockSalesforce, OPAPolicy, TEST_IDENTITIES, find_opa, normalize
from .boundary import BrokerBoundary
from .transport import serve

def details(record='A'):
    return dict(type='salesforce_record',record=record,action='read',fields=['Id','Name'],
                task='opportunity-summary',audience='salesforce-read')

def main():
    parser = argparse.ArgumentParser(description='Local broker lab; MCP adapter: python -m broker_lab.mcp_server')
    parser.add_argument('mode',choices=['demo','serve','read'])
    parser.add_argument('--socket',default='.run/broker.sock')
    parser.add_argument('--fixture-policy',action='store_true',help='explicit offline fixture; NOT OPA')
    parser.add_argument('--ttl',type=float,default=60)
    parser.add_argument('--record',choices=['A','B'],default='A')
    parser.add_argument('--salesforce',action='store_true',help='USER-RUN ONLY: hidden client ID/secret prompts; broker authenticates in memory')
    parser.add_argument('--record-a')
    parser.add_argument('--record-b')
    parser.add_argument('--records-config',help='optional server-owned A/B ID override file')
    parser.add_argument('--config',default=str(LOCAL_PATH),help='ignored live configuration')
    parser.add_argument('--check-runtime-access',action='store_true',help='USER-RUN live preflight: read only Id/Name from both configured synthetic records')
    parser.add_argument('--api-version',default='v67.0',help='Salesforce API version; v67.0 confirmed by user prior probe')
    args = parser.parse_args()
    if args.check_runtime_access and (not args.salesforce or args.mode!='serve'):
        parser.error('--check-runtime-access requires serve --salesforce')
    if args.salesforce and args.mode != 'serve':
        parser.error('--salesforce is available only inside broker serve mode')
    if args.mode=='demo':
        print('OFFLINE FIXTURE DEMO: injected test identities; no OPA, MCP or live Salesforce.')
        mock = MockSalesforce()
        broker = Broker(FixturePolicy(),mock,TEST_IDENTITIES,args.ttl)
        for subject in TEST_IDENTITIES:
            for record in ('A','B'):
                try:
                    request = details(record)
                    grant = broker.issue(Caller(subject),request)['grant']
                    value = broker.redeem(Caller(subject),grant,request)
                    print(subject,record,'ALLOW',json.dumps(value))
                except Denied:
                    print(subject,record,'DENY')
        print('downstream reads:',mock.calls)
    elif args.mode=='serve':
        policy = FixturePolicy() if args.fixture_policy else OPAPolicy(data_path=args.config if args.salesforce else None)
        if args.fixture_policy:
            print('OFFLINE FIXTURE POLICY: NOT OPA. Mock records unless --salesforce explicitly supplied.',flush=True)
        downstream = MockSalesforce()
        if args.salesforce:
            if args.fixture_policy:
                parser.error('live adapter requires actual OPA; fixture policy is offline only')
            configuration=load_live_config(args.config)
            if not args.record_a or not args.record_b:
                try:
                    configured=json.loads(Path(args.records_config).read_text()) if args.records_config else configuration['records']
                    if not isinstance(configured,dict) or set(configured)!={'A','B'}:
                        raise ValueError('invalid record config')
                    args.record_a=args.record_a or configured['A']
                    args.record_b=args.record_b or configured['B']
                except (OSError,ValueError,KeyError):
                    parser.error('valid server-owned A/B record config required')
            if find_opa() is None:
                parser.error('actual OPA required for live adapter')
            from .salesforce import SalesforceReadOnly
            if not sys.stdin.isatty():
                parser.error('hidden credential entry requires an interactive terminal')
            with warnings.catch_warnings():
                warnings.simplefilter('error',getpass.GetPassWarning)
                client_id = getpass.getpass('Consumer key / client ID (hidden; broker memory only): ')
                client_secret = getpass.getpass('Consumer secret (hidden; broker memory only): ')
            downstream = SalesforceReadOnly.from_client_credentials(client_id,client_secret,{'A':args.record_a,'B':args.record_b},args.api_version,configuration=configuration)
            del client_id,client_secret
            if args.check_runtime_access:
                for alias in ('A','B'):
                    downstream.read(normalize(details(alias)))
                print('Runtime read preflight passed for A and B (Id/Name only).',flush=True)
        # Server-owned mapping; all processes under this UID act as AgentA.
        mapping = {'uid:'+str(os.getuid()):('AgentA','A','opportunity-summary')}
        serve(args.socket,Broker(policy,downstream,mapping,args.ttl))
    else:
        boundary = BrokerBoundary(args.socket)
        request = details(args.record)
        grant = boundary.authorize_read(request)['grant']
        print(json.dumps(boundary.redeem_read(grant,request)))

if __name__=='__main__':
    try:
        main()
    except Denied:
        raise SystemExit('Denied; no downstream data returned.')
    except KeyboardInterrupt:
        pass
