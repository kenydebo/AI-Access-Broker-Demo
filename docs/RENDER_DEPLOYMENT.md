# Render synthetic demo preparation

Author: **Kehinde Bade**

This synthetic deployment source is approved for publication to the demo repository and deployment to a Free Render service. Hosted verification is pending. The hosted entrypoint makes fixed scripted requests through the existing Broker and actual OPA policy. Synthetic Salesforce provides only Id and Name. By default it runs no model, MCP transport, live OAuth or Salesforce connection. The separate optional Gemini candidate is described in GEMINI_DEMO.md and stays disabled until activation approval. The local Ollama, MCP and live Salesforce modes remain available separately.

## Review and setup

1. Use the reviewed source in `kenydebo/AI-Access-Broker-Demo`. Publication and a Free Render deployment were approved on 2026-10-03. Verify the commit selected by Render before deploying.
2. Sign in to Render yourself and securely connect that public repository. Do not paste credentials into chat. Choose a Git-backed **Web Service**, Docker runtime, branch `main`, Dockerfile `./Dockerfile`, root context `.`, **Free** instance, one instance, no disks or database. Disable automatic deploys during review. No environment secrets are needed for scripted mode; optional Gemini activation requires its separately reviewed private environment key.
3. Render supplies `PORT` (default 10000) and `RENDER_EXTERNAL_HOSTNAME`. The service requires the exact generated `*.onrender.com` hostname, binds `0.0.0.0:$PORT` inside its container, and uses `/health` as the health check path. Do not set an Ollama URL or Salesforce keys. Custom domains are not supported by this candidate.
4. Create the reviewed Free synthetic Web Service. No paid plan, database, credentials or live integration is authorized. A successful build is followed by hosted health and scenario verification.

Optional user-run container check, after Docker is running and image downloads are approved:

```sh
docker build --platform linux/amd64 -t ai-broker-render-review .
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 0.1 -e RENDER_EXTERNAL_HOSTNAME=review.onrender.com \
  -p 127.0.0.1:10000:10000 ai-broker-render-review
curl -H 'Host: review.onrender.com' http://127.0.0.1:10000/health
```

The public browser requires HTTPS and secure cookies. The curl command checks health only. Render provides the TLS edge; the internal container listener is HTTP. Host/Origin checks use the configured external hostname and ignore client-supplied forwarded headers. No Mac firewall, listener or network setting must be changed for preparation. Docker is installed here but its daemon was unavailable; image build/run, Linux compatibility and the 512 MiB container memory limit have **not** been verified.

## Dependencies proposed for the build

No pip packages or apt installs are needed for this isolated hosted runtime. The Docker build will download layers for exactly these immutable registry manifests:

- Docker Official Python `3.13-slim-bookworm`, digest `sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed`, from `docker.io/library/python`.
- Official OPA `1.21.1-static`, digest `sha256:4675ab04ad1627f74741d2d9c5142698c79e18b7b09f192587d31d6dba20838e`, from `docker.io/openpolicyagent/opa`.

The official registry manifest metadata and amd64/arm64 availability were inspected on 2026-10-03. These are content pins, not an independently verified signature/attestation or vulnerability-free claim. No images were downloaded during preparation. Docker build runs a strict OPA policy check before switching to UID 10001. `.dockerignore` excludes everything by default and admits only the exact synthetic runtime files; Dockerfile includes optional Gemini standard-library modules but copies no live Salesforce/OAuth adapters, `.local`, Git history, virtual environment or credentials. The broader review archive includes local modes; it is not the Docker runtime context.

## Visitor and execution boundaries

A browser receives an opaque `__Host-` HttpOnly, Secure, SameSite=Strict cookie with a per-session CSRF token in the CSP-nonced page. Both are transient demo isolation, not genuine identity authentication. Selecting a scenario selects a server-owned simulated A/B identity and task; the client cannot submit an identity, record, fields, policy or code. Each execution gets an independent broker, fresh simulated human/executor context, grants and synthetic downstream counter. Grants never leave the server. No visitor status/result store is shared. All result text is displayed with `textContent`.

The six fixed scenarios cover A/A allow, B/B allow, A/B deny, B/A deny, expired grant and replay. Expiry advances an isolated test clock by 60 seconds instead of waiting. Replay performs one allowed read, then rejects the second redemption; its total downstream count is correctly one. OPA outage/malformed results fail closed.

Bounds: at most 128 absolute 15-minute sessions, creation budget 20/minute, 12 runs/minute per session, 60 runs/minute globally, one active run per session, one active OPA scenario, 16 request threads, backlog 16, body 128 bytes, 3-second socket timeout and 10 seconds per hosted OPA evaluation to allow for the observed 0.1 CPU Free allocation. A scenario has at most two evaluations, so OPA work is bounded at 20 seconds. The local broker retains its 3-second default. A background sweep clears expired sessions every 30 seconds; restart drops everything. Invalid/busy requests get generic responses, with no payload/access/exception logs. Application handlers contain no outbound HTTP path; this does not impose a container-level egress firewall.

The standard-library HTTP server is a bounded demonstration server, not a general production application server. These controls limit work; they do not establish DDoS protection. Anonymous callers can exhaust the global session/run budget and temporarily deny access. The Render TLS proxy, platform limits and access logs remain part of the deployment boundary. A public load test, proxy behavior and container CPU/memory measurements are still needed before claiming hosted readiness.

## Free-tier fit and limits

A synthetic Python process plus one bounded OPA process is the intended free-tier workload. The dashboard currently shows Free: 0.1 CPU, 512 MiB. This memory envelope is a target requiring measurement, not a demonstrated guarantee. A real Ollama model is excluded. Render's Free service sleeps after 15 minutes idle and may take about a minute to wake. Sessions disappear on restart. Free instance hours, bandwidth and build minutes are limited. Confirm the dashboard's current Free resources and usage policy; do not attach a payment method or select a paid plan for this task.

Sources: [Render Free services](https://render.com/docs/free), [Render web service binding and deployment](https://render.com/docs/web-services), [Render health check Host header](https://render.com/docs/health-checks), [Docker Official Python](https://hub.docker.com/_/python), [OPA container images](https://hub.docker.com/r/openpolicyagent/opa).

## Validation status

Local offline tests exercise the real installed OPA across every fixed scenario, outage denial, visitor separation, expiry, replay, strict JSON, spoofed host/origin, CSRF/cookie substitution, request budgets and safe errors. The complete lab suite and strict OPA checks must pass. Container build/run, browser over Render TLS, Render resource limits and public load behavior are not yet verified. Live Salesforce through model/UI remains outside the hosted scope and unverified.
