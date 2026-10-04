"""Public scripted synthetic demo; optional private loopback model adapter. No live Salesforce."""
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit
from .configuration import load_config
from .core import Broker, Caller, Denied, MockSalesforce, OPAPolicy, find_opa

SCENARIOS = {'allow-a': ('A', 'A', 'read'), 'allow-b': ('B', 'B', 'read'),
             'cross-a': ('A', 'B', 'read'), 'cross-b': ('B', 'A', 'read'),
             'expired': ('A', 'A', 'expired'), 'replay': ('A', 'A', 'replay')}
COOKIE = '__Host-broker-demo'


def run_scenario(name, policy=None):
    if name not in SCENARIOS:
        raise ValueError('Unknown fixed scenario')
    customer, record, operation = SCENARIOS[name]
    config = load_config()  # Public example only. Never loads .local.
    context = {'human_session_required': True, 'runtime_executor_required': True,
               'human': {'org_id': config['expected_org_id'],
                         'user_id': config['users']['Customer'+customer]['salesforce_user_id'],
                         'session_id': secrets.token_urlsafe(32), 'active': True,
                         'allowed_record': customer, 'allowed_task': 'opportunity-summary'},
               'executor': {'org_id': config['expected_org_id'],
                            'user_id': config['runtime_user_id'], 'validated': True}}
    events = []
    pdp = policy or OPAPolicy(timeout=10)

    class Evidence:
        def allow(self, attributes):
            decision = pdp.decide(attributes)
            events.append({'stage': 'opa', 'allow': decision['allow'], 'reasons': decision['reasons']})
            return decision['allow']

    now = [time.monotonic()]
    downstream = MockSalesforce()
    broker = Broker(Evidence(), downstream,
                    {'hosted-simulation': ('Agent'+customer, customer, 'opportunity-summary')},
                    clock=lambda: now[0], context_provider=lambda: context)
    caller = Caller('hosted-simulation')
    details = {'type': 'salesforce_record', 'record': record, 'action': 'read',
               'fields': ['Id', 'Name'], 'task': 'opportunity-summary', 'audience': 'salesforce-read'}
    data = None
    try:
        grant = broker.issue(caller, details)['grant']
        events.append({'stage': 'issue', 'result': 'opaque grant kept server-side'})
        if operation == 'expired':
            now[0] += 60
            events.append({'stage': 'clock', 'result': 'isolated test clock advanced 60 seconds'})
        data = broker.redeem(caller, grant, details)
        events.append({'stage': 'redeem', 'result': 'allowed; grant consumed before synthetic read'})
        if operation == 'replay':
            broker.redeem(caller, grant, details)
        decision = 'allow'
    except Denied:
        decision = 'replay_denied_after_one_read' if operation == 'replay' and downstream.calls else 'deny'
        events.append({'stage': 'redeem' if operation in ('expired', 'replay') else 'authorization',
                       'result': 'denied; this denied operation made no downstream read'})
    finally:
        broker.grants.clear()
    return {'scenario': name, 'decision': decision, 'customer': customer, 'requested_record': record,
            'downstream_reads': downstream.calls, 'data': data, 'events': events,
            'identity': 'server-injected synthetic session; scenario selection is not authentication',
            'execution': 'scripted broker request; real OPA; synthetic Salesforce; no model or MCP transport'}


