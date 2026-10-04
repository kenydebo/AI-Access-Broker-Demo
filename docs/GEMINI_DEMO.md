# Render Gemini demo

Author: **Kehinde Bade**

The deployed demonstration uses temporary server-issued Customer A/B identities and synthetic records. It does not use Salesforce, real-person authentication, shared test passwords or a separate identity provider. Gemini was enabled on the deployed Render instance on 2026-10-04, and the live A/B scope matrix passed. Source configuration remains disabled by default until an operator explicitly opts in. The hosted tool flow is manual Gemini function calling, not MCP; local MCP/Ollama/Salesforce modes remain separate.

## Actual flow

1. The browser receives an opaque HttpOnly visitor cookie and per-session CSRF token. With Gemini enabled by the operator, Start demo accepts only the requested demo customer A or B. The server assigns the corresponding principal/scope/task, rotates the opaque cookie and CSRF token, and invalidates the old session immediately. This is demo identity establishment, not proof of a real human identity.
2. A five-minute task session is stored server-side under the new opaque browser token. The browser can request A or B or select the automatic Cross-customer access test. It cannot submit a principal, scope, task, model URL, fields or free-form prompt on the agent route. Selecting another customer is possible only by explicitly starting a replacement session; it does not inherit the old identity or action grants. Browser request budgets are retained across this switch.
3. The server resolves the cookie to the authoritative demo identity before invoking Gemini. Gemini receives a fixed prompt and one narrowly typed `read_record` declaration. A proposal is not permission. One valid call for the exact requested record and Id/Name projection can proceed. Parallel, additional or altered calls are rejected.
4. The broker maps the resolved principal to the server-owned A/B scope and evaluates the existing Rego with simulated context. The opaque session token is resolved again during both grant issuance and redemption. An expired, unknown, replaced or changed token fails closed. A random 60-second single-use action grant binds identity/session, request, audience and policy context; it stays on the server. Policy is rechecked and consumption is atomic before the synthetic read.
5. Only a broker-authorized synthetic result, or a generic denial, is supplied to Gemini for a final text turn. Final text is untrusted and separated from result evidence. The page displays actual session/model/host/OPA/grant/redemption events, safe policy context, policy reasons, observed Rego-source hash and the downstream counter. No session token, grant, API key, internal thought or thought signature is shown. The fixed synthetic user prompt and system instruction, bounded non-thought model text, and allowlisted tool arguments are available as execution evidence; none establish identity or permission. Unsupported arguments are redacted.

The Rego and request-schema naming retain the existing broker lab's `salesforce-read` audience and synthetic record contract for compatibility. Those names do not enable a Salesforce adapter, credential or network call. The public container excludes live Salesforce/OAuth/MCP/Ollama modules. Existing local enterprise modes are preserved separately.

## Deployment configuration

The existing service can use the same Dockerfile, Free plan, port and `/health` path. The Gemini path adds only standard-library modules and one static UI fragment to the strictly allowlisted image. It installs no Python package. With the following settings absent, the public scripted scenarios continue and no Gemini key is inspected:

| Setting | Reviewed activation value |
| --- | --- |
| `BROKER_GEMINI_ENABLED` | `1` |
| `BROKER_GEMINI_ACTIVATION_APPROVED` | `synthetic-audience-reviewed` |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `GEMINI_API_KEY` | User-entered private Render environment secret |

The acknowledgement is an operator enable switch, not authentication, geolocation or automatic terms compliance. Unsupported or incomplete enabled configuration stops startup before a provider request. A key alone does not enable the feature. Setting `BROKER_GEMINI_ENABLED=0` disables it again.

The user approved activation and the deployed instance is enabled. The key was supplied privately through Render; it is not part of the source or evidence. The hosted code calls Google's model rather than running it inside Render's 512 MiB instance. This activation does not establish the project's exact free-tier quota or authorize billing changes. Any new deployment still needs operator review, private key setup, and confirmation of model access, provider limits and permitted audience.

Google's terms restrict API clients offered to EEA, Swiss or UK users to Paid Services. The user's Canadian location does not settle unrestricted global public access. No fabricated country check or compliance claim is implemented. Decide audience/eligibility and any paid-service implications before exposing the live model to everyone. The scripted public demo can remain available while that decision is pending.

## Budgets and failure behavior

One active execution globally. The application limits are:

| Boundary | Limit |
|---|---|
| Server process | 20 reserved model runs per rolling 24 hours; 4 per rolling minute |
| Visitor session | 4 admitted model runs over its absolute 15-minute lifetime; 3 per rolling minute |
| Task session | At most 5 minutes, also bounded by visitor expiry |
| Provider calls | At most 2 attempts per run; no retries |

Twenty reserved runs can make at most 40 provider attempts in that process/window. Reservations count even when a provider call fails, is safety-blocked or produces no tool call; they do not promise successful reads. Browser admission can also consume a visitor run before the process budget rejects it. Reloading, starting a new task or switching A/B retains visitor counters and the original expiry.

These budgets, identities and grants are process-memory state. A restart loses the counters and sessions, so the limits are not durable across restarts or a billing cap. They are application safeguards, **not Google's free-tier allowance**. Actual project quota was not verified for this update; inspect the selected model's request/token limits and project tier separately. Provider quota errors can still occur below the local limits.

