import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from broker_lab.__main__ import details
from broker_lab.core import Broker, Caller, Denied, FixturePolicy, MockSalesforce, OPAPolicy, TEST_IDENTITIES, find_opa
from broker_lab.transport import handle, peer_uid, receive
from broker_lab.salesforce import SalesforceReadOnly, ORG, USER, NoRedirect

class BrokerCases:
    def setUp(self):
        self.now = 100
        self.policy = self.make_policy()
        self.downstream = MockSalesforce()
        self.broker = Broker(self.policy,self.downstream,TEST_IDENTITIES,clock=lambda:self.now)
        self.a,self.b = Caller('injected-test-A'),Caller('injected-test-B')

    def issue(self):
        return self.broker.issue(self.a,details())['grant']

    def denied(self, fn):
        before = self.downstream.calls
        with self.assertRaises(Denied):
            fn()
        self.assertEqual(self.downstream.calls,before)

    def test_four_combinations(self):
        for caller in (self.a,self.b):
            for record in ('A','B'):
                expected = (caller==self.a and record=='A') or (caller==self.b and record=='B')
                if expected:
                    req = details(record)
                    grant = self.broker.issue(caller,req)['grant']
                    self.assertEqual(self.broker.redeem(caller,grant,req)['Id'],'mock-'+record)
                else:
                    self.denied(lambda:self.broker.issue(caller,details(record)))
        self.assertEqual(self.downstream.calls,2)

    def test_escalation(self):
        for field,value in [('fields',['Name','Secret__c']),('action','update'),('task','admin'),('audience','other'),('fields',[]),('fields',['Name','Name'])]:
            request = details()
            request[field] = value
            self.denied(lambda:self.broker.issue(self.a,request))

    def test_spoofed_identity(self):
        self.denied(lambda:self.broker.issue(Caller('AgentA'),details()))
        request = details()
        request['agent'] = 'AgentA'
        self.denied(lambda:self.broker.issue(self.b,request))
        self.denied(lambda:self.broker.issue(Caller('unknown'),details()))

    def test_expiry_and_replay(self):
        grant = self.issue()
        self.now += 60
        self.denied(lambda:self.broker.redeem(self.a,grant,details()))
        grant = self.issue()
        self.broker.redeem(self.a,grant,details())
        self.denied(lambda:self.broker.redeem(self.a,grant,details()))

    def test_binding_and_normalization(self):
        grant = self.issue()
        self.denied(lambda:self.broker.redeem(self.b,grant,details()))
        self.denied(lambda:self.broker.redeem(self.a,grant,details('B')))
        request = details()
        request['fields'] = ['Name']
        self.denied(lambda:self.broker.redeem(self.a,grant,request))
        request['fields'] = ['Name','Id']
        self.broker.redeem(self.a,grant,request)

    def test_changed_policy(self):
        grant = self.issue()
        self.revoke()
        self.denied(lambda:self.broker.redeem(self.a,grant,details()))

    def test_outage_at_issue_and_redemption(self):
        grant = self.issue()
        self.broker.policy = OPAPolicy(executable='/nonexistent/opa')
        self.denied(lambda:self.broker.issue(self.a,details()))
        self.denied(lambda:self.broker.redeem(self.a,grant,details()))

    def test_atomic_single_use(self):
        grant = self.issue()
        def redeem(_):
            try:
                self.broker.redeem(self.a,grant,details())
                return True
            except Denied:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(redeem,range(16)))
        self.assertEqual(sum(outcomes),1)
        self.assertEqual(self.downstream.calls,1)

    def test_failed_downstream_still_consumes(self):
        grant = self.issue()
        with patch.object(self.downstream,'read',side_effect=Denied('read failed')):
            self.denied(lambda:self.broker.redeem(self.a,grant,details()))
        self.denied(lambda:self.broker.redeem(self.a,grant,details()))

class OfflineFixtureTests(BrokerCases,unittest.TestCase):
    def make_policy(self):
        return FixturePolicy()
    def revoke(self):
        self.policy.enabled = False

@unittest.skipUnless(find_opa(),'Real OPA NOT RUN: executable absent')
class ActualOPATests(BrokerCases,unittest.TestCase):
    def make_policy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.policy_path = Path(tmp.name)/'broker.rego'
        source = Path(__file__).resolve().parent.parent/'policy'/'broker.rego'
        self.policy_path.write_text(source.read_text())
        return OPAPolicy(self.policy_path)
    def revoke(self):
        self.policy_path.write_text('package broker\nimport rego.v1\ndefault allow := false\n')