class Sessions:
    def __init__(self, clock=time.monotonic, capacity=128, ttl=900):
        self.clock, self.capacity, self.ttl = clock, capacity, ttl
        self.lock = threading.Lock()
        self.items = {}
        self.created = []
        self.runs = []
        self.execution = threading.BoundedSemaphore(1)

    def cleanup(self):
        with self.lock:
            now = self.clock()
            self.items = {key: value for key, value in self.items.items() if value['expires'] > now}
            self.created = [stamp for stamp in self.created if stamp > now-60]
            self.runs = [stamp for stamp in self.runs if stamp > now-60]

    def page(self, cookie):
        self.cleanup()
        with self.lock:
            session = self.items.get(cookie)
            if session is not None:
                return cookie, session
            if len(self.items) >= self.capacity or len(self.created) >= 20:
                return None, None
            key = secrets.token_urlsafe(32)
            session = {'csrf': secrets.token_urlsafe(32), 'expires': self.clock()+self.ttl,
                       'runs': [], 'busy': False, 'model_subject': secrets.token_urlsafe(32), 'model_runs': [], 'model_total': 0, 'demo_context': None}
            self.items[key] = session
            self.created.append(self.clock())
            return key, session

    def replace_demo(self, token, session, customer):
        if customer not in ('A', 'B'):
            raise ValueError('Unknown demo customer')
        with self.lock:
            if self.items.get(token) is not session or session['expires'] <= self.clock():
                raise Denied('Demo token expired or unknown')
            key = secrets.token_urlsafe(32)
            replacement = {**session, 'csrf': secrets.token_urlsafe(32), 'busy': False,
                           'model_subject': secrets.token_urlsafe(32),
                           'demo_context': {'id': secrets.token_urlsafe(32), 'expires': min(self.clock()+300, session['expires']), 'scope': customer}}
            del self.items[token]  # Old browser token/identity is invalid immediately.
            self.items[key] = replacement
            return key, {'principal': 'Temporary DemoCustomer'+customer, 'scope': customer,
                         'task': 'opportunity-summary', 'expires_in': max(0, int(replacement['demo_context']['expires']-self.clock())),
                         'csrf': replacement['csrf'], 'real_human_authentication': False,
                         'events': [{'stage': 'demo_session', 'source': 'server-issued temporary principal', 'scope': customer, 'real_human_authentication': False}]}

    def resolve_demo(self, token):
        self.cleanup()
        with self.lock:
            session = self.items.get(token)
            if session is None or session['demo_context'] is None or session['demo_context']['expires'] <= self.clock():
                raise Denied('Demo token expired or unknown')
            return dict(session['demo_context'])

    def begin(self, cookie, csrf, model=False, demo_required=False):
        self.cleanup()
        with self.lock:
            session = self.items.get(cookie)
            if session is None or not hmac.compare_digest(session['csrf'], csrf):
                return 403, None
            now = self.clock()
            if demo_required and (session['demo_context'] is None or session['demo_context']['expires'] <= now or session['demo_context']['scope'] not in ('A', 'B')):
                return 409, None
            session['runs'] = [stamp for stamp in session['runs'] if stamp > now-60]
            session['model_runs'] = [stamp for stamp in session['model_runs'] if stamp > now-60]
            if model and (len(session['model_runs']) >= 3 or session['model_total'] >= 4):
                return 429, None
            if session['busy'] or len(session['runs']) >= 12 or len(self.runs) >= 60:
                return 429, None
            if not self.execution.acquire(blocking=False):
                return 503, None
            if model:
                session['model_runs'].append(now)
                session['model_total'] += 1
            session['busy'] = True
            session['runs'].append(now)
            self.runs.append(now)
            return 200, session

    def finish(self, session):
        with self.lock:
            session['busy'] = False
        self.execution.release()


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, *args):
        pass


