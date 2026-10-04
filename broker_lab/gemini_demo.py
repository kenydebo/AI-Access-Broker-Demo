"""USER-RUN opt-in loopback Gemini test; synthetic records only, never public Render."""
import argparse
import os
import subprocess
import threading
from .core import find_opa
from .gemini_agent import GeminiAgent, GeminiClient, MODEL
from .hosted_demo import create_server, Sessions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--approved-private-test', action='store_true', help='Confirm provider eligibility, project quota and authorized API use')
    args = parser.parse_args()
    if not args.approved_private_test:
        parser.error('Explicit approved private test required; no key loaded')
    if not 1024 <= args.port <= 65535:
        parser.error('Unprivileged loopback port required')
    if os.environ.get('RENDER') or os.environ.get('RENDER_EXTERNAL_HOSTNAME'):
        raise SystemExit('Public Render Gemini activation is disabled; eligibility/configuration review pending')
    opa = find_opa()
    if not opa:
        raise SystemExit('OPA missing; no model request enabled')
    subprocess.run([opa, 'check', '--strict', 'policy'], check=True, capture_output=True, timeout=5)
    # Only this explicitly user-run entrypoint reads a configured key, held in process memory.
    try:
        client = GeminiClient(os.environ.get('GEMINI_API_KEY', ''), os.environ.get('GEMINI_MODEL', MODEL))
    except ValueError:
        raise SystemExit('Reviewed model and server-side environment key required; values not logged') from None
    agent = GeminiAgent(client)
    sessions = Sessions()
    stop = threading.Event()
    def sweep():
        while not stop.wait(30):sessions.cleanup()
    threading.Thread(target=sweep, daemon=True).start()
    try:
        with create_server('127.0.0.1', args.port, 'http://127.0.0.1:'+str(args.port), sessions,
                           secure=False, model_runner=agent.run) as server:
            print('Private loopback Gemini test: http://127.0.0.1:'+str(args.port)+'. Synthetic data only. API usage may consume project quota.', flush=True)
            server.serve_forever()
    finally:
        stop.set()


if __name__ == '__main__':
    main()
