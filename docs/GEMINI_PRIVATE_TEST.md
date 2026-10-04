# Optional Gemini agent candidate

Author: **Kehinde Bade**

This source implements an opt-in private test path: Gemini proposes a `read_record` function call, the host validates it against the exact server-owned request, the broker evaluates actual OPA at issuance and redemption, and synthetic Salesforce returns only Id and Name. The authorized result or a generic denial is sent back to Gemini for a final text turn. The model does not authenticate anyone. A/B customer selection injects a simulated server-side identity for this lab.

**Current status:** source and offline tests only. Provider responses are mocked. No live Gemini request, real credential access, key creation, billing change or activation was performed for this candidate. Source publication and a disabled deployment were approved separately. The existing public scripted demo remains independent. The Render candidate now includes an optional server-issued demo-session mode, disabled by default; see GEMINI_DEMO.md. This document describes the separate private loopback test harness. A configured key does not silently activate anything.

## Verified API contract

On 2026-10-03 Google's official model page lists `gemini-3.5-flash-lite` and recommends current models for new projects. The optional client restricts configuration to this reviewed model. It uses the documented `v1beta/models/{model}:generateContent` REST contract with `functionDeclarations`, model `functionCall`, user `functionResponse` and a tool-disabled final turn. Bounded model parts, including `thoughtSignature` and function-call ID when present, are preserved for the response roundtrip. Live compatibility and your project's access are still unverified.

Sources: [Model identity and availability](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [REST generateContent reference](https://ai.google.dev/api/generate-content), [Function calling](https://ai.google.dev/gemini-api/docs/function-calling), [API key handling](https://ai.google.dev/gemini-api/docs/api-key).

## User activation prerequisites

1. Review the source and tests. Choose a private user-run test first. Confirm your account, region, selected model availability and actual project quota in Google AI Studio. The code does not assume a free allowance or enable billing. No paid service is approved.
2. Decide eligibility before any public availability. Google's [terms](https://ai.google.dev/gemini-api/terms) require available regions and restrict clients offered to EEA, Swiss or UK users to Paid Services. The user's Canadian location alone does not establish eligibility for every public visitor. This candidate deliberately does not invent IP geofencing. A later public or access-controlled deployment needs a separately reviewed eligibility/access configuration and provider-cost decision. The [rate-limit dashboard](https://ai.google.dev/gemini-api/docs/rate-limits) is authoritative for your project.
3. If you approve the private test and create/select a key yourself, enter it into a hidden local prompt. It is passed only in a child-process environment, never a command-line argument, file, URL or Git commit. Stop the process when done. No key is needed for offline tests. Do not paste it into chat or add it to the current Render service.
4. Separately authorize the first live Gemini calls and their possible quota/cost effects. The source is built; activation is pending. Live Salesforce validation stays separate and deferred.

Optional **user-run only**, after those approvals, from the project root with the existing approved Python/OPA environment:

```sh
.venv/bin/python - <<'PY'
import getpass, os, subprocess, sys
child_env = dict(os.environ)
child_env['GEMINI_API_KEY'] = getpass.getpass('Gemini key (hidden, private test only): ')
child_env['GEMINI_MODEL'] = 'gemini-3.5-flash-lite'
try:
    subprocess.run([sys.executable, '-m', 'broker_lab.gemini_demo',
                    '--approved-private-test', '--port', '8766'], env=child_env, check=True)
finally:
    child_env.pop('GEMINI_API_KEY', None)
PY
```

Open `http://127.0.0.1:8766`. A request is made only when you click the private Gemini button. The key is held by the server process and sent over verified TLS in `x-goog-api-key` to the fixed Google endpoint. Redirects and system proxy auto-discovery are disabled. Local HTTP cookies use a separate test name and omit Secure because the listener is loopback HTTP; this is not a hosted TLS/authentication demonstration. Render environment detection rejects this entrypoint before reading a key. No new dependency install is required.

## What the result means

Each run reports model API attempt count, safe model proposal summary, OPA decisions, broker outcome, downstream read count and `authorized_data`. Only the broker-returned data/counter is access evidence. Final `model_answer_untrusted` is displayed as text and may be inaccurate; it is never parsed as authority or result data. Thought signatures, visitor identities, grants, API keys and full prompts are not exposed or logged.

A provider safety block is `model_blocked`. Ordinary text without a call is `no_tool_call`; that is not automatically classified as a refusal or a broker denial. A valid cross-customer call reaches OPA and is broker-denied with zero reads. An attempted prompt override that changes the exact requested record is rejected by the host before OPA. If the model resists the override and proposes the original B/B request, the broker may allow it. If final model generation fails after a read, the verified read evidence stays visible and the provider error is reported separately. Parallel calls and any second tool call are rejected.

## Bounds and trust boundaries

Only five server-owned prompts are available: A/A, B/B, A/B, B/A and B/B with an attempted scope/identity override. No browser prompt, customer identity, task, record, fields, model URL, SQL, arbitrary tool, code or policy is accepted. The single declared function exposes record A/B and exactly Id/Name; the host independently requires the scenario's target and field set. One tool may execute per run. Fresh opaque TTL-60 single-use grants remain broker-side and policy/session expiry is rechecked on redemption.

The existing HTTP host/origin, CSRF, body and session checks apply to the private model route. Sessions last 15 minutes. Model runs are capped at 3/minute and 4 per visitor lifetime, with one active run globally. A process-local global budget permits at most 4 runs/minute and 20 runs/24 hours, reserving at most two provider attempts per run. Provider 429/errors do not retry. These in-memory limits reset on restart and are not a durable billing cap. Check provider project quotas independently.

Fixed prompt text is below 256 characters. Each response is capped at 64 KiB, 8 parts, 512 requested output tokens and 2,048 displayed final characters. Requests are capped at 64 KiB; unsupported or incomplete responses fail closed. Network operations use a 12-second socket timeout and OPA evaluations use at most 10 seconds each. Socket timeouts do not establish a hard bound on OS DNS resolution or every possible slow-response pattern. No production latency/availability claim is made. Logs contain no model prompt, provider error body or key.

## Tests and remaining validation

Offline tests mock HTTPS provider responses and use real installed OPA. They cover valid manual calls, all A/B combinations, thought-signature roundtrip, invalid fields/actions/identity additions, prompt scope changes, multiple/parallel calls, no-call text, safety block, malformed/incomplete/oversized output, timeout, 429, forbidden redirect, quota reservations, visitor expiry during generation, safe errors and public-mode disablement. The original hosted suite checks the public scripted scenarios remain available.

Still pending: project eligibility/quota confirmation, private activation approval, user-entered key, real model roundtrip, provider timing and a separately reviewed hosted access configuration. No Gemini-enabled Render deployment or real Salesforce execution is claimed.


## Selected hosted completion

The user selected temporary server-issued A/B demo identities and synthetic tools without Salesforce. See [the Render Gemini demo](GEMINI_DEMO.md). The customer OAuth plan is future reference only; it is not required for this selected demo.
