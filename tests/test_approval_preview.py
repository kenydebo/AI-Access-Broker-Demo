"""Actual OPA policy + injected TEST contexts. No login, credential or Salesforce write."""
import concurrent.futures
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from broker_lab.configuration import load_config
from broker_lab.approval_preview import ApprovalPreview, Delegation
from broker_lab.core import Denied, OPAPolicy, find_opa
from broker_lab.trusted_context import AgentContext, HumanContext

class InjectedTestResolver:
    def __init__(self):
        self.agent_ref=object();self.human_ref=object();self.other_human=object();self.child_ref=object()
        self.agents={self.agent_ref:AgentContext('test-workload-A','test-sf-user-A','root'),
                     self.child_ref:AgentContext('test-child','test-sf-user-A','child')}
        self.humans={self.human_ref:HumanContext('independent-test-human-A','test-sf-user-A'),
                     self.other_human:HumanContext('independent-test-human-B','test-sf-user-B')}
    def agent(self, ref):
        if ref not in self.agents:raise Denied('test-only unauthenticated workload')
        return self.agents[ref]
    def human(self, ref):
        if ref not in self.humans:raise Denied('test-only unauthenticated human')
        return self.humans[ref]

@unittest.skipUnless(find_opa(),'actual OPA required')
class ApprovalPreviewTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.resolver=InjectedTestResolver()
        records=load_config()['records']
        self.preview=ApprovalPreview(records,resolver=self.resolver,clock=lambda:self.now)
        self.root=Delegation('root','test-workload-A','test-sf-user-A','next-step-followup',frozenset({'A'}),frozenset({'NextStep'}),200)
        self.preview.add_delegation(self.root)
    def request(self):
        return {'type':'salesforce_record_change','record':'A','action':'update',
                'changes':{'NextStep':'Synthetic approval preview'},'task':'next-step-followup','audience':'salesforce-write-preview'}
    def proposed(self):return self.preview.propose(self.resolver.agent_ref,self.request())
    def approved(self):
        result=self.proposed()
        self.preview.approve(self.resolver.human_ref,result['proposal'],result['request_hash'])
        return result
    def test_pending_exact_human_approval_and_nonexecuting_one_use(self):
        result=self.proposed();key=result['proposal']
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,key,self.request())
        human_view=self.preview.human_preview(self.resolver.human_ref,key)
        self.assertEqual(human_view['request']['record_id'],self.preview.records['A'])
        self.assertEqual(human_view['request']['changes'],self.request()['changes'])
        self.preview.approve(self.resolver.human_ref,key,result['request_hash'])
        output=self.preview.redeem_preview(self.resolver.agent_ref,key,self.request())
        self.assertIs(output['executed'],False)
        self.assertIs(output['dry_run'],True)
        self.assertEqual(output['approved_by'],'independent-test-human-A')
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,key,self.request())
    def test_agent_cannot_self_approve_and_other_human_cannot_approve(self):
        result=self.proposed()
        for ref in (self.resolver.agent_ref,self.resolver.other_human,'CustomerA'):
            with self.assertRaises(Denied):self.preview.approve(ref,result['proposal'],result['request_hash'])
    def test_unknown_workload_or_selected_label_is_not_authentication(self):
        for ref in ('AgentA','CustomerA',object()):
            with self.assertRaises(Denied):self.preview.propose(ref,self.request())
        missing=ApprovalPreview(self.preview.records)
        with self.assertRaises(Denied):missing.propose('AgentA',self.request())
    def test_exact_hash_required(self):
        result=self.proposed()
        with self.assertRaises(Denied):self.preview.approve(self.resolver.human_ref,result['proposal'],'tampered')
    def test_changed_record_field_value_task_and_audience_denied(self):
        result=self.approved()
        for field,value in [('record','B'),('changes',{'NextStep':'different exact value'}),('changes',{'Amount':200}),('task','other'),('audience','salesforce-write')]:
            request=self.request();request[field]=value
            with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,result['proposal'],request)
        request=self.request();request['approved']=True
        with self.assertRaises(Denied):self.preview.propose(self.resolver.agent_ref,request)
    def test_mutating_original_request_cannot_mutate_saved_approval(self):
        request=self.request()
        result=self.preview.propose(self.resolver.agent_ref,request)
        request['changes']['NextStep']='mutated after issuance'
        self.assertEqual(self.preview.human_preview(self.resolver.human_ref,result['proposal'])['request']['changes']['NextStep'],'Synthetic approval preview')
    def test_expiry_before_approval_and_before_redemption(self):
        result=self.proposed();self.now+=60
        with self.assertRaises(Denied):self.preview.approve(self.resolver.human_ref,result['proposal'],result['request_hash'])
        result=self.approved();self.now+=60
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,result['proposal'],self.request())
    def test_parent_revocation_invalidates_approved_child(self):
        child=Delegation('child','test-child','test-sf-user-A',self.root.task,frozenset({'A'}),frozenset({'NextStep'}),190,'root')
        self.preview.add_delegation(child)
        result=self.preview.propose(self.resolver.child_ref,self.request())
        self.preview.approve(self.resolver.human_ref,result['proposal'],result['request_hash'])
        self.preview.revoke('root')
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.child_ref,result['proposal'],self.request())
    def test_child_cannot_broaden_customer_fields_human_task_or_lifetime(self):
        child=Delegation('child','test-child','test-sf-user-A',self.root.task,frozenset({'A'}),frozenset({'NextStep'}),190,'root')
        for variant in (replace(child,records=frozenset({'A','B'})),replace(child,fields=frozenset({'NextStep','Amount'})),replace(child,human_user_id='test-sf-user-B'),replace(child,task='other'),replace(child,expires=201)):
            with self.assertRaises(Denied):self.preview.add_delegation(variant)
    def test_changed_opa_policy_and_outage_recheck(self):
        result=self.approved()
        with tempfile.TemporaryDirectory() as temp:
            policy=Path(temp)/'deny.rego';policy.write_text('package broker\nimport rego.v1\ndefault allow := false\n')
            self.preview.policy=OPAPolicy(policy)
            with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,result['proposal'],self.request())
        self.preview.policy=OPAPolicy(executable='/nonexistent/opa')
        with self.assertRaises(Denied):self.preview.propose(self.resolver.agent_ref,self.request())
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.agent_ref,result['proposal'],self.request())
    def test_concurrent_preview_consumption_exactly_once(self):
        result=self.approved()
        def redeem(_):
            try:return self.preview.redeem_preview(self.resolver.agent_ref,result['proposal'],self.request())
            except Denied:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:outcomes=list(pool.map(redeem,range(16)))
        successes=[o for o in outcomes if o is not None]
        self.assertEqual(len(successes),1)
        self.assertIs(successes[0]['executed'],False)
    def test_sender_binding_even_when_human_and_task_match(self):
        result=self.approved()
        self.resolver.agents[self.resolver.child_ref]=AgentContext('test-other-workload','test-sf-user-A','root')
        with self.assertRaises(Denied):self.preview.redeem_preview(self.resolver.child_ref,result['proposal'],self.request())