class FailClosedAdapterTests(unittest.TestCase):
    def test_malformed_opa_output(self):
        for output in ('{}','not json','{"result":[{"expressions":[{"value":"true"}]}]}','{"result":null}'):
            with patch('broker_lab.core.subprocess.run',return_value=subprocess.CompletedProcess([],0,output,'')):
                self.assertFalse(OPAPolicy().allow({}))
    def test_opa_exception(self):
        with patch('broker_lab.core.subprocess.run',side_effect=subprocess.TimeoutExpired('opa',3)):
            self.assertFalse(OPAPolicy().allow({}))
    def test_policy_exception(self):
        policy = FixturePolicy()
        with patch.object(policy,'allow',side_effect=RuntimeError('outage')):
            broker = Broker(policy,MockSalesforce(),TEST_IDENTITIES)
            with self.assertRaises(Denied):
                broker.issue(Caller('injected-test-A'),details())

class UnixTransportTests(unittest.TestCase):
    def exchange(self, broker, message):
        server,client = socket.socketpair()
        def run():
            with server:
                handle(server,broker)
        thread = threading.Thread(target=run)
        thread.start()
        with client:
            client.sendall(json.dumps(message).encode()+b'\n')
            response = receive(client)
        thread.join()
        return response

    def test_native_uid_issue_redeem_and_spoof(self):
        a,b = socket.socketpair()
        with a,b:
            self.assertEqual(peer_uid(a),os.getuid())
        mock = MockSalesforce()
        broker = Broker(FixturePolicy(),mock,{'uid:'+str(os.getuid()):('AgentA','A','opportunity-summary')})
        response = self.exchange(broker,dict(op='issue',authorization_details=details()))
        self.assertTrue(response['ok'])
        grant = response['result']['grant']
        self.assertTrue(self.exchange(broker,dict(op='redeem',grant=grant,authorization_details=details()))['ok'])
        self.assertFalse(self.exchange(broker,dict(op='issue',agent='AgentB',authorization_details=details('B')))['ok'])
        self.assertFalse(self.exchange(broker,dict(op='issue',authorization_details=details('B')))['ok'])
        self.assertEqual(mock.calls,1)

    def test_unknown_uid(self):
        mock = MockSalesforce()
        broker = Broker(FixturePolicy(),mock,{})
        self.assertFalse(self.exchange(broker,dict(op='issue',authorization_details=details()))['ok'])
        self.assertEqual(mock.calls,0)

class SalesforceOfflineTests(unittest.TestCase):
    def adapter(self):
        return SalesforceReadOnly('fake-test-token',{'A':'006000000000001','B':'006000000000002'},'v65.0')
    def test_identity_guard_no_network(self):
        adapter = self.adapter()
        from broker_lab.core import normalize
        with self.assertRaises(Denied):
            adapter.read(normalize(details()))
        with patch.object(adapter,'_get',return_value={'organization_id':ORG,'user_id':USER}):
            adapter.validate_identity()
        self.assertTrue(adapter._validated)
        with patch.object(adapter,'_get',return_value={'organization_id':'wrong','user_id':USER}):
            with self.assertRaises(Denied):
                adapter.validate_identity()
        self.assertFalse(adapter._validated)
    def test_id_validation(self):
        with self.assertRaises(ValueError):
            SalesforceReadOnly('fake',{'A':'arbitrary SOQL','B':'006000000000002'},'v65.0')
    def test_redirect_refused(self):
        with self.assertRaises(Denied):
            NoRedirect().redirect_request(None,None,302,'redirect',{},'https://elsewhere.example')
    def test_projection_and_target_no_network(self):
        adapter = self.adapter()
        adapter._validated = True
        from broker_lab.core import normalize
        request = details()
        request['fields'] = ['Name']
        with patch.object(adapter,'_get',return_value={'Id':'006000000000001','Name':'A','Secret__c':'hidden'}) as get:
            self.assertEqual(adapter.read(normalize(request)),{'Name':'A'})
            self.assertIn('fields=Id%2CName',get.call_args[0][0])
        with patch.object(adapter,'_get',return_value={'Id':'006000000000002','Name':'B'}):
            with self.assertRaises(Denied):
                adapter.read(normalize(request))
