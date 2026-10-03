"""Offline executable live-path checks. Fake credential and mocked HTTP only."""
import contextlib
import io
import json
import sys
import unittest
from broker_lab.configuration import load_config
import urllib.error
from unittest.mock import MagicMock, patch

from broker_lab.__main__ import details, main
from broker_lab.core import Denied, normalize
from broker_lab.salesforce import HOST, ORG, USER, SalesforceReadOnly

A='006000000000001'
B='006000000000002'


def response(path, value, status=200, final_url=None, raw=None):
    obj=MagicMock()
    obj.status=status
    obj.geturl.return_value=final_url or 'https://'+HOST+path
    obj.read.return_value=raw if raw is not None else json.dumps(value).encode()
    obj.__enter__.return_value=obj
    return obj


class LiveAdapterHTTPTests(unittest.TestCase):
    def adapter(self):
        return SalesforceReadOnly('fake-offline-test-token',{'A':A,'B':B},'v65.0',configuration=load_config())

    def test_identity_and_both_runtime_records_through_mock_http(self):
        adapter=self.adapter()
        def open_response(request, timeout):
            self.assertEqual(timeout,10)
            self.assertEqual(request.get_method(),'GET')
            self.assertEqual(request.get_header('Authorization'),'Bearer fake-offline-test-token')
            self.assertTrue(request.full_url.startswith('https://'+HOST+'/'))
            path=request.full_url.removeprefix('https://'+HOST)
            if path=='/services/oauth2/userinfo':
                return response(path,{'organization_id':ORG,'user_id':USER})
            self.assertIn('?fields=Id%2CName',path)
            record=A if '/'+A+'?' in path else B
            return response(path,{'Id':record,'Name':'Synthetic','Amount':999,'Secret__c':'hidden'})
        with patch.object(adapter._opener,'open',side_effect=open_response) as opened:
            adapter.validate_identity()
            for alias,record in [('A',A),('B',B)]:
                self.assertEqual(adapter.read(normalize(details(alias))),{'Id':record,'Name':'Synthetic'})
            self.assertEqual(opened.call_count,3)

    def test_http_errors_malformed_data_and_redirect_fail_closed(self):
        path='/services/oauth2/userinfo'
        cases=[response(path,{},status=401),response(path,{},raw=b'bad json'),
               response(path,{},final_url='https://elsewhere.example/'),
               response(path,{},raw=b'x'*(1024*1024+1))]
        for item in cases:
            adapter=self.adapter()
            with patch.object(adapter._opener,'open',return_value=item):
                with self.assertRaises(Denied):adapter.validate_identity()
            self.assertFalse(adapter._validated)
        adapter=self.adapter()
        with patch.object(adapter._opener,'open',side_effect=urllib.error.URLError('offline simulated failure')):
            with self.assertRaises(Denied):adapter.validate_identity()
        self.assertFalse(adapter._validated)

    def test_invalid_read_never_reaches_http(self):
        adapter=self.adapter()
        with patch.object(adapter._opener,'open') as opened:
            with self.assertRaises(Denied):adapter.read(normalize(details()))
            opened.assert_not_called()


class LiveExecutableWiringTests(unittest.TestCase):
    def setUp(self):
        config_patch=patch('broker_lab.__main__.load_live_config',return_value=load_config())
        config_patch.start();self.addCleanup(config_patch.stop)

    def argv(self, *extra):
        return ['broker-lab','serve','--salesforce','--record-a',A,'--record-b',B,'--api-version','v65.0',*extra]

    def test_hidden_prompt_identity_then_serve_order(self):
        events=[]
        fake_adapter=MagicMock()
        fake_adapter._validated=True
        def fake_getpass(prompt):
            self.assertIn('broker memory only',prompt)
            events.append('hidden-prompt')
            return 'fake-offline-client-value'
        def fake_serve(path, broker):
            events.append('serve')
            self.assertIs(broker.downstream,fake_adapter)
            self.assertEqual(broker.ttl,60)
        with patch.object(sys,'argv',self.argv()), patch('broker_lab.__main__.sys.stdin.isatty',return_value=True), patch('broker_lab.__main__.getpass.getpass',side_effect=fake_getpass), patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',side_effect=lambda *args,**kwargs:(events.append('authenticate') or fake_adapter)) as constructor, patch('broker_lab.__main__.serve',side_effect=fake_serve):
            main()
        constructor.assert_called_once_with('fake-offline-client-value','fake-offline-client-value',{'A':A,'B':B},'v65.0',configuration=load_config())
        self.assertEqual(events,['hidden-prompt','hidden-prompt','authenticate','serve'])

    def test_identity_failure_does_not_start_server(self):
        fake_adapter=MagicMock()
        fake_adapter._validated=False
        with patch.object(sys,'argv',self.argv()), patch('broker_lab.__main__.sys.stdin.isatty',return_value=True), patch('broker_lab.__main__.getpass.getpass',return_value='fake-offline-test-token'), patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',side_effect=Denied('identity mismatch')), patch('broker_lab.__main__.serve') as serve:
            with self.assertRaises(Denied):main()
            serve.assert_not_called()

    def test_nonterminal_and_fixture_refused_before_token_entry(self):
        for extra in ([],['--fixture-policy']):
            with patch.object(sys,'argv',self.argv(*extra)), patch('broker_lab.__main__.sys.stdin.isatty',return_value=False), patch('broker_lab.__main__.getpass.getpass') as prompt, patch('broker_lab.__main__.serve') as serve, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):main()
                prompt.assert_not_called()
                serve.assert_not_called()

