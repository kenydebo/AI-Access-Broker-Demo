"""Split identity tests: actual OPA, injected human, mocked runtime HTTP only."""
import contextlib
from dataclasses import replace
import io
import json
import sys
import time
import unittest
from broker_lab.configuration import load_config
from unittest.mock import patch,MagicMock
from broker_lab.__main__ import details
from broker_lab.core import Broker,Caller,Denied,OPAPolicy
from broker_lab.human_registry import HumanRegistry
from broker_lab.salesforce import HOST,ORG,USER,SalesforceReadOnly
from broker_lab.user_oauth import HumanSession,HumanBoundRuntime
from broker_lab.user_login import main

RECORDS={'A':'006000000000001AAA','B':'006000000000002AAA'}

class SplitExecutorTests(unittest.TestCase):
    def session(self,alias='A',expiry=1000):
        subject={'A':'005000000000001AAA','B':'005000000000002AAA'}[alias]
        assignment=HumanRegistry().for_verified_subject(ORG,subject)
        return HumanSession(assignment,expiry,'offline-HUMAN-token')
    def runtime(self):
        adapter=SalesforceReadOnly('offline-RUNTIME-token',RECORDS,'v67.0',configuration=load_config())
        adapter._validated=True
        adapter._identity={'org_id':ORG,'user_id':USER}  # injected verified runtime only in test
        return adapter
    def bound(self,alias='A'):
        self.now=100
        downstream=HumanBoundRuntime(self.session(alias),self.runtime(),clock=lambda:self.now)
        broker=Broker(OPAPolicy(),downstream,{'injected-peer':('Agent'+alias,alias,'opportunity-summary')},clock=lambda:self.now,context_provider=downstream.context)
        return downstream,broker
    def test_four_combinations_only_runtime_bearer_executes(self):
        for alias in ('A','B'):
            for target in ('A','B'):
                with self.subTest(human=alias,target=target):
                    downstream,broker=self.bound(alias)
                    def opened(request,timeout):
                        self.assertEqual(request.get_header('Authorization'),'Bearer offline-RUNTIME-token')
                        self.assertNotIn('offline-HUMAN-token',str(request.headers))
                        self.assertEqual(request.get_method(),'GET')
                        self.assertIn('/'+RECORDS[target]+'?',request.full_url)
                        response=MagicMock();response.status=200;response.geturl.return_value=request.full_url
                        response.read.return_value=json.dumps({'Id':RECORDS[target],'Name':'Synthetic '+target}).encode()
                        response.__enter__.return_value=response
                        return response
                    with patch.object(downstream.adapter._opener,'open',side_effect=opened) as http:
                        if alias==target:
                            grant=broker.issue(Caller('injected-peer'),details(target))['grant']
                            self.assertEqual(broker.redeem(Caller('injected-peer'),grant,details(target)),{'Id':RECORDS[target],'Name':'Synthetic '+target})
                            self.assertEqual(http.call_count,1)
                        else:
                            with self.assertRaises(Denied):broker.issue(Caller('injected-peer'),details(target))
                            http.assert_not_called()
    def test_missing_or_mismatched_runtime_refused(self):
        for identity,validated in ((None,True),({'org_id':ORG,'user_id':'005000000000001AAA'},True),({'org_id':ORG,'user_id':USER},False)):
            runtime=self.runtime();runtime._identity=identity;runtime._validated=validated
            with self.assertRaises(Denied):HumanBoundRuntime(self.session(),runtime,clock=lambda:100)
    def test_session_expiry_at_issue_and_redemption_no_runtime_call(self):
        downstream,broker=self.bound()
        grant=broker.issue(Caller('injected-peer'),details())['grant']
        downstream.session=replace(downstream.session,expires_at=110)
        self.now=110
        with patch.object(downstream.adapter,'read') as read:
            with self.assertRaises(Denied):broker.issue(Caller('injected-peer'),details())
            with self.assertRaises(Denied):broker.redeem(Caller('injected-peer'),grant,details())
            read.assert_not_called()
    def test_changed_human_session_cannot_redeem_old_grant(self):
        downstream,broker=self.bound()
        grant=broker.issue(Caller('injected-peer'),details())['grant']
        downstream.session=self.session()  # Same human/task, new session identifier.
        with patch.object(downstream.adapter,'read') as read:
            with self.assertRaises(Denied):broker.redeem(Caller('injected-peer'),grant,details())
            read.assert_not_called()
    def test_opa_checks_human_and_executor_attributes(self):
        downstream,broker=self.bound()
        attrs=broker.attributes(Caller('injected-peer'),__import__('broker_lab.core',fromlist=['normalize']).normalize(details()))
        self.assertTrue(OPAPolicy().allow(attrs))
        for name,key,value in [('human','user_id','005000000000002AAA'),('human','org_id','bad-org'),('human','session_id',''),('executor','user_id','005000000000001AAA'),('executor','validated',False)]:
            changed=json.loads(json.dumps(attrs));changed[name][key]=value
            self.assertFalse(OPAPolicy().allow(changed))
        changed=dict(attrs);del changed['human']
        self.assertFalse(OPAPolicy().allow(changed))
    def test_outage_and_runtime_revocation_fail_closed(self):
        downstream,broker=self.bound()
        grant=broker.issue(Caller('injected-peer'),details())['grant']
        with patch.object(broker.policy,'allow',return_value=False),patch.object(downstream.adapter,'read') as read:
            with self.assertRaises(Denied):broker.redeem(Caller('injected-peer'),grant,details())
            read.assert_not_called()
        with patch.object(downstream.adapter._opener,'open',side_effect=OSError('injected revoked token')) as http:
            with self.assertRaises(Denied):broker.redeem(Caller('injected-peer'),grant,details())
            self.assertEqual(http.call_count,1)
            with self.assertRaises(Denied):broker.redeem(Caller('injected-peer'),grant,details())
            self.assertEqual(http.call_count,1)
    def test_close_discards_both_session_and_runtime(self):
        downstream,broker=self.bound()
        downstream.close()
        self.assertIsNone(downstream.session)
        self.assertEqual(downstream.adapter._token,'')
        with self.assertRaises(Denied):broker.issue(Caller('injected-peer'),details())

