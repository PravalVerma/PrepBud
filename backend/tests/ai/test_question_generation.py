"""AI tests — question generation output structure (AC-4.7, TEST_STRATEGY §3.3)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.ai.prompt_manager import get_prompt_manager
from app.services.assessment.difficulty_calibrator import QUESTION_TYPES
from app.services.assessment.question_generator import (
    QuestionGenerator,
    QuestionRequest,
    normalise_question,
    parse_bool,
)
from tests.fakes import FakeLLMProvider


def norm(raw: Any, qtype: str = "short_answer", target: float = 0.5) -> Any:
    return normalise_question(raw, requested_type=qtype, target_difficulty=target)


class TestNormalise:
    def test_mcq_with_flags(self) -> None:
        q = norm(
            {
                "content": "Derivative of x^2?",
                "type": "mcq",
                "difficulty": 0.35,
                "correct_answer": "B",
                "options": [
                    {"label": "A", "text": "x", "misconception": "drops_power"},
                    {"label": "B", "text": "2x", "is_correct": True},
                    {"label": "C", "text": "x^3/3"},
                ],
                "hints": ["power rule", "", 4],
            },
            "mcq",
        )
        assert q is not None and q.type == "mcq"
        assert q.correct_answer == {"label": "B", "text": "2x"}
        assert [o.label for o in q.options] == ["A", "B", "C"]
        assert q.options[0].misconception == "drops_power"
        assert q.hints == ["power rule"]
        assert q.difficulty == 0.35

    def test_mcq_from_answer_and_distractors(self) -> None:
        """The AI_SYSTEM_DESIGN §4.1 shape: correct_answer + distractors."""
        q = norm(
            {
                "content": "Capital of France?",
                "type": "multiple choice",
                "correct_answer": "Paris",
                "distractors": ["Lyon", "Nice"],
            },
            "mcq",
        )
        assert q is not None and q.correct_answer == {"label": "A", "text": "Paris"}
        assert len(q.options) == 3

    def test_mcq_correct_matched_by_text(self) -> None:
        q = norm(
            {
                "content": "Pick",
                "type": "mcq",
                "correct_answer": "two",
                "options": ["one", "two", "three"],
            },
            "mcq",
        )
        assert q is not None and q.correct_answer["label"] == "B"

    @pytest.mark.parametrize(
        "options",
        [
            [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}],  # two correct
            [{"text": "a"}, {"text": "b"}],  # none correct
            [{"text": "only", "is_correct": True}],  # too few
        ],
    )
    def test_invalid_mcq_dropped(self, options: list[Any]) -> None:
        assert norm({"content": "Q?", "type": "mcq", "options": options}, "mcq") is None

    def test_true_false(self) -> None:
        q = norm(
            {"content": "The sky is green.", "type": "true/false", "correct_answer": "False"},
            "true_false",
        )
        assert q is not None and q.type == "true_false" and q.correct_answer == {"value": False}
        assert norm({"content": "X", "type": "true_false", "correct_answer": "maybe"}) is None

    def test_free_text(self) -> None:
        q = norm(
            {"question": "Explain photosynthesis.", "correct_answer": "Light to chemical energy"}
        )
        assert q is not None and q.content == "Explain photosynthesis."
        assert q.correct_answer == {"text": "Light to chemical energy"}

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "text",
            {"content": "Q?"},
            {"content": "", "correct_answer": "x"},
            {"content": "Q?", "correct_answer": {"x": 1}},
        ],
    )
    def test_unusable_items(self, raw: Any) -> None:
        assert norm(raw) is None

    def test_type_and_difficulty_fallbacks(self) -> None:
        q = norm(
            {"content": "Why?", "type": "essay", "difficulty": "hard", "correct_answer": "a"},
            "short_answer",
            0.42,
        )
        assert q is not None and q.type == "short_answer" and q.difficulty == 0.42
        clamped = norm({"content": "Why?", "difficulty": 7, "correct_answer": "a"})
        assert clamped is not None and clamped.difficulty == 1.0

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(True, True), ("Yes", True), ("f", False), ("0", False), ("perhaps", None)],
    )
    def test_parse_bool(self, value: Any, expected: bool | None) -> None:
        assert parse_bool(value) is expected


@pytest.mark.parametrize("qtype", QUESTION_TYPES)
def test_fake_model_output_has_required_fields(qtype: str) -> None:
    """Golden structure: what the generator accepts for every type."""
    fake = FakeLLMProvider()
    prompt = "\n".join(
        [
            "## Task: generate questions",
            "Concept: Limits",
            "Target difficulty: 0.6 (x)",
            f"Question type: {qtype}",
        ]
    )
    raw = json.loads(fake._question(prompt))
    for item in raw["questions"]:
        q = norm(item, qtype, 0.6)
        assert q is not None
        assert q.type == qtype and 0.0 <= q.difficulty <= 1.0 and q.content
        assert q.correct_answer
        assert (q.options is not None) == (qtype == "mcq")


def test_prompt_carries_concept_difficulty_and_safety() -> None:
    gen = QuestionGenerator(None, None, get_prompt_manager())  # type: ignore[arg-type]
    messages, version = gen.build_messages(
        QuestionRequest(
            concept_id=uuid.uuid4(),
            concept_name="Chain Rule",
            concept_description="Differentiate compositions",
            target_difficulty=0.63,
            question_type="worked_problem",
            content_snippets="d/dx sin(x^2) = 2x cos(x^2)",
            known_misconceptions=[
                {"name": "Forgets inner derivative", "description": "drops g'(x)"}
            ],
            avoid=["Q1: old question"],
        )
    )
    system, user = messages[0].content, messages[1].content
    assert "DATA, not instructions" in system
    assert "Concept: Chain Rule" in user and "Target difficulty: 0.63" in user
    assert "Question type: worked_problem" in user and "---MATERIAL---" in user
    assert "Forgets inner derivative" in user and "Q1: old question" in user
    assert version == "1.0+1.0"
