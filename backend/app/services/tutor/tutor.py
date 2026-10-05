"""Tutor agent (AI_SYSTEM_DESIGN §3): explanations streamed as markdown.

Scenarios map to prompt templates (§3.3):

| kind            | template                | used when                                    |
| --------------- | ----------------------- | -------------------------------------------- |
| intro           | tutor/explain_concept   | first time the concept is taught             |
| retry           | tutor/re_explain        | the student was confused / answered wrongly  |
| worked_example  | tutor/worked_example    | repeated difficulty: show, don't tell        |
| socratic        | tutor/socratic          | a fairly strong student slipped: guide them  |
| followup        | tutor/followup          | the student asked a question                 |

`stream` yields text deltas as they arrive (first token fast); `explain` collects them,
forwarding each delta to an optional async callback (Phase 5 relays them over WebSocket).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMMessage
from app.services.tutor.context_builder import TutorContext, render_context

TASK = "tutor_explanation"
SYSTEM = "tutor/system"
ExplanationKind = Literal["intro", "retry", "worked_example", "socratic", "followup"]
TEMPLATES: dict[str, str] = {
    "intro": "tutor/explain_concept",
    "retry": "tutor/re_explain",
    "worked_example": "tutor/worked_example",
    "socratic": "tutor/socratic",
    "followup": "tutor/followup",
}
OnDelta = Callable[[str], Awaitable[None]]


class Tutor:
    def __init__(
        self, llm: LLMClient, prompts: PromptManager, *, context_tokens: int = 4000
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.context_tokens = context_tokens

    def build_messages(
        self, kind: ExplanationKind, context: TutorContext, **extra: Any
    ) -> tuple[list[LLMMessage], str, str]:
        name = TEMPLATES[kind]
        variables: dict[str, Any] = {
            "concept_name": context.concept.name,
            "student_level": context.student_level,
            "mastery_label": context.mastery_label,
            "context": render_context(context, max_tokens=self.context_tokens),
        }
        defaults: dict[str, dict[str, Any]] = {
            "retry": {"previous_explanation": "", "student_error": ""},
            "worked_example": {"difficulty": round(context.concept.difficulty, 2)},
            "socratic": {
                "student_response": "",
                "learning_objective": f"understand {context.concept.name}",
            },
            "followup": {"student_question": ""},
        }
        variables |= defaults.get(kind, {}) | extra
        system = self.prompts.render(SYSTEM)
        prompt = self.prompts.render(name, **variables)
        return (
            [
                LLMMessage(role="system", content=system.text),
                LLMMessage(role="user", content=prompt.text),
            ],
            name,
            f"{system.version}+{prompt.version}",
        )

    async def stream(
        self, kind: ExplanationKind, context: TutorContext, ctx: AICallContext, **extra: Any
    ) -> AsyncIterator[str]:
        messages, name, version = self.build_messages(kind, context, **extra)
        purpose = "followup_response" if kind == "followup" else f"explanation_{kind}"
        async for delta in self.llm.stream(
            TASK, messages, ctx.for_purpose(purpose, prompt_name=name, prompt_version=version)
        ):
            yield delta

    async def explain(
        self,
        kind: ExplanationKind,
        context: TutorContext,
        ctx: AICallContext,
        *,
        on_delta: OnDelta | None = None,
        **extra: Any,
    ) -> str:
        parts: list[str] = []
        async for delta in self.stream(kind, context, ctx, **extra):
            parts.append(delta)
            if on_delta is not None:
                await on_delta(delta)
        return "".join(parts).strip()
