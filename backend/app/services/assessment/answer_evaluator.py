"""Answer evaluation and misconception detection (AI_SYSTEM_DESIGN §4.2, AC-4.6).

* Objective questions (MCQ, true/false) are graded exactly — no LLM, no cost, no
  variance. A wrong MCQ choice reveals the misconception its distractor was written to
  catch; when the distractor isn't tagged, the ``misconception_detection`` task diagnoses it.
* Free-text answers (short answer, worked problem, open ended) are graded by the
  ``answer_evaluation`` task against the stored correct answer, which also reports
  misconceptions.

The student's text is always placed in a delimited block and treated as data
(SECURITY_MODEL §6.1). Feedback is growth-oriented (AI_SYSTEM_DESIGN §8.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMMessage
from app.db.models import Question
from app.services.assessment.question_generator import parse_bool
from app.services.student_model.misconception_tracker import DetectedMisconception, display_name

EVAL_TASK = "answer_evaluation"
MISC_TASK = "misconception_detection"
EVAL_PROMPT = "assessment/evaluate_answer"
MISC_PROMPT = "assessment/detect_misconception"
SYSTEM = "assessment/system"
CORRECT_THRESHOLD = 0.7
MAX_RESPONSE_CHARS = 4000

_LABEL = re.compile(r"^\s*\(?([A-Za-z])\)?\s*[.):]?\s*(.*)$", re.DOTALL)


@dataclass(slots=True)
class EvaluationResult:
    is_correct: bool
    score: float
    explanation: str
    misconceptions: list[DetectedMisconception] = field(default_factory=list)
    follow_up_suggestion: str | None = None
    method: str = "exact"  # exact | llm
    selected_option: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_correct": self.is_correct,
            "score": self.score,
            "explanation": self.explanation,
            "misconceptions": [_misc_dict(m) for m in self.misconceptions],
            "follow_up_suggestion": self.follow_up_suggestion,
            "method": self.method,
            "selected_option": self.selected_option,
        }


def _misc_dict(m: DetectedMisconception) -> dict[str, Any]:
    return {
        "name": m.name,
        "description": m.description,
        "confidence": m.confidence,
        "evidence": m.evidence,
    }


class _MisconceptionOut(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    confidence: float = 0.7
    evidence: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.7

    @field_validator("description", "evidence", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return " ".join(str(v or "").split())[:500]


def _misconceptions(raw: Any) -> list[DetectedMisconception]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and "name" not in item and "misconception_name" in item:
            item = {**item, "name": item["misconception_name"]}
        try:
            m = _MisconceptionOut.model_validate(item)
        except ValidationError:
            continue
        out.append(DetectedMisconception(m.name, m.description, m.confidence, m.evidence))
    return out[:3]


class EvaluationOut(BaseModel):
    is_correct: bool
    score: float
    explanation: str = ""
    misconceptions_detected: list[Any] = Field(default_factory=list)
    follow_up_suggestion: str | None = None


def coerce_evaluation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object")
    score: float | None
    try:
        score = max(0.0, min(1.0, float(raw["score"])))
    except (KeyError, TypeError, ValueError):
        score = None
    correct = parse_bool(raw.get("is_correct")) if raw.get("is_correct") is not None else None
    if score is None and correct is None:
        raise ValueError("evaluation needs a score or is_correct")
    if score is None:
        score = 1.0 if correct else 0.0
    if correct is None:
        correct = score >= CORRECT_THRESHOLD
    suggestion = raw.get("follow_up_suggestion")
    return {
        "is_correct": correct,
        "score": round(score, 3),
        "explanation": " ".join(str(raw.get("explanation") or "").split())[:3000],
        "misconceptions_detected": raw.get("misconceptions_detected")
        or raw.get("misconceptions")
        or [],
        "follow_up_suggestion": str(suggestion)[:200] if suggestion else None,
    }


class MisconceptionOut(BaseModel):
    misconceptions: list[Any] = Field(default_factory=list)


def answer_text(question: Question) -> str:
    """The correct answer as human-readable text."""
    answer = question.correct_answer or {}
    if question.question_type == "mcq":
        return f"{answer.get('label')}) {answer.get('text')}"
    if question.question_type == "true_false":
        return "True" if answer.get("value") else "False"
    return str(answer.get("text", ""))


def match_option(question: Question, response: str) -> dict[str, Any] | None:
    options = question.options or []
    text = response.strip()
    lowered = text.lower().rstrip(".")
    for option in options:
        if lowered == str(option.get("text", "")).strip().lower().rstrip("."):
            return dict(option)
    m = _LABEL.match(text)
    if m and (not m.group(2).strip() or len(text) <= 3):
        label = m.group(1).upper()
        for option in options:
            if str(option.get("label", "")).upper() == label:
                return dict(option)
    return None


class AnswerEvaluator:
    def __init__(self, llm: LLMClient, prompts: PromptManager) -> None:
        self.llm = llm
        self.prompts = prompts

    def _messages(self, prompt: str, **variables: Any) -> tuple[list[LLMMessage], str]:
        system = self.prompts.render(SYSTEM)
        rendered = self.prompts.render(prompt, **variables)
        return [
            LLMMessage(role="system", content=system.text),
            LLMMessage(role="user", content=rendered.text),
        ], f"{system.version}+{rendered.version}"

    async def evaluate(
        self,
        question: Question,
        response: str,
        ctx: AICallContext,
        *,
        concept_name: str,
        mastery: float,
    ) -> EvaluationResult:
        response = response.strip()[:MAX_RESPONSE_CHARS]
        if question.question_type == "mcq":
            option = match_option(question, response)
            if option is not None:
                return await self._grade_option(question, option, response, ctx, concept_name)
        elif question.question_type == "true_false":
            value = parse_bool(response)
            if value is not None:
                return self._grade_true_false(question, value)
        return await self._grade_free_text(question, response, ctx, concept_name, mastery)

    def _grade_true_false(self, question: Question, value: bool) -> EvaluationResult:
        expected = bool((question.correct_answer or {}).get("value"))
        correct = value == expected
        return EvaluationResult(
            is_correct=correct,
            score=1.0 if correct else 0.0,
            explanation=_feedback(question, correct),
            selected_option="True" if value else "False",
        )

    async def _grade_option(
        self,
        question: Question,
        option: dict[str, Any],
        response: str,
        ctx: AICallContext,
        concept_name: str,
    ) -> EvaluationResult:
        correct = bool(option.get("is_correct"))
        result = EvaluationResult(
            is_correct=correct,
            score=1.0 if correct else 0.0,
            explanation=_feedback(question, correct),
            selected_option=str(option.get("label")),
        )
        if correct:
            return result
        tag = option.get("misconception")
        if tag:
            result.misconceptions = [
                DetectedMisconception(
                    name=str(tag),
                    description=display_name(str(tag)),
                    confidence=0.75,
                    evidence=(
                        f"Chose {option.get('label')}) {option.get('text')} "
                        f"instead of {answer_text(question)}"
                    ),
                )
            ]
        else:
            result.misconceptions = await self.detect_misconceptions(
                question, f"{option.get('label')}) {option.get('text')}", ctx, concept_name
            )
        return result

    async def _grade_free_text(
        self,
        question: Question,
        response: str,
        ctx: AICallContext,
        concept_name: str,
        mastery: float,
    ) -> EvaluationResult:
        if not response:
            return EvaluationResult(
                False, 0.0, "No answer was given — have a go, even a partial answer helps!"
            )
        messages, version = self._messages(
            EVAL_PROMPT,
            concept_name=concept_name,
            question=question.content,
            question_type=question.question_type,
            options=question.options or [],
            correct_answer=answer_text(question),
            reference_explanation=question.explanation or "",
            student_response=response,
            mastery=round(mastery, 2),
        )
        out = await self.llm.complete_json(
            EVAL_TASK,
            messages,
            ctx.for_purpose("evaluate_answer", prompt_name=EVAL_PROMPT, prompt_version=version),
            EvaluationOut,
            coerce=coerce_evaluation,
        )
        return EvaluationResult(
            is_correct=out.is_correct,
            score=out.score,
            explanation=out.explanation or _feedback(question, out.is_correct),
            misconceptions=_misconceptions(out.misconceptions_detected)
            if not out.is_correct or out.score < 1
            else [],
            follow_up_suggestion=out.follow_up_suggestion,
            method="llm",
        )

    async def detect_misconceptions(
        self, question: Question, wrong_answer: str, ctx: AICallContext, concept_name: str
    ) -> list[DetectedMisconception]:
        messages, version = self._messages(
            MISC_PROMPT,
            concept_name=concept_name,
            question=question.content,
            correct_answer=answer_text(question),
            student_response=wrong_answer,
        )
        out = await self.llm.complete_json(
            MISC_TASK,
            messages,
            ctx.for_purpose(
                "detect_misconception", prompt_name=MISC_PROMPT, prompt_version=version
            ),
            MisconceptionOut,
            coerce=lambda raw: raw if isinstance(raw, dict) else {"misconceptions": raw},
        )
        return _misconceptions(out.misconceptions)


def _feedback(question: Question, correct: bool) -> str:
    if correct:
        return f"Correct! {question.explanation or ''}".strip()
    reason = f" {question.explanation}" if question.explanation else ""
    return f"Not quite — the answer is {answer_text(question)}.{reason}"
