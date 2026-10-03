"""Interfaces only. No user login/workload authentication implementation here.
Contexts MUST come from a trusted authenticated transport adapter, never request labels.
Tests inject fake adapters; real approval cannot run until these are implemented.
"""
from dataclasses import dataclass
from typing import Protocol
from .core import Denied

@dataclass(frozen=True)
class AgentContext:
    principal: str
    human_user_id: str
    delegation_id: str

@dataclass(frozen=True)
class HumanContext:
    principal: str
    salesforce_user_id: str

class TrustedContextResolver(Protocol):
    def agent(self, transport_context: object) -> AgentContext: ...
    def human(self, transport_context: object) -> HumanContext: ...

class AuthenticationNotConfigured:
    def agent(self, transport_context):
        raise Denied('authenticated workload/delegation adapter not configured')
    def human(self, transport_context):
        raise Denied('independent human authentication adapter not configured')
