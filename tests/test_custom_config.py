"""Fictitious second-org configuration; no local/private config or real HTTP."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from broker_lab.configuration import load_config
from broker_lab.core import OPAPolicy
from broker_lab.__main__ import details
from broker_lab.human_registry import HumanRegistry
from broker_lab.salesforce import validated_oauth_token,SalesforceReadOnly
from broker_lab.user_oauth import UserOAuth,HumanSession,HumanBoundRuntime

class CustomConfigTests(unittest.TestCase):
    def config(self):
        cfg=load_config();cfg.update(mode='local',salesforce_host='other-example.my.salesforce.com',expected_org_id='00D000000000009AAA',runtime_user_id='005000000000009AAA')
        cfg['users']['CustomerA']['salesforce_user_id']='005000000000007AAA'
        cfg['users']['CustomerB']['salesforce_user_id']='005000000000008AAA'
        return cfg
    def test_runtime_exchange_pins_custom_org_host_user(self):
        cfg=self.config();oauth={'instance_url':'https://'+cfg['salesforce_host'],'id':'https://login.salesforce.com/id/'+cfg['expected_org_id']+'/'+cfg['runtime_user_id'],'token_type':'Bearer','access_token':'offline-custom-runtime'}
        self.assertEqual(validated_oauth_token(oauth,cfg),'offline-custom-runtime')
        reply=MagicMock();reply.status=200;reply.geturl.return_value='https://'+cfg['salesforce_host']+'/services/oauth2/token';reply.read.return_value=json.dumps(oauth).encode();reply.__enter__.return_value=reply
        opener=MagicMock();opener.open.return_value=reply
        with patch('broker_lab.salesforce.urllib.request.build_opener',return_value=opener):
            adapter=SalesforceReadOnly.from_client_credentials('offline-key','offline-secret',cfg['records'],cfg['api_version'],configuration=cfg)
        self.assertEqual(adapter._identity,{'org_id':cfg['expected_org_id'],'user_id':cfg['runtime_user_id']})
        self.assertEqual(opener.open.call_args.args[0].full_url,reply.geturl.return_value)
    def test_human_exchange_uses_custom_identity_service(self):
        cfg=self.config();subject=cfg['users']['CustomerA']['salesforce_user_id'];flow=UserOAuth('offline-key','offline-secret',HumanRegistry(configuration=cfg),clock=lambda:100)
        oauth={'instance_url':'https://'+cfg['salesforce_host'],'id':'https://login.salesforce.com/id/'+cfg['expected_org_id']+'/'+subject,'token_type':'Bearer','access_token':'offline-custom-human','issued_at':'1791050000000'}
        oauth['signature']=base64.b64encode(hmac.new(b'offline-secret',(oauth['id']+oauth['issued_at']).encode(),hashlib.sha256).digest()).decode()
        with patch.object(flow,'_json',side_effect=[oauth,{'organization_id':cfg['expected_org_id'],'user_id':subject,'active':True}]) as calls:
            session=flow._exchange('offline-code','offline-verifier')
        self.assertEqual(session.assignment.salesforce_user_id,subject)
        self.assertEqual(calls.call_args_list[1].args[0].full_url,'https://'+cfg['salesforce_host']+'/id/'+cfg['expected_org_id']+'/'+subject)
    def test_pdp_authority_loaded_from_config_not_source_ids(self):
        cfg=self.config();registry=HumanRegistry(configuration=cfg)
        session=HumanSession(registry.for_verified_subject(cfg['expected_org_id'],cfg['users']['CustomerB']['salesforce_user_id']),1000,'offline-token')
        runtime=SalesforceReadOnly('offline-runtime',cfg['records'],cfg['api_version'],cfg);runtime._validated=True;runtime._identity={'org_id':cfg['expected_org_id'],'user_id':cfg['runtime_user_id']}
        bound=HumanBoundRuntime(session,runtime,clock=lambda:100)
        attrs={**bound.context(),'agent':'AgentB','allowed_record':'B','allowed_task':'opportunity-summary','request':{k:v for k,v in details('B').items() if k!='type'}}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lab.json';path.write_text(json.dumps({'lab':cfg}))
            self.assertTrue(OPAPolicy(data_path=path).allow(attrs))
            self.assertFalse(OPAPolicy().allow(attrs))
