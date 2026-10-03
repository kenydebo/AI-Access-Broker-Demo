# Existing dependencies and manual setup

Validated on this Mac with Python 3.13.15, official MCP SDK 2.3.0, OPA 1.21.1 (darwin arm64), and already-installed Ollama model `llama3.2:3b`. The UI uses Python's standard library and plain HTML/JS: no Node build, Docker, new SDK, global install, model download or new service was added for this phase.

`requirements.review.txt` pins all 28 installed Python packages with hashes. `dependency-advisory-review.json` records the earlier exact-version PyPI/OSV review; no returned advisories at that review time is not a future safety guarantee. OPA archive hash was checked before execution, but independent attestation verification was not completed because it required an authenticated GitHub CLI. Do not treat checksum verification as independent provenance or production assurance.

## Another machine: manual installation after review

No setup command runs automatically. Review official sources and approve/download dependencies on that machine yourself. Python must be compatible with the pinned SDK (this lab verified 3.13).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary :all: -r requirements.review.txt
```

For the exact reviewed Mac Apple Silicon OPA binary:

```sh
mkdir -p .tools
curl -fL https://github.com/open-policy-agent/opa/releases/download/v1.21.1/opa_darwin_arm64 -o .tools/opa
printf 'a00a6469a0968c47c01137ed0dacaa88148be5d0eaa8524310cd371f3a98a169  .tools/opa\n' | shasum -a 256 -c -
# Only after checksum/provenance review:
chmod +x .tools/opa
```

For another architecture, use the official OPA release and verify that artifact's checksum/attestation rather than reusing this hash. Linux broker peer-identity transport is not implemented: the runnable lab currently targets macOS `getpeereid`; there are no portability claims.

Install/start Ollama and provision a tool-capable model separately if absent; this project's commands never pull models or start services. The reviewed model is `llama3.2:3b`; model tag contents can change upstream, so record your locally installed model ID when reproducing. On the prepared Mac the model ID begins `a80c4f17acd5`.

Official sources: [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [PyPI mcp](https://pypi.org/project/mcp/), [OPA releases](https://github.com/open-policy-agent/opa/releases), [Ollama downloads](https://ollama.com/download), [Ollama chat API](https://docs.ollama.com/api/chat).
