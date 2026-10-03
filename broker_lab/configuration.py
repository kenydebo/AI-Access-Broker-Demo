"""Non-secret server-owned configuration; examples never trigger live access."""
import json
from pathlib import Path
import re
from .core import Denied

ROOT=Path(__file__).resolve().parent.parent
EXAMPLE_PATH=ROOT/'examples'/'lab.example.json'
LOCAL_PATH=ROOT/'.local'/'lab.json'

def load_config(path=EXAMPLE_PATH, require_local=False):
    try:
        value=json.loads(Path(path).read_text())['lab']
        allowed={'mode','expected_org_id','salesforce_host','runtime_user_id','api_version','records','users','identity_status'}
        if not isinstance(value,dict) or set(value)-allowed or value.get('mode') not in ('example','local'):
            raise ValueError('invalid config')
        if require_local and value['mode']!='local':raise ValueError('live requires local config')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.my\.salesforce\.com',value['salesforce_host']):
            raise ValueError('invalid host')
        if not re.fullmatch(r'00D[A-Za-z0-9]{15}',value['expected_org_id']) or not re.fullmatch(r'005[A-Za-z0-9]{15}',value['runtime_user_id']):
            raise ValueError('invalid identity')
        if set(value['records'])!={'A','B'} or len(set(value['records'].values()))!=2 or any(not re.fullmatch(r'006[A-Za-z0-9]{15}',v) for v in value['records'].values()):
            raise ValueError('invalid records')
        if set(value['users'])!={'CustomerA','CustomerB'}:raise ValueError('invalid users')
        human_ids=[]
        for label,alias in (('CustomerA','A'),('CustomerB','B')):
            user=value['users'][label]
            if set(user)-{'salesforce_user_id','intended_delegation_records','username','human_salesforce_fixture_access'} or not re.fullmatch(r'005[A-Za-z0-9]{15}',user['salesforce_user_id']) or user['intended_delegation_records']!=[alias]:
                raise ValueError('invalid assignment')
            human_ids.append(user['salesforce_user_id'])
        if len(set(human_ids+[value['runtime_user_id']]))!=3 or not re.fullmatch(r'v[0-9]{2,3}\.0',value['api_version']):
            raise ValueError('identities must differ')
        return value
    except (OSError,KeyError,ValueError,TypeError):
        raise Denied('Non-secret lab config missing/invalid; see docs/LIVE_SALESFORCE.md. No credentials loaded.') from None

def load_live_config(path=LOCAL_PATH):
    return load_config(path,require_local=True)
