"""Actual SDK stdio -> Unix socket -> actual OPA -> mock Salesforce."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

from broker_lab.core import Broker, MockSalesforce, OPAPolicy, find_opa
from broker_lab.__main__ import details
from broker_lab.transport import handle


@unittest.skipUnless(SDK_AVAILABLE and find_opa(), 'actual MCP + OPA dependencies required')
class ActualMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp',prefix='broker-mcp-')
        self.socket_path = str(Path(self.temp.name)/'broker.sock')
        self.now = 100
        self.mock = MockSalesforce()
        self.policy = OPAPolicy()
        self.broker = Broker(self.policy,self.mock,
            {'uid:'+str(os.getuid()):('AgentA','A','opportunity-summary')},clock=lambda:self.now)
        self.listener = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.listener.bind(self.socket_path)
        os.chmod(self.socket_path,0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.stop = threading.Event()
        def run():
            while not self.stop.is_set():
                try:
                    conn,_ = self.listener.accept()
                except socket.timeout:
                    continue
                with conn:
                    handle(conn,self.broker)
        self.thread = threading.Thread(target=run)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(timeout=6)
        self.listener.close()
        self.temp.cleanup()
        self.assertFalse(self.thread.is_alive())

    async def session_check(self, callback):
        params = StdioServerParameters(command=sys.executable,
            args=['-m','broker_lab.mcp_server','--socket',self.socket_path],
            cwd=str(Path(__file__).resolve().parent.parent),env={'PYTHONDONTWRITEBYTECODE':'1'})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({t.name for t in tools.tools},{'authorize_read','redeem_read'})
                await callback(session)

    def result_data(self, result):
        self.assertFalse(result.is_error)
        if result.structured_content is not None:
            return result.structured_content
        return json.loads(result.content[0].text)

    async def grant(self, session):
        result = await session.call_tool('authorize_read',{'authorization_details':details()})
        return self.result_data(result)['grant']

    async def assert_denied(self, session, name, args):
        before = self.mock.calls
        result = await session.call_tool(name,args)
        self.assertTrue(result.is_error)
        self.assertEqual(self.mock.calls,before)

    async def test_stdio_read_denials_and_replay(self):
        async def check(session):
            grant = await self.grant(session)
            req = details()
            req['fields'] = ['Name']
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':req})
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':details('B')})
            for field,value in [('record','B'),('action','update'),('fields',['Secret__c']),('task','admin'),('audience','other'),('agent','AgentB')]:
                req = details()
                req[field] = value
                await self.assert_denied(session,'authorize_read',{'authorization_details':req})
            await self.assert_denied(session,'authorize_read',{'authorization_details':details(),'agent':'AgentB'})
            self.assertEqual(self.mock.calls,0)
            result = await session.call_tool('redeem_read',{'grant':grant,'authorization_details':details()})
            self.assertEqual(self.result_data(result),{'Id':'mock-A','Name':'Synthetic A'})
            self.assertEqual(self.mock.calls,1)
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':details()})
        await self.session_check(check)

    async def test_stdio_expiry_policy_change_and_outage(self):
        async def check(session):
            grant = await self.grant(session)
            self.now += 60
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':details()})
            grant = await self.grant(session)
            # Change authoritative server-owned scope between issue and redemption.
            self.broker.identities['uid:'+str(os.getuid())] = ('AgentA','B','opportunity-summary')
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':details()})
            self.broker.identities['uid:'+str(os.getuid())] = ('AgentA','A','opportunity-summary')
            self.broker.policy = OPAPolicy(executable='/nonexistent/opa')
            await self.assert_denied(session,'redeem_read',{'grant':grant,'authorization_details':details()})
            await self.assert_denied(session,'authorize_read',{'authorization_details':details()})
            self.assertEqual(self.mock.calls,0)
        await self.session_check(check)
