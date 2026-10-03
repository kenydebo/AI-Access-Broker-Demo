"""Synthetic onboarding over REAL Ollama, SDK MCP, broker and OPA; no Salesforce.
Customer selection injects test context. It is never user authentication.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
from .configuration import load_config
from .core import Broker,MockSalesforce,OPAPolicy
from .transport import handle
from .ollama_host import run

class EvidencePolicy:
    def __init__(self): self.opa,self.events=OPAPolicy(),[]
    def allow(self,attributes):
        decision=self.opa.decide(attributes)
        display=json.loads(json.dumps(attributes))
        display['human']['session_id']='[opaque synthetic session; not displayed]'
        self.events.append({'stage':'opa_evaluation','evaluation':len(self.events)+1,
            'allow':decision['allow'],'reasons':decision['reasons'],'attributes':display})
        return decision['allow']

async def synthetic(customer,record,model='llama3.2:3b',attack=False):
    if customer not in ('A','B') or record not in ('A','B') or type(attack) is not bool:
        raise ValueError('invalid scenario')
    config=load_config()
    subject=config['users']['Customer'+customer]['salesforce_user_id']
    context={'human_session_required':True,'runtime_executor_required':True,
        'human':{'org_id':config['expected_org_id'],'user_id':subject,
            'session_id':secrets.token_urlsafe(32),'active':True,'allowed_record':customer,'allowed_task':'opportunity-summary'},
        'executor':{'org_id':config['expected_org_id'],'user_id':config['runtime_user_id'],'validated':True}}
    policy,downstream=EvidencePolicy(),MockSalesforce()
    broker=Broker(policy,downstream,{'uid:'+str(os.getuid()):('Agent'+customer,customer,'opportunity-summary')},context_provider=lambda:context)
    stop=threading.Event()
    with tempfile.TemporaryDirectory(prefix='ai-broker-demo-',dir='/tmp') as folder:
        path=str(Path(folder)/'broker.sock')
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        listener.bind(path);os.chmod(path,0o600);listener.listen(8);listener.settimeout(.1)
        def accept():
            while not stop.is_set():
                try: connection,_=listener.accept()
                except socket.timeout: continue
                with connection:handle(connection,broker)
        thread=threading.Thread(target=accept,daemon=True);thread.start()
        try: output=await run(model,record,path,attack=attack)
        finally:
            stop.set();thread.join(timeout=6);listener.close()
    output.update({'mode':'synthetic','identity_source':'injected lab context; selection is NOT authentication',
        'customer':customer,'model':model,'attack_prompt_sent':attack,'opa_evidence':policy.events,
        'downstream_reads':downstream.calls,'unconsumed_grants':len(broker.grants),
        'decision':'allow' if output['authorized_reads'] else ('deny' if output['denials'] else 'no_authorized_tool_call')})
    return output

async def live(record,socket_path,model='llama3.2:3b',attack=False):
    output=await run(model,record,socket_path,attack=attack)
    output.update({'mode':'live_salesforce','identity_source':'existing authenticated split broker; this UI does not authenticate',
        'customer':None,'model':model,'attack_prompt_sent':attack,'opa_evidence':[],
        'decision':'allow' if output['authorized_reads'] else ('deny' if output['denials'] else 'no_authorized_tool_call'),
        'evidence_limit':'OPA reasons/context are not exported by live MCP; a generic deny also covers expiry or unavailability.'})
    return output

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--customer',choices=['A','B'],default='A')
    parser.add_argument('--record',choices=['A','B'],default='A')
    parser.add_argument('--model',default='llama3.2:3b')
    parser.add_argument('--attack',action='store_true')
    args=parser.parse_args()
    result=asyncio.run(synthetic(args.customer,args.record,args.model,args.attack))
    print(json.dumps(result,indent=2))
    if not result['authorized_reads']:raise SystemExit(1)

if __name__=='__main__':main()