class ClientCredentialsOfflineTests(unittest.TestCase):
    def oauth(self):
        return {'access_token':'fake-test-token','token_type':'Bearer',
                'instance_url':'https://'+HOST,'id':'https://login.salesforce.com/id/'+ORG+'/'+USER}

    def test_form_host_and_verified_identity(self):
        import urllib.parse
        opener=MagicMock()
        path='/services/oauth2/token'
        opener.open.return_value=response(path,self.oauth())
        with patch('broker_lab.salesforce.urllib.request.build_opener',return_value=opener):
            adapter=SalesforceReadOnly.from_client_credentials('fake-id','fake-secret',{'A':A,'B':B})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://'+HOST+path)
        self.assertEqual(request.get_method(),'POST')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(urllib.parse.parse_qs(request.data.decode()),{'grant_type':['client_credentials'],'client_id':['fake-id'],'client_secret':['fake-secret']})
        self.assertEqual(adapter._token,'fake-test-token')
        self.assertEqual(adapter._version,'v67.0')
        self.assertTrue(adapter._validated)  # Verified exact OAuth id/instance before serve.

    def test_oauth_identity_instance_token_fail_closed(self):
        from broker_lab.salesforce import validated_oauth_token
        for key,value in [('id','https://login.salesforce.com/id/'+ORG+'/005000000000001'),
                          ('id','https://elsewhere.example/id/'+ORG+'/'+USER),
                          ('instance_url','https://elsewhere.example'),('token_type','other'),('access_token','bad token')]:
            oauth=self.oauth();oauth[key]=value
            with self.assertRaises(Denied):validated_oauth_token(oauth)
        oauth=self.oauth();oauth['id']='https://'+HOST+'/id/'+ORG[:15]+'/'+USER[:15]
        self.assertEqual(validated_oauth_token(oauth),'fake-test-token')
        oauth['id']='https://'+HOST+'/id/'+ORG[:15]+'ZZZ/'+USER
        with self.assertRaises(Denied):validated_oauth_token(oauth)

    def test_auth_error_never_returns_token_or_body(self):
        opener=MagicMock()
        opener.open.side_effect=urllib.error.HTTPError('https://'+HOST,401,'sensitive fake body',{},None)
        with patch('broker_lab.salesforce.urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(Denied,'^Salesforce authentication failed$'):
                SalesforceReadOnly.from_client_credentials('fake-id','fake-secret',{'A':A,'B':B})

class ConfiguredLivePreflightTests(unittest.TestCase):
    def setUp(self):
        config_patch=patch('broker_lab.__main__.load_live_config',return_value=load_config())
        config_patch.start();self.addCleanup(config_patch.stop)

    def test_actual_nonsecret_config_and_two_record_preflight(self):
        fake_adapter=MagicMock()
        fake_adapter.read.return_value={'Id':'fake-offline-id','Name':'synthetic'}
        with patch.object(sys,'argv',['broker-lab','serve','--salesforce','--check-runtime-access']), patch('broker_lab.__main__.sys.stdin.isatty',return_value=True), patch('broker_lab.__main__.getpass.getpass',return_value='fake-client-value'), patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',return_value=fake_adapter) as authenticate, patch('broker_lab.__main__.serve') as serve, contextlib.redirect_stdout(io.StringIO()):
            main()
        authenticate.assert_called_once_with('fake-client-value','fake-client-value',{'A':'006000000000001AAA','B':'006000000000002AAA'},'v67.0',configuration=load_config())
        self.assertEqual([c.args[0].record for c in fake_adapter.read.call_args_list],['A','B'])
        self.assertTrue(all(set(c.args[0].fields)=={'Id','Name'} for c in fake_adapter.read.call_args_list))
        serve.assert_called_once()

    def test_failed_preflight_prevents_server_start(self):
        fake_adapter=MagicMock()
        fake_adapter.read.side_effect=[{'Id':'fake-A','Name':'A'},Denied('runtime cannot read B')]
        with patch.object(sys,'argv',['broker-lab','serve','--salesforce','--check-runtime-access']), patch('broker_lab.__main__.sys.stdin.isatty',return_value=True), patch('broker_lab.__main__.getpass.getpass',return_value='fake-client-value'), patch('broker_lab.salesforce.SalesforceReadOnly.from_client_credentials',return_value=fake_adapter), patch('broker_lab.__main__.serve') as serve:
            with self.assertRaises(Denied):main()
            serve.assert_not_called()
