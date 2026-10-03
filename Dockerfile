# Official registry manifests inspected 2026-10-03; immutable digests, no pip/apt.
FROM openpolicyagent/opa:1.21.1-static@sha256:4675ab04ad1627f74741d2d9c5142698c79e18b7b09f192587d31d6dba20838e AS opa
FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed
COPY --from=opa /opa /usr/local/bin/opa
WORKDIR /app
COPY broker_lab/__init__.py broker_lab/core.py broker_lab/configuration.py broker_lab/hosted_demo.py /app/broker_lab/
COPY policy/broker.rego /app/policy/broker.rego
COPY examples/lab.example.json /app/examples/lab.example.json
COPY web/hosted.html /app/web/hosted.html
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000
RUN opa check --strict /app/policy
USER 10001:10001
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/health',timeout=3).read()"
CMD ["python", "-m", "broker_lab.hosted_demo"]
