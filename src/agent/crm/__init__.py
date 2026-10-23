"""CRM adapters. The agent talks to `CRMClient` only; `make_crm` picks the implementation from .env."""

from agent.crm.client import CRMClient
from agent.crm.http import HttpCRM
from agent.crm.mock import MockCRM
from agent.crm.models import (
    AgentConfig,
    CRMError,
    CRMNotFound,
    CRMUnavailable,
    CRMValidationError,
)
from agent.crm.outbox import Outbox, ResilientCRM


def make_crm(backend: str, *, base_url: str, api_key: str, data_dir) -> CRMClient:
    if backend == "http":
        return ResilientCRM(HttpCRM(base_url, api_key), Outbox(data_dir / "outbox"))
    if backend == "mock":
        return MockCRM(store=data_dir / "mock-crm.json")
    raise ValueError(f"CRM_BACKEND must be 'mock' or 'http', got {backend!r}")


__all__ = [
    "AgentConfig",
    "CRMClient",
    "CRMError",
    "CRMNotFound",
    "CRMUnavailable",
    "CRMValidationError",
    "HttpCRM",
    "MockCRM",
    "Outbox",
    "ResilientCRM",
    "make_crm",
]
