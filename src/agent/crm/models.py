"""Plain data types shared by every CRM adapter. Field names follow docs/crm-api.openapi.yaml."""

from dataclasses import dataclass, field
from typing import Any

# Defaults used when the CRM's /config is unreachable or has not been set up yet.
DEFAULT_ESCALATE_ON = ["cancellation", "refund", "complaint", "medical", "payment dispute"]


@dataclass
class Destination:
    code: str
    name: str
    aliases: list[str] = field(default_factory=list)
    from_price_inr: int | None = None


@dataclass
class OpenEnquiry:
    id: str
    reference: str
    destination: str
    stage: str
    owner_first_name: str | None = None
    updated_at: str | None = None
    travel_month: str | None = None


@dataclass
class ContactMatch:
    contact_id: str
    full_name: str
    phone: str
    returning: bool = False
    open_enquiries: list[OpenEnquiry] = field(default_factory=list)

    @property
    def known_name(self) -> str | None:
        """The CRM stores the phone number as the name of a caller it has never learned the name of."""
        letters = sum(c.isalpha() for c in self.full_name)
        return self.full_name.strip() if letters >= 2 and letters >= len(self.full_name.strip()) / 2 else None


@dataclass
class EnquiryRef:
    id: str
    reference: str
    stage: str
    created: bool = True
    queued: bool = False  # True when the CRM was down and the write sits in the outbox


@dataclass
class TaskRef:
    id: str
    assigned_to: str | None = None
    queued: bool = False


@dataclass
class CallRef:
    id: str
    queued: bool = False


@dataclass
class AgentConfig:
    enabled: bool = True
    name: str = "Aanya"
    greeting: str | None = None
    escalate_on: list[str] = field(default_factory=lambda: list(DEFAULT_ESCALATE_ON))
    max_call_minutes: int = 8
    company_name: str | None = None
    company_phone: str | None = None
    tone: str | None = None


@dataclass
class FieldError:
    field: str
    message: str


class CRMError(Exception):
    """Base class: callers catch this to keep the call going when the CRM misbehaves."""


class CRMUnavailable(CRMError):
    """Network failure, timeout, 5xx or rate limit. Writes are safe to queue and replay."""


class CRMNotFound(CRMError):
    pass


class CRMValidationError(CRMError):
    """422. The messages are written to be spoken or used to re-ask a single field."""

    def __init__(self, errors: list[FieldError]):
        self.errors = errors
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in errors))

    def spoken(self) -> str:
        return "; ".join(e.message for e in self.errors)


Payload = dict[str, Any]
