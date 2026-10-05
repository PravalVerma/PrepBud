"""Question generation (AI_SYSTEM_DESIGN §4.1, AC-4.7).

Questions are generated from the concept and its retrieved material at a target
difficulty and type, validated into a strict shape, and stored for reuse (product
assumption #9: stored questions near the target difficulty that the student has not
seen in this session are reused before paying for new ones).

Every stored question references the concept it was generated for.
"""

from __future__ import annotations

import string
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMMessage, LLMOutputError
from app.db.models import Question
from app.services.assessment.difficulty_calibrator import QUESTION_TYPES

TASK = "question_generation"
PROMPT = "assessment/generate_question"
SYSTEM = "assessment/system"
GENERATE_COUNT = 2
_TRUE = {"true", "t", "yes", "y", "1", "correct"}
_FALSE = {"false", "f", "no", "n", "0", "incorrect"}


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower().rstrip(".!")
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


class OptionOut(BaseModel):
    label: str
    text: str
    is_correct: bool = False
    misconception: str | None = None


class GeneratedQuestion(BaseModel):
    """A validated, storable question."""

    content: str = Field(min_length=3, max_length=4000)
    type: str
    difficulty: float
    correct_answer: dict[str, Any]
    explanation: str = ""
    options: list[OptionOut] | None = None
    hints: list[str] = Field(default_factory=list)
    misconceptions_tested: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in QUESTION_TYPES:
            raise ValueError(f"unknown question type {v!r}")
        return v


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit] if not isinstance(text, (dict, list)) else ""


def normalise_question(
    raw: Any, *, requested_type: str, target_difficulty: float
) -> GeneratedQuestion | None:
    """Coerce one model-produced question into a valid `GeneratedQuestion`, or drop it."""
    if not isinstance(raw, dict):
        return None
    kind = (
        str(raw.get("type") or requested_type).strip().lower().replace("-", "_").replace(" ", "_")
    )
    kind = {"multiple_choice": "mcq", "truefalse": "true_false", "true/false": "true_false"}.get(
        kind, kind
    )
    if kind not in QUESTION_TYPES:
        kind = requested_type
    try:
        difficulty = float(raw.get("difficulty", target_difficulty))
    except (TypeError, ValueError):
        difficulty = target_difficulty
    difficulty = round(max(0.0, min(1.0, difficulty)), 3)
    content = str(raw.get("content") or raw.get("question") or "").strip()
    hints = [_clean(h, 300) for h in raw.get("hints") or [] if isinstance(h, str) and h.strip()][:3]
    tested = [
        _clean(m, 120)
        for m in raw.get("misconceptions_tested") or []
        if isinstance(m, str) and m.strip()
    ][:5]
    answer = raw.get("correct_answer")
    options: list[OptionOut] | None = None

    if kind == "mcq":
        items = raw.get("options") or []
        distractors = raw.get("distractors") or []
        if (
            not items and distractors and answer
        ):  # AI_SYSTEM_DESIGN §4.1 shape: answer + distractors
            items = [{"text": answer, "is_correct": True}] + [
                d if isinstance(d, dict) else {"text": d} for d in distractors
            ]
        parsed: list[OptionOut] = []
        for item in items[:6]:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            label = string.ascii_uppercase[len(parsed)]
            text = _clean(item.get("text"), 500)
            flag = parse_bool(item.get("is_correct")) is True
            if not flag and answer is not None:
                ans = str(answer).strip()
                flag = (
                    ans.upper() == str(item.get("label", "")).strip().upper()
                    or ans.lower() == text.lower()
                )
            misconception = item.get("misconception")
            parsed.append(
                OptionOut(
                    label=label,
                    text=text,
                    is_correct=flag,
                    misconception=_clean(misconception, 120)
                    if isinstance(misconception, str) and misconception.strip()
                    else None,
                )
            )
        correct = [o for o in parsed if o.is_correct]
        if len(parsed) < 2 or len(correct) != 1:
            return None
        options = parsed
        correct_answer: dict[str, Any] = {"label": correct[0].label, "text": correct[0].text}
    elif kind == "true_false":
        value = parse_bool(answer)
        if value is None:
            return None
        correct_answer = {"value": value}
    else:
        text = _clean(answer, 2000)
        if not text:
            return None
        correct_answer = {"text": text}

    try:
        return GeneratedQuestion(
            content=content,
            type=kind,
            difficulty=difficulty,
            correct_answer=correct_answer,
            explanation=_clean(raw.get("explanation"), 3000),
            options=options,
            hints=hints,
            misconceptions_tested=tested,
        )
    except ValidationError:
        return None


class GenerationOutput(BaseModel):
    questions: list[GeneratedQuestion]


