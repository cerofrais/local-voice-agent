"""Pipeline step between the user aggregator and the LLM: enforces the escalation guardrail in code.

When a caller raises cancellation, refund, a complaint, a medical issue or a payment problem, the urgent
hand-off task is created right away and the model is told how to answer, whether or not it would have
called handoff_to_human by itself. See CallSession.guard_user_text.
"""

from typing import Any

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.call.session import CallSession


def apply_guard(session: CallSession, context: Any, guarded_upto: int) -> int:
    """Inspect the newest caller message once. Returns the new 'already looked at' message count."""
    messages = context.messages
    if len(messages) <= guarded_upto:
        return guarded_upto
    last = messages[-1]
    if isinstance(last, dict) and last.get("role") == "user" and isinstance(last.get("content"), str):
        instruction = session.guard_user_text(last["content"])
        if instruction:
            logger.info("escalation topic detected; hand-off started and the reply is constrained")
            context.add_message({"role": "developer", "content": instruction})
    return len(context.messages)


class EscalationGuard(FrameProcessor):
    def __init__(self, session: CallSession, **kwargs):
        super().__init__(**kwargs)
        self._session = session
        self._guarded_upto = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and not getattr(frame, "speculation", False):
            self._guarded_upto = apply_guard(self._session, frame.context, self._guarded_upto)
        await self.push_frame(frame, direction)
