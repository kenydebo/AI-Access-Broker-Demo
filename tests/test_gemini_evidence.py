"""Offline provider fixtures; actual broker/OPA, no provider network access."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from broker_lab.core import find_opa
from broker_lab.gemini_agent import GeminiAgent, GeminiClient
from test_gemini_agent import KEY, MockOpener, answer, reply


@unittest.skipUnless(find_opa(), 'Actual OPA evidence tests NOT RUN: OPA missing')
class GeminiEvidenceTests(unittest.TestCase):
    def run_demo(self, values, scenario='cross-customer', scope='A'):
        opener = MockOpener(values)
        agent = GeminiAgent(GeminiClient(KEY, opener=opener), clock=lambda: 100)
        context = {'scope': scope, 'id': 'private-injected-demo-session-token', 'expires': 400}
        result = agent.run_demo(scenario, {'token': 'private-browser-token', 'resolver': lambda token: dict(context)})
        encoded = json.dumps(result)
        for private in (KEY, context['id'], 'private-browser-token', 'test-signature'):
            self.assertNotIn(private, encoded)
        return result, opener

    def test_text_empty_refusal_and_thought_only_never_imply_a_request(self):
        for parts, text in (([{'text': 'Just ordinary text.'}], 'Just ordinary text.'),
                            ([{'text': ''}], None), ([{'text': 'I refuse.'}], 'I refuse.'),
                            ([{'text': 'PRIVATE INTERNAL THOUGHT', 'thought': True}], None)):
            with self.subTest(parts=parts):
                result, opener = self.run_demo([reply(parts=parts)])
                self.assertEqual(result['model_outcome'], 'no_tool_call')
                self.assertEqual(result['model_response']['finish_reason'], 'STOP')
                self.assertEqual(result['model_response']['non_thought_text_untrusted'], text)
                self.assertIsNone(result['model_answer_untrusted'])
                self.assertIsNone(result['policy_context'])
                self.assertEqual(result['evaluated_policy_inputs'], [])
                self.assertIsNone(result['broker_request'])
                self.assertEqual(result['model_response']['tool_calls'], [])
                self.assertEqual(result['downstream_reads'], 0)
                self.assertEqual(len(opener.requests), 1)
                self.assertNotIn('PRIVATE INTERNAL THOUGHT', json.dumps(result))
                self.assertEqual(result['request_context']['allowed_record'], 'A')
                self.assertEqual(result['request_context']['cross_customer_target'], 'B')
                sent = json.loads(opener.requests[0][0].data)
                self.assertEqual(result['model_prompt_sent'], sent['contents'][0]['parts'][0]['text'])
                self.assertEqual(result['model_system_instruction_sent'], sent['systemInstruction']['parts'][0]['text'])

    def test_cross_customer_proposal_reaches_opa_without_changing_identity(self):
        for scope, target in (('A', 'B'), ('B', 'A')):
            with self.subTest(scope=scope):
                result, _ = self.run_demo([reply(target), answer('Denied.')], scope=scope)
                self.assertEqual(result['model_response']['tool_calls'][0]['args'], {'record': target, 'fields': ['Id', 'Name']})
                self.assertEqual(result['host_outcome'], 'accepted')
                self.assertEqual(result['request_context']['allowed_record'], scope)
                self.assertEqual(result['request_context']['requested_record'], target)
                self.assertEqual(result['broker_request']['record'], target)
                self.assertEqual(result['policy_context']['allowed_record'], scope)
                self.assertEqual(result['policy_context']['requested_record'], target)
                self.assertEqual(result['broker_outcome'], 'deny')
                self.assertEqual(next(e for e in result['events'] if e['stage'] == 'opa')['reasons'], ['target_outside_task_scope'])
                self.assertEqual(result['downstream_reads'], 0)
                self.assertIsNone(result['authorized_data'])
                self.assertEqual(len(result['evaluated_policy_inputs']), 1)

    def test_cross_customer_wire_prompt_is_exact_retrieval_without_identity_claims(self):
        for scope,target in (('A','B'),('B','A')):
            result,opener=self.run_demo([reply(target),answer('Denied.')],scope=scope)
            sent=json.loads(opener.requests[0][0].data)
            expected='Retrieve synthetic record '+target+' using read_record with fields Id and Name.'
            self.assertEqual(sent['contents'][0]['parts'][0]['text'],expected)
            self.assertEqual(result['model_prompt_sent'],expected)
            self.assertEqual(result['request_context']['cross_customer_target'],target)
            self.assertEqual(sent['toolConfig']['functionCallingConfig']['mode'],'AUTO')
            self.assertNotIn('claim',expected.lower());self.assertNotIn('ignore',expected.lower())
            self.assertEqual(result['request_context']['allowed_record'],scope)
            self.assertEqual(result['broker_outcome'],'deny');self.assertEqual(result['downstream_reads'],0)

    def test_cross_customer_identity_injection_is_rejected_before_broker(self):
        response = reply('B')
        response['candidates'][0]['content']['parts'][0]['functionCall']['args']['identity'] = 'CustomerB'
        result, _ = self.run_demo([response])
        self.assertEqual(result['host_outcome'], 'rejected')
        self.assertEqual(result['request_context']['allowed_record'], 'A')
        self.assertEqual(result['broker_outcome'], 'not_evaluated')
        self.assertIsNone(result['broker_request']); self.assertIsNone(result['policy_context'])
        self.assertEqual(result['downstream_reads'], 0)

    def test_explicit_b_read_under_a_records_actual_opa_denial_and_tool_response(self):
        result, opener = self.run_demo([reply('B'), answer('No data returned.')], 'read-b')
        self.assertEqual(result['host_outcome'], 'accepted')
        self.assertEqual(result['broker_request']['record'], 'B')
        self.assertEqual(result['policy_context']['requested_record'], 'B')
        self.assertEqual(result['policy_context']['allowed_record'], 'A')
        self.assertEqual(len(result['evaluated_policy_inputs']), 1)
        self.assertEqual(result['broker_outcome'], 'deny'); self.assertEqual(result['downstream_reads'], 0)
        self.assertEqual(next(e for e in result['events'] if e['stage'] == 'opa')['reasons'], ['target_outside_task_scope'])
        sent = json.loads(opener.requests[1][0].data)
        self.assertEqual(result['tool_response'], sent['contents'][2]['parts'][0]['functionResponse']['response'])

    def test_changed_target_is_rejected_and_regular_a_read_still_allows(self):
        result, _ = self.run_demo([reply('A')])
        self.assertEqual(result['host_outcome'], 'rejected')
        self.assertEqual(result['model_outcome'], 'host_request_binding_denied')
        self.assertIsNone(result['broker_request']); self.assertEqual(result['downstream_reads'], 0)
        result, _ = self.run_demo([reply('A'), answer('Synthetic A only.')], 'read-a')
        self.assertEqual(result['broker_outcome'], 'allow'); self.assertEqual(result['downstream_reads'], 1)
        self.assertEqual(len(result['evaluated_policy_inputs']), 2)
        self.assertEqual(result['tool_response']['data'], {'Id': 'mock-A', 'Name': 'Synthetic A'})

    def test_safe_metadata_survives_block_token_limit_and_malformed_response(self):
        fixtures = [({'promptFeedback': {'blockReason': 'SAFETY'}}, 'model_blocked', None, 'SAFETY'),
                    ({'candidates': [{'finishReason': 'MAX_TOKENS'}]}, 'incomplete_response', 'MAX_TOKENS', None),
                    ({'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': []}}]}, 'invalid_response', 'STOP', None)]
        for response, outcome, finish, block in fixtures:
            result, _ = self.run_demo([response])
            self.assertEqual(result['model_outcome'], outcome)
            self.assertEqual(result['model_response']['finish_reason'], finish)
            self.assertEqual(result['model_response']['block_reason'], block)
            self.assertIsNone(result['broker_request']); self.assertIsNone(result['policy_context'])
            self.assertEqual(result['downstream_reads'], 0)
        malformed = reply('B')
        malformed['candidates'][0]['content']['parts'][0]['functionCall']['args']['identity'] = 'PRIVATE CLAIM'
        result, _ = self.run_demo([malformed])
        self.assertTrue(result['model_response']['tool_calls'][0]['unsupported_content_redacted'])
        self.assertNotIn('PRIVATE CLAIM', json.dumps(result))
        self.assertEqual(result['downstream_reads'], 0)


class EvidenceSummaryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node') or Path('/opt/homebrew/bin/node').exists(), 'Node unavailable: UI summary checks NOT RUN')
    def test_summary_does_not_conflate_no_call_host_rejection_and_opa_denial(self):
        source = (Path(__file__).resolve().parent.parent / 'web/gemini_controls.html').read_text()
        functions = source[source.index('function describeModel'):source.index('function showEvidence')]
        script = functions + '''
const assert=require('node:assert/strict');
const context={allowed_record:'A',requested_record:'B',cross_customer_target:'B'};
const noCall={request_context:context,model_outcome:'no_tool_call',broker_outcome:'not_evaluated',downstream_reads:0,broker_request:null,evaluated_policy_inputs:[],model_response:{finish_reason:'STOP',tool_calls:[]}};
assert.equal(describeModel(noCall).title,'No tool call; policy not tested');
assert.equal(evidenceView(noCall).mismatch,false);
assert.equal(evidenceView(noCall).brokerRequest,'No request sent to the broker.');
const rejected={...noCall,model_outcome:'host_request_binding_denied',host_outcome:'rejected',model_response:{tool_calls:[{name:'read_record',args:{record:'B',fields:['Id','Name']}}]}};
assert.equal(evidenceView(rejected).mismatch,true);
assert.match(describeModel(rejected).why,/before the broker or OPA/);
const denied={...rejected,model_outcome:'answer_received_untrusted',host_outcome:'accepted',broker_outcome:'deny',broker_request:{record:'B'},evaluated_policy_inputs:[{}],events:[{stage:'opa',reasons:['target_outside_task_scope']}]};
assert.match(describeModel(denied).why,/request for B is outside/);
assert.match(evidenceView(denied).decision,/OPA evaluations: 1/);
'''
        subprocess.run([shutil.which('node') or '/opt/homebrew/bin/node', '-e', script], check=True, capture_output=True, text=True)
