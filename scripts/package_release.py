"""Explicit public file allowlist. Writes a local review candidate; never publishes."""
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT=Path(__file__).resolve().parent.parent
DIRECTORIES=('broker_lab','tests','policy','examples','web','docs','scripts')
FILES=('README.md','.gitignore','request.schema.json','pdp-input.schema.json','requirements.review.txt','dependency-advisory-review.json','opa-advisory-resolution.json')
files=[ROOT/p for p in FILES]
for directory in DIRECTORIES:
    files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and p.name!='.DS_Store')
files=sorted(set(files))
forbidden=[]
local=ROOT/'.local'/'lab.json'
if local.exists():
    cfg=json.loads(local.read_text())['lab']
    forbidden.extend([cfg['salesforce_host'],cfg['expected_org_id'],cfg['runtime_user_id'],*cfg['records'].values()])
    for user in cfg['users'].values():
        forbidden.append(user['salesforce_user_id'])
        if user.get('username'):forbidden.append(user['username'])
issues=[]
for path in files:
    if path.suffix in ('.jpg','.png'):continue
    text=path.read_text()
    if any(value in text for value in forbidden) or re.search('/'+'Users/'+r'[^/\s]+/',text):
        issues.append({'file':str(path.relative_to(ROOT)),'category':'private identifier or personal path'})
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text):
        issues.append({'file':str(path.relative_to(ROOT)),'category':'private key pattern'})
if issues:
    print(json.dumps({'ready':False,'issues':issues},indent=2));raise SystemExit(1)
out=ROOT/'release';out.mkdir(exist_ok=True)
archive=out/'ai-access-broker-review.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for path in files:tar.add(path,arcname='ai-access-broker/'+str(path.relative_to(ROOT)),recursive=False)
manifest={'status':'local review candidate; not published; license pending','files':[{ 'path':str(path.relative_to(ROOT)), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in files],'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'known_identifier_scan':'passed','git_history':'no repository exists in working project'}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'archive':str(archive.relative_to(ROOT)),'files':len(files),'sha256':manifest['archive_sha256'],'known_identifier_scan':'passed','license':'pending'}))
