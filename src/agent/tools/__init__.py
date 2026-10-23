"""Tool definitions (what the model sees) and their registration on the Pipecat LLM service."""

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from agent.tools.lead_tools import SET_FIELDS, LeadTools, call_tool

_STR = {"type": "string"}

TOOL_SCHEMAS = [
    FunctionSchema(
        name="identify_trip",
        description="Find which destination the caller means from their words, e.g. 'backwaters' or 'Phuket'. Call it as soon as they mention a place.",
        properties={"description": {**_STR, "description": "The caller's words about the place or trip"}},
        required=["description"],
    ),
    FunctionSchema(
        name="set_lead_field",
        description=f"Save one detail the caller just told you. Pass their words as value. name is one of: {SET_FIELDS}. Returns what to ask next.",
        properties={
            "name": {**_STR, "description": f"One of: {SET_FIELDS}"},
            "value": {**_STR, "description": "What the caller said, in their words"},
        },
        required=["name", "value"],
    ),
    FunctionSchema(
        name="get_missing_fields",
        description="What is still needed. When everything is collected it returns the summary to read back to the caller.",
        properties={},
        required=[],
    ),
    FunctionSchema(
        name="submit_lead",
        description="Send the details to our team. Only after you read the summary back and the caller clearly said yes.",
        properties={
            "caller_confirmed": {"type": "boolean", "description": "true only if the caller said yes to the read-back"},
            "update_existing": {"type": "boolean", "description": "Only when the tool asks: true to add to their existing enquiry, false to start a new one"},
        },
        required=["caller_confirmed"],
    ),
    FunctionSchema(
        name="schedule_callback",
        description="Book a call from a human consultant when the caller asks for one or you cannot finish.",
        properties={
            "when": {**_STR, "description": "When the caller wants the call, in their words, e.g. 'tomorrow evening'"},
            "reason": {**_STR, "description": "Why, in a few words"},
        },
        required=["when"],
    ),
    FunctionSchema(
        name="handoff_to_human",
        description="Hand over to a human consultant. Use at once for cancellation, refund, complaint, medical issue, payment problem or visa rejection.",
        properties={"reason": {**_STR, "description": "What the caller raised, in a few words"}},
        required=["reason"],
    ),
]


def tools_schema() -> ToolsSchema:
    return ToolsSchema(standard_tools=list(TOOL_SCHEMAS))


def register_tools(llm, tools: LeadTools) -> None:
    def make(name: str):
        async def handler(params: FunctionCallParams) -> None:
            await params.result_callback(await call_tool(tools, name, dict(params.arguments or {})))

        return handler

    for schema in TOOL_SCHEMAS:
        llm.register_function(schema.name, make(schema.name))
