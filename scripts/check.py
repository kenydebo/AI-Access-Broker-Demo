"""Fail-fast offline validation; no model, live config or Salesforce required."""
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from broker_lab.core import find_opa
try:import mcp
except ImportError:raise SystemExit('Install the approved pinned SDK first; MCP checks NOT RUN.')
opa=find_opa()
if not opa:raise SystemExit('Install approved OPA first; real OPA checks NOT RUN.')
root=Path(__file__).resolve().parent.parent
for command in ([sys.executable,'-m','unittest','discover','-s','tests','-v'],[opa,'check','--strict','policy']):
    subprocess.run(command,cwd=root,check=True)
print('Offline suite and strict actual OPA checks passed.')