The fixed provider URL is HTTPS with certificate verification, no redirects and no system proxy autodetection. The key is sent only in `x-goog-api-key`, never a URL. Input prompts are server-owned and below 256 characters. Responses and follow-up requests are capped at 64 KiB; output requests specify 512 tokens, parts are bounded and final displayed text is capped at 2,048 characters. Socket timeout is 12 seconds; OPA timeout is 10 seconds/evaluation. These socket bounds do not guarantee OS DNS or all slow-response wall time. No production availability or DDoS protection is claimed.

Provider safety block, no tool call, host rejection, broker denial and provider/quota failure have separate outcomes. Ordinary text is not inferred to be a refusal or evidence of access. A failed final model answer after a successful read preserves the actual read evidence. Expiration or replacement before the final turn suppresses that second provider request. Errors and logs contain no prompt, provider error body or credential.

## Reading the execution evidence

The evidence separates the trusted A/B session from the selected request target. **Cross-customer access test** automatically targets B under session A and A under session B. The prompt is a normal retrieval request: `Retrieve synthetic record B using read_record with fields Id and Name.` for A, with A substituted for B under session B. It does not ask the model to impersonate anyone, ignore permissions or change identity. The trusted identity remains the selected session's customer.

`model_response` projects the first response's safe finish/block reason, bounded non-thought text and allowlisted tool arguments. Thoughts, signatures and unsupported argument values are omitted. Text is untrusted and is never interpreted as proof of refusal or access. `broker_request` is null until a tool call passes the exact host binding. `policy_context` is null when OPA was not reached; `evaluated_policy_inputs` contains only the safe actual input projections for evaluations attempted. A valid other-record proposal reaches the broker, where the existing OPA policy denies it for task scope. Extra identity/action/field claims or a changed selected target are rejected by the host. Manual Read A/B selections remain available. Clearly labelled scripted scope tests exercise the same boundary without depending on a model proposal.

For recording, start Customer A, run Read record A, then select Cross-customer access test and run it. Show the exact sent prompt, actual tool proposal, broker decision, policy reason, read count and returned records in last-run evidence. If Gemini returns no tool call or ordinary refusal text, report that the broker was not reached; do not present it as a policy denial. A matching synthetic read returns Id/Name; a policy-denied other-record request returns no record. The reverse test uses Customer B and automatically targets A. The provider remains in AUTO tool mode; no tool call or output is forced or fabricated.

The next-request preview is separate from the last-run evidence. It identifies the selected customer, the active server-issued demo customer, the intended target and the fixed safe prompt. An approximate browser countdown disables Run when the task expires locally; server-side expiry remains authoritative. The current CSRF token is shared by model and scripted requests after rotation, while stale pages remain rejected.

Pending and lost/malformed-response views show unknown execution and read counts. A missing browser response may follow server execution, so it does not establish zero reads. The UI does not automatically retry and requires session recovery after an unknown run. A separate collapsible model explanation displays only bounded non-thought final text and remains untrusted. Thought-marked function calls are rejected before execution; invalid thought markers cannot project public text or tools.

## Verified versus pending

On 2026-10-04 the enabled hosted page completed genuine Gemini A/B scope tests:

| Temporary session | Requested record | Broker outcome | Downstream result |
|---|---|---|---|
| A | A | Allow | Correct synthetic A record, 1 read |
| A | B | Deny | No data, 0 reads |
| B | A | Deny | No data, 0 reads |
| B | B | Allow | Correct synthetic B record, 1 read |

The earlier, now-retired hosted impersonation prompt returned `model_outcome=no_tool_call`, `broker_outcome=not_evaluated`, `authorized_data=null` and zero reads. No model text was exposed. This proves no read occurred in that run; it does **not** demonstrate a broker denial of an actual override call. That historical result is separate from the new cross-customer retrieval scenario. The new scenario may still return no tool call or a provider block; evaluate its actual trace rather than assuming a broker decision.

Live stale-tab testing verified `page_out_of_sync` and zero model API attempts. Starting a session in another tab rotates the shared cookie while the original page retains its old CSRF token. Reload that page before continuing. Separate messages cover missing/expired tasks, visitor exhaustion, request rate limits and busy execution; none relax the checks or limits.

All 133 offline tests and strict actual installed OPA checks passed for the admission diagnostic update. Gemini HTTPS responses are mocked in those offline tests. They cover the A/B combinations, token rotation and expiry, session/grant separation, identity-change budget retention, no token disclosure, policy rechecks, injected prompt override, malformed/parallel calls, provider failures and unchanged scripted scenarios. Disabled-mode tests prove no key inspection occurs when activation is off. Those offline checks are separate from the live results above.

Pending: exact Google project/model quota verification, a bounded live check of the new automatic cross-customer retrieval scenario, and separate private validation of the complete model/MCP/live Salesforce path. No production authentication, durable audit/budget guarantee or production assurance is claimed. No Salesforce credentials or callback are needed for this hosted synthetic demo.

Sources checked 2026-10-03: [Gemini model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [REST generateContent contract](https://ai.google.dev/api/generate-content), [key handling](https://ai.google.dev/gemini-api/docs/api-key), [project rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [terms](https://ai.google.dev/gemini-api/terms), [Render environment configuration](https://render.com/docs/configure-environment-variables).
