"""Model responses are injected; these tests make no Ollama or Salesforce requests."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from broker_lab.__main__ import details
try:
    from broker_lab.ollama_host import tool_loop
    SDK_AVAILABLE=True
except ImportError:
    SDK_AVAILABLE=False

@unittest.skipUnless(SDK_AVAILABLE,'MCP SDK required')
class OllamaHostTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session=SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[
            SimpleNamespace(name=n,description='test',input_schema={}) for n in ('authorize_read','redeem_read')])),call_tool=AsyncMock())
        return session
    def replies(self, items):
        pending=iter(items)
        return lambda *args:next(pending)
    def call(self,name,args):
        return {'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}
    async def test_issue_redeem_and_summary(self):
        session=self.session()
        session.call_tool.side_effect=[SimpleNamespace(is_error=False,structured_content={'grant':'fake-short-grant','expires_in':60}),
            SimpleNamespace(is_error=False,structured_content={'Id':'mock-A','Name':'A'})]
        output=await tool_loop(session,'existing-model','A',self.replies([
            self.call('read_opportunity',{'authorization_details':details()}),
            {'role':'assistant','content':'A summary'}]))
        self.assertEqual(output['authorized_reads'],[{'Id':'mock-A','Name':'A'}])
        self.assertEqual(output['tool_calls'],1)
        self.assertNotIn('fake-short-grant',json.dumps(output))
        self.assertEqual(output['grants'],[{'ttl_seconds':60,'consumed':True}])
        self.assertEqual(session.call_tool.call_count,2)
    async def test_model_scope_and_identity_escalation_never_reaches_mcp(self):
        for args in ({'authorization_details':details('B')},{'authorization_details':details(),'agent':'AgentB'}):
            session=self.session()
            output=await tool_loop(session,'existing-model','A',self.replies([self.call('read_opportunity',args)]))
            session.call_tool.assert_not_called()
            self.assertEqual(output['authorized_reads'],[])
            self.assertEqual(output['denials'],1)
    async def test_broker_denial_stops_loop(self):
        session=self.session()
        session.call_tool.return_value=SimpleNamespace(is_error=True)
        output=await tool_loop(session,'existing-model','B',self.replies([self.call('read_opportunity',{'authorization_details':details('B')})]))
        self.assertEqual(output['denials'],1)
        self.assertEqual(session.call_tool.call_count,1)
    async def test_hallucinated_answer_without_read_suppressed(self):
        session=self.session()
        output=await tool_loop(session,'existing-model','A',self.replies([{'role':'assistant','content':'invented data'}]))
        self.assertEqual(output['model_answer'],'No authorized record returned.')
        session.call_tool.assert_not_called()