def create_server(host, port, origin, sessions=None, runner=run_scenario, secure=True, model_runner=None, public_demo=False):
    if model_runner is not None and not public_demo and (host != '127.0.0.1' or secure):
        raise ValueError('Gemini adapter is currently restricted to private loopback tests')
    parsed = urlsplit(origin)
    if (parsed.scheme != ('https' if secure else 'http') or parsed.path or parsed.query or parsed.fragment
            or not parsed.netloc or parsed.username or parsed.password):
        raise ValueError('Exact public origin required')
    if not secure and (host != '127.0.0.1' or parsed.hostname != '127.0.0.1'):
        raise ValueError('HTTP test mode is loopback only')
    sessions = sessions or Sessions()
    cookie_name = COOKIE if secure else 'broker-demo-test'

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def log_message(self, *args):
            pass

        def version_string(self):
            return 'BrokerDemo'

        def send_error(self, code, message=None, explain=None):
            self.reply(code, b'{"error":"request unsupported or malformed"}')

        def reply(self, status, body, kind='application/json', nonce=None, cookie=None):
            self.send_response(status)
            for key, value in [('Content-Type', kind), ('Content-Length', str(len(body))),
                               ('Cache-Control', 'no-store'), ('Connection', 'close'),
                               ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                               ('Content-Security-Policy', "default-src 'none'; script-src 'nonce-"+(nonce or '')+
                                "'; style-src 'nonce-"+(nonce or '')+"'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")]:
                self.send_header(key, value)
            if secure:
                self.send_header('Strict-Transport-Security', 'max-age=31536000')
            if cookie:
                self.send_header('Set-Cookie', cookie_name+'='+cookie+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=900'+('; Secure' if secure else ''))
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def gate(self, post=False):
            if self.headers.get_all('Host', []) != [parsed.netloc]:
                return False
            origins = self.headers.get_all('Origin', [])
            if (post and origins != [origin]) or (origins and origins != [origin]):
                return False
            return self.headers.get('Sec-Fetch-Site') != 'cross-site'

        def cookie(self):
            values = self.headers.get_all('Cookie', [])
            if len(values) != 1 or len(values[0]) > 2048:
                return None
            # Reject ambiguous duplicate cookie names, not last-value-wins.
            if sum(part.strip().split('=', 1)[0] == cookie_name for part in values[0].split(';')) != 1:
                return None
            cookie = SimpleCookie()
            try:
                cookie.load(values[0])
                value = cookie[cookie_name].value
                return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else None
            except (KeyError, ValueError, CookieError):
                return None

        def do_GET(self):
            internal_health = (self.path == '/health' and self.client_address[0] == '127.0.0.1'
                               and self.headers.get_all('Host', []) == ['127.0.0.1:'+str(self.server.server_port)])
            if not internal_health and not self.gate():
                self.reply(403, b'{"error":"same-origin request required"}')
                return
            if self.path == '/health':
                self.reply(200, b'{"status":"ready","mode":"synthetic-only"}')
            elif self.path == '/':
                key, session = sessions.page(self.cookie())
                if session is None:
                    self.reply(503, b'{"error":"demo capacity reached; retry later"}')
                    return
                body = (Path(__file__).resolve().parent.parent/'web'/'hosted.html').read_text().replace('__NONCE__', session['csrf'])
                controls = (Path(__file__).resolve().parent.parent/'web'/'gemini_controls.html').read_text() if model_runner else ''
                controls = controls.replace('__PUBLIC_DEMO__', 'true' if public_demo else 'false')
                body = body.replace('__GEMINI_CONTROLS__', controls.replace('__NONCE__', session['csrf']))
                self.reply(200, body.encode(), 'text/html; charset=utf-8', session['csrf'], key)
            else:
                self.reply(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self.gate(post=True):
                self.reply(403, b'{"error":"same-origin request required"}')
                return
            model_request = self.path == '/api/gemini' and model_runner is not None
            start_request = self.path == '/api/demo/start' and model_runner is not None and public_demo
            if self.path != '/api/run' and not model_request and not start_request:
                self.reply(404, b'{"error":"not found"}')
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not lengths[0].isdigit() or not 1 <= int(lengths[0]) <= 128:
                    raise ValueError()
                if self.headers.get_all('Content-Type', []) != ['application/json'] or self.headers.get('Transfer-Encoding'):
                    raise ValueError()
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                value = json.loads(self.rfile.read(int(lengths[0])), object_pairs_hook=unique)
                allowed_scenarios = ({'read-a', 'read-b', 'prompt-override'} if public_demo else {'allow-a', 'allow-b', 'cross-a', 'cross-b', 'prompt-override'}) if model_request else SCENARIOS
                if start_request:
                    if not isinstance(value, dict) or set(value) != {'customer'} or value['customer'] not in ('A', 'B'):
                        raise ValueError()
                elif not isinstance(value, dict) or set(value) != {'scenario'} or not isinstance(value['scenario'], str) or value['scenario'] not in allowed_scenarios:
                    raise ValueError()
                tokens = self.headers.get_all('X-Lab-CSRF', [])
                if len(tokens) != 1 or len(tokens[0]) > 128:
                    self.reply(403, b'{"error":"valid visitor session required"}')
                    return
                status, session = sessions.begin(self.cookie(), tokens[0], model=model_request, demo_required=model_request and public_demo)
                if session is None:
                    self.reply(status, b'{"error":"session unavailable or demo busy; reload or retry later"}')
                    return
                try:
                    if start_request:
                        new_cookie, result = sessions.replace_demo(self.cookie(), session, value['customer'])
                    elif model_request:
                        context = {'token': self.cookie(), 'resolver': sessions.resolve_demo} if public_demo else {'id': session['model_subject'], 'expires': session['expires']}
                        result = model_runner(value['scenario'], context)
                    else:
                        result = runner(value['scenario'])
                    self.reply(200, json.dumps(result).encode(), cookie=new_cookie if start_request else None)
                except Exception:
                    self.reply(503, b'{"error":"execution failed closed; no result available"}')
                finally:
                    sessions.finish(session)
            except (ValueError, TypeError, UnicodeError):
                self.reply(400, b'{"error":"invalid fixed scenario"}')

    server = BoundedServer((host, port), Handler)
    server.sessions = sessions
    return server


def main():
    port = int(os.environ.get('PORT', '10000'))
    if not 1024 <= port <= 65535:
        raise SystemExit('Unprivileged PORT required')
    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.onrender\.com', hostname):
        raise SystemExit('Exact Render onrender.com hostname required; no fallback listener')
    opa = find_opa()
    if not opa:
        raise SystemExit('OPA missing; hosted startup denied')
    subprocess.run([opa, 'check', '--strict', 'policy'], check=True, timeout=5, capture_output=True)
    from .gemini_runtime import optional_model
    model_runner = optional_model(os.environ)
    sessions = Sessions()
    stop = threading.Event()
    def sweep():
        while not stop.wait(30):
            sessions.cleanup()
    threading.Thread(target=sweep, daemon=True).start()
    try:
        with create_server('0.0.0.0', port, 'https://'+hostname, sessions, model_runner=model_runner, public_demo=model_runner is not None) as server:
            print('Synthetic broker demo ready; optional Gemini '+('enabled by reviewed configuration' if model_runner else 'disabled')+'. No live Salesforce integration.', flush=True)
            server.serve_forever()
    finally:
        stop.set()


if __name__ == '__main__':
    main()