class SplitLoginWiringTests(unittest.TestCase):
    def setUp(self):
        config_patch=patch('broker_lab.user_login.load_live_config',return_value=load_config())
        config_patch.start();self.addCleanup(config_patch.stop)

    def test_runtime_prompt_order_and_binding(self):
        session=SplitExecutorTests().session(expiry=time.monotonic()+900)
        runtime=SplitExecutorTests().runtime()
        events=[];prompts=[]
        def prompt(text): prompts.append(text);events.append('prompt');return ['human-key','human-secret','runtime-key','runtime-secret'][len(prompts)-1]
        def login(flow): events.append('human-login');return session
        def authenticate(*args,**kwargs): events.append('runtime-auth');return runtime
        def serve(path,broker):
            events.append('serve')
            self.assertEqual(path,'.run/split.sock')
            self.assertIs(broker.downstream.adapter,runtime)
            self.assertEqual(broker.context_provider()['human']['user_id'],session.assignment.salesforce_user_id)
        with patch.object(sys,'argv',['lab','--approved-setup','--executor','runtime','--socket','.run/split.sock']),patch('broker_lab.user_login.sys.stdin.isatty',return_value=True),patch('broker_lab.user_login.getpass.getpass',side_effect=prompt),patch('broker_lab.user_login.login',side_effect=login),patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',side_effect=authenticate) as auth,patch('broker_lab.user_login.serve',side_effect=serve),contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        auth.assert_called_once_with('runtime-key','runtime-secret',RECORDS,'v67.0',configuration=load_config())
        self.assertEqual(events,['prompt','prompt','human-login','prompt','prompt','runtime-auth','serve'])
        self.assertIn('Human-login',prompts[0]);self.assertIn('RUNTIME',prompts[2])
        self.assertIn('verified integration runtime',output.getvalue())
        self.assertNotIn('offline-HUMAN-token',output.getvalue())
        self.assertEqual(runtime._token,'')
    def test_runtime_auth_failure_never_serves(self):
        session=SplitExecutorTests().session(expiry=time.monotonic()+900)
        with patch.object(sys,'argv',['lab','--approved-setup','--executor','runtime']),patch('broker_lab.user_login.sys.stdin.isatty',return_value=True),patch('broker_lab.user_login.getpass.getpass',return_value='injected-credential'),patch('broker_lab.user_login.login',return_value=session),patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',side_effect=Denied('wrong runtime identity')),patch('broker_lab.user_login.serve') as serve:
            with self.assertRaises(Denied):main()
            serve.assert_not_called()
    def test_default_preserves_and_labels_human_executor(self):
        session=SplitExecutorTests().session(expiry=time.monotonic()+900)
        with patch.object(sys,'argv',['lab','--approved-setup']),patch('broker_lab.user_login.sys.stdin.isatty',return_value=True),patch('broker_lab.user_login.getpass.getpass',return_value='injected-credential') as prompt,patch('broker_lab.user_login.login',return_value=session),patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials') as auth,patch('broker_lab.user_login.serve') as serve,contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertEqual(prompt.call_count,2);auth.assert_not_called();serve.assert_called_once()
        self.assertIn('HUMAN token (initial validation mode; not split identity)',output.getvalue())
