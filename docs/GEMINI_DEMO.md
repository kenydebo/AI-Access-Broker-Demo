# Render Gemini demo candidate

Author: **Kehinde Bade**

The selected demonstration uses temporary server-issued Customer A/B identities and synthetic records. It does not use Salesforce, real-person authentication, shared test passwords or a separate identity provider. The goal is to show the complete session-to-agent-to-broker-to-policy flow on Render, with honest labels for the simulated identity and tool. Source publication and disabled deployment were approved on 2026-10-04; offline tests pass. Gemini activation is disabled by default; no live provider call or key access has occurred for this candidate.

## Actual flow

1. The browser receives an opaque HttpOnly visitor cookie and per-session CSRF token. With Gemini enabled by the operator, Start demo accepts only the requested demo customer A or B. The server assigns the corresponding principal/scope/task, rotates the opaque cookie and CSRF token, and invalidates the old session immediately. This is demo identity establishment, not proof of a real human identity.
2. A five-minute task session is stored server-side under the new opaque browser token. The browser can request A or B or select a fixed attempted prompt override. It cannot submit a principal, scope, task, model URL, fields or free-form prompt on the agent route. Selecting another customer is possible only by explicitly starting a replacement session; it does not inherit the old identity or action grants. Browser request budgets are retained across this switch.
3. The server resolves the cookie to the authoritative demo identity before invoking Gemini. Gemini receives a fixed prompt and one narrowly typed `read_record` declaration. A proposal is not permission. One valid call for the exact requested record and Id/Name projection can proceed. Parallel, additional or altered calls are rejected.
4. The broker maps the resolved principal to the server-owned A/B scope and evaluates the existing Rego with simulated context. The opaque session token is resolved again during both grant issuance and redemption. An expired, unknown, replaced or changed token fails closed. A random 60-second single-use action grant binds identity/session, request, audience and policy context; it stays on the server. Policy is rechecked and consumption is atomic before the synthetic read.
5. Only a broker-authorized synthetic result, or a generic denial, is supplied to Gemini for a final text turn. Final text is untrusted and separated from result evidence. The page displays actual session/model/host/OPA/grant/redemption events, safe policy context, policy reasons, observed Rego-source hash and the downstream counter. No session token, grant, API key or prompt is shown or logged.

The Rego and request-schema naming retain the existing broker lab's `salesforce-read` audience and synthetic record contract for compatibility. Those names do not enable a Salesforce adapter, credential or network call. The public container excludes live Salesforce/OAuth/MCP/Ollama modules. Existing local enterprise modes are preserved separately.

## Activation on Render, after review

The existing service can use the same Dockerfile, Free plan, port and `/health` path. The candidate adds only standard-library modules and one static UI fragment to the strictly allowlisted image. It installs no Python package. With the following settings absent, the public scripted scenarios continue and no Gemini key is inspected:

| Setting | Reviewed activation value |
| --- | --- |
| `BROKER_GEMINI_ENABLED` | `1` |
| `BROKER_GEMINI_ACTIVATION_APPROVED` | `synthetic-audience-reviewed` |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `GEMINI_API_KEY` | User-entered private Render environment secret |

The acknowledgement is an operator enable switch, not authentication, geolocation or automatic terms compliance. Unsupported or incomplete enabled configuration stops startup before a provider request. A key alone does not enable the feature. Setting `BROKER_GEMINI_ENABLED=0` disables it again.

Before activation the user must review the source, authorize publication and Render configuration, confirm project/model access and actual quota, decide the permitted audience under Google's terms, supply a key securely and authorize the first live requests. No billing change is assumed or authorized. The current public model feature is not activated. The Render-hosted code will call Google's model, not run the model inside Render's 512 MiB instance.

Google's terms restrict API clients offered to EEA, Swiss or UK users to Paid Services. The user's Canadian location does not settle unrestricted global public access. No fabricated country check or compliance claim is implemented. Decide audience/eligibility and any paid-service implications before exposing the live model to everyone. The scripted public demo can remain available while that decision is pending.

## Budgets and failure behavior

One active execution globally. Visitor cookies expire absolutely after 15 minutes; task sessions expire after five minutes. Per browser: at most three model runs/minute and four in the visitor lifetime, retained when switching A/B. Per process: at most four model runs/minute and twenty/24 hours, with at most two provider attempts per run and no retry. These in-memory bounds reset on restart and are not a durable billing cap. Configure and inspect provider project quotas independently.

The fixed provider URL is HTTPS with certificate verification, no redirects and no system proxy autodetection. The key is sent only in `x-goog-api-key`, never a URL. Input prompts are server-owned and below 256 characters. Responses and follow-up requests are capped at 64 KiB; output requests specify 512 tokens, parts are bounded and final displayed text is capped at 2,048 characters. Socket timeout is 12 seconds; OPA timeout is 10 seconds/evaluation. These socket bounds do not guarantee OS DNS or all slow-response wall time. No production availability or DDoS protection is claimed.

Provider safety block, no tool call, host rejection, broker denial and provider/quota failure have separate outcomes. Ordinary text is not inferred to be a refusal or evidence of access. A failed final model answer after a successful read preserves the actual read evidence. Expiration or replacement before the final turn suppresses that second provider request. Errors and logs contain no prompt, provider error body or credential.

## Verified versus pending

Offline tests use mocked Gemini HTTPS responses and actual installed OPA. They check the four A/B combinations, opaque token rotation, unknown/tampered/expired tokens, session and grant separation, quota retention across identity changes, no token disclosure to the model, policy rechecks, prompt override, malformed/parallel calls, provider failures and unchanged scripted scenarios. Runtime configuration tests prove disabled mode does not inspect a key.

Pending: Gemini activation approval, actual project quota/audience decision, first live model roundtrip and hosted browser testing of the enabled feature. A disabled deployment is approved; it does not authorize model calls. No Salesforce callback or credentials are required for this chosen demo. Real Salesforce validation remains separate and deferred.

Sources checked 2026-10-03: [Gemini model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [REST generateContent contract](https://ai.google.dev/api/generate-content), [key handling](https://ai.google.dev/gemini-api/docs/api-key), [project rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [terms](https://ai.google.dev/gemini-api/terms), [Render environment configuration](https://render.com/docs/configure-environment-variables).
