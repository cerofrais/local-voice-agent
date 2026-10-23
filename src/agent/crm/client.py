"""The only CRM surface the agent code depends on (docs/07 section 5).

Lead payloads are plain dicts using the API's snake_case field names; `LeadForm` is what builds them
and validates them in code, so the LLM never writes a payload.
"""

from typing import Protocol

from agent.crm.models import (
    AgentConfig,
    CallRef,
    ContactMatch,
    Destination,
    EnquiryRef,
    OpenEnquiry,
    Payload,
    TaskRef,
)


class CRMClient(Protocol):
    async def get_config(self) -> AgentConfig: ...

    async def lookup_contact(self, phone: str) -> ContactMatch | None: ...

    async def list_open_enquiries(self, contact_id: str) -> list[OpenEnquiry]: ...

    async def list_destinations(self) -> list[Destination]: ...

    async def get_enquiry(self, enquiry_id: str) -> Payload: ...

    async def create_enquiry(self, lead: Payload, *, idempotency_key: str) -> EnquiryRef: ...

    async def update_enquiry(
        self, enquiry_id: str, patch: Payload, *, idempotency_key: str
    ) -> EnquiryRef: ...

    async def add_note(self, enquiry_id: str, text: str, *, idempotency_key: str) -> None: ...

    async def add_task(self, task: Payload, *, idempotency_key: str) -> TaskRef: ...

    async def log_call(self, call: Payload, *, idempotency_key: str) -> CallRef: ...