@dataclass(frozen=True, slots=True)
class QuestionRequest:
    concept_id: uuid.UUID
    concept_name: str
    concept_description: str
    target_difficulty: float
    question_type: str
    content_snippets: str = ""
    student_level: str = "intermediate"
    known_misconceptions: Sequence[dict[str, str]] = ()
    avoid: Sequence[str] = ()  # recent question texts


class QuestionGenerator:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMClient,
        prompts: PromptManager,
        *,
        reuse_window: float = 0.15,
    ) -> None:
        self.session = session
        self.llm = llm
        self.prompts = prompts
        self.reuse_window = reuse_window

    async def reusable(
        self, user_id: uuid.UUID, req: QuestionRequest, exclude: Sequence[uuid.UUID]
    ) -> Question | None:
        stmt = (
            select(Question)
            .where(
                Question.user_id == user_id,
                Question.concept_id == req.concept_id,
                Question.question_type == req.question_type,
                func.abs(Question.difficulty - req.target_difficulty) <= self.reuse_window,
            )
            .order_by(
                func.abs(Question.difficulty - req.target_difficulty), Question.created_at.desc()
            )
            .limit(1)
        )
        if exclude:
            stmt = stmt.where(Question.id.not_in(list(exclude)))
        return await self.session.scalar(stmt)

    def build_messages(self, req: QuestionRequest) -> tuple[list[LLMMessage], str]:
        system = self.prompts.render(SYSTEM)
        prompt = self.prompts.render(
            PROMPT,
            concept_name=req.concept_name,
            concept_description=req.concept_description,
            target_difficulty=req.target_difficulty,
            question_type=req.question_type,
            count=GENERATE_COUNT,
            content_snippets=req.content_snippets,
            student_level=req.student_level,
            misconceptions=list(req.known_misconceptions),
            avoid=list(req.avoid)[-5:],
        )
        return [
            LLMMessage(role="system", content=system.text),
            LLMMessage(role="user", content=prompt.text),
        ], f"{system.version}+{prompt.version}"

    async def generate(
        self,
        user_id: uuid.UUID,
        req: QuestionRequest,
        ctx: AICallContext,
        *,
        session_id: uuid.UUID | None = None,
    ) -> list[Question]:
        messages, version = self.build_messages(req)

        def coerce(raw: Any) -> dict[str, Any]:
            items = raw.get("questions") if isinstance(raw, dict) else raw
            if isinstance(raw, dict) and items is None and "content" in raw:
                items = [raw]
            if not isinstance(items, list):
                raise ValueError("expected a 'questions' array")
            valid = [
                q
                for q in (
                    normalise_question(
                        item,
                        requested_type=req.question_type,
                        target_difficulty=req.target_difficulty,
                    )
                    for item in items
                )
                if q is not None
            ]
            if not valid:
                raise ValueError("no valid questions in the response")
            return {"questions": valid}

        output = await self.llm.complete_json(
            TASK,
            messages,
            ctx.for_purpose("generate_question", prompt_name=PROMPT, prompt_version=version),
            GenerationOutput,
            coerce=coerce,
        )
        stored = []
        for q in output.questions:
            row = Question(
                user_id=user_id,
                concept_id=req.concept_id,  # always the target concept (AC-4.7)
                question_type=q.type,
                difficulty=q.difficulty,
                content=q.content,
                options=[o.model_dump() for o in q.options] if q.options else None,
                correct_answer=q.correct_answer,
                explanation=q.explanation or None,
                hints=q.hints or None,
                source_type="generated",
                metadata_={
                    "misconceptions_tested": q.misconceptions_tested,
                    "target_difficulty": req.target_difficulty,
                    "prompt_version": version,
                    "session_id": str(session_id) if session_id else None,
                },
            )
            self.session.add(row)
            stored.append(row)
        await self.session.flush()
        return stored

    async def next_question(
        self,
        user_id: uuid.UUID,
        req: QuestionRequest,
        ctx: AICallContext,
        *,
        exclude: Sequence[uuid.UUID] = (),
        session_id: uuid.UUID | None = None,
    ) -> tuple[Question, bool]:
        """A question for the request: reused when possible, else generated. ``(q, reused)``."""
        existing = await self.reusable(user_id, req, exclude)
        if existing is not None:
            return existing, True
        generated = await self.generate(user_id, req, ctx, session_id=session_id)
        fresh = [q for q in generated if q.id not in set(exclude)]
        if not fresh:  # pragma: no cover - new rows can't be excluded
            raise LLMOutputError("No new question could be generated")
        best = min(fresh, key=lambda q: abs(q.difficulty - req.target_difficulty))
        return best, False


def public_options(question: Question) -> list[dict[str, str]] | None:
    """Options as shown to the student — never reveals which one is correct."""
    if not question.options:
        return None
    return [{"label": str(o.get("label")), "text": str(o.get("text"))} for o in question.options]
