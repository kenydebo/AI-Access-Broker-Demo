"""Server-owned mapping AFTER trusted OAuth identity verification; not authentication.
No label/email/request claim proves identity. user_oauth uses this after authenticated identity verification.
The trusted adapter verifies OAuth exchange/org/user and bounds session freshness.
"""
from dataclasses import dataclass
import json
from pathlib import Path
from .core import Denied

@dataclass(frozen=True)
class HumanAssignment:
    salesforce_user_id: str
    expected_org_id: str
    intended_records: frozenset

class HumanRegistry:
    def __init__(self, path=None, configuration=None):
        from .configuration import load_config,EXAMPLE_PATH
        config=configuration if configuration is not None else load_config(path or EXAMPLE_PATH)
        self.configuration=config
        self.expected_org_id=config['expected_org_id']
        self.host=config['salesforce_host']
        self.runtime_user_id=config['runtime_user_id']
        self.assignments={user['salesforce_user_id']:HumanAssignment(user['salesforce_user_id'],
            self.expected_org_id,frozenset(user['intended_delegation_records']))
            for user in config['users'].values()}

    def for_verified_subject(self, org_id, salesforce_user_id):
        """TRUSTED ADAPTER ONLY: accepts a subject already verified by OAuth.
        This method checks registration; it does not authenticate its arguments.
        It must never be called with caller-selected labels, emails or request IDs.
        """
        if org_id!=self.expected_org_id or salesforce_user_id not in self.assignments:
            raise Denied('verified subject not registered for lab')
        return self.assignments[salesforce_user_id]
