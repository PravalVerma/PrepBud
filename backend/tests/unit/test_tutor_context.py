"""Tutor context assembly within a token budget (AI_SYSTEM_DESIGN §3.2, LEARNING_ENGINE §9.2)."""

from __future__ import annotations

import uuid

from app.ai.prompt_manager import get_prompt_manager
from app.services.content.retriever import RetrievedSection
from app.services.tutor.context_builder import (
    ConceptBrief,
    TutorContext,
    render_context,
    snippets_text,
    student_level,
)
from app.services.tutor.tutor import TEMPLATES, Tutor


def section(
    text: str, heading: str | None = "2.1 Limits", pages: list[int] | None = None
) -> RetrievedSection:
    return RetrievedSection(uuid.uuid4(), "Calculus notes", heading, pages or [], text, ("linked",))


def context(**kw: object) -> TutorContext:
    base: dict[str, object] = {
        "concept": ConceptBrief(uuid.uuid4(), "Derivatives", "Instantaneous rate of change", 0.6),
        "mastery": 0.35,
        "student_level": "11th grade",
        "prerequisites": [("Limits", 0.72)],
        "misconceptions": [{"name": "Sign error", "description": "flips the sign"}],
        "material": [section("The derivative is a limit of difference quotients.", pages=[4, 5])],
        "history": [f"event {i}" for i in range(8)],
    }
    return TutorContext(**(base | kw))  # type: ignore[arg-type]


def test_all_parts_in_priority_order() -> None:
    text = render_context(context())
    order = [
        text.index(s)
        for s in (
            "Concept: Derivatives",
            "Prerequisites",
            "Known misconceptions",
            "---MATERIAL---",
            "Recent session history",
        )
    ]
    assert order == sorted(order)
    assert "- Limits (proficient, 72%)" in text
    assert "- Sign error: flips the sign" in text
    assert "[Calculus notes — 2.1 Limits (p. 4, 5)]" in text
    assert "event 7" in text and "event 2" not in text  # last five history lines only


def test_material_respects_the_budget_most_relevant_first() -> None:
    big = [section("word " * 400, heading=f"S{i}") for i in range(5)]
    text = render_context(context(material=big), max_tokens=900)
    assert "[Calculus notes — S0]" in text and "[Calculus notes — S1]" not in text


def test_no_material_is_said_explicitly() -> None:
    text = render_context(context(material=[], prerequisites=[], misconceptions=[], history=[]))
    assert "No learning material" in text and "Prerequisites" not in text and "history" not in text


def test_history_dropped_when_over_budget() -> None:
    text = render_context(
        context(material=[section("word " * 600)], history=["x" * 9000]), max_tokens=700
    )
    assert "Recent session history" not in text


def test_student_level_and_labels() -> None:
    assert student_level("10th grade", "advanced") == "10th grade, advanced-level"
    assert student_level(None, None) == "intermediate-level"
    assert context(mastery=0.9).mastery_label == "mastered"
    assert context(mastery=1.7).mastery_label == "mastered"


def test_snippets_text_budget() -> None:
    material = [section("alpha " * 100), section("beta " * 100), section("gamma " * 1000)]
    out = snippets_text(material, max_tokens=400)
    assert "alpha" in out and "beta" in out and "gamma" not in out


def test_every_tutor_kind_renders() -> None:
    tutor = Tutor(None, get_prompt_manager())  # type: ignore[arg-type]
    for kind in TEMPLATES:
        messages, name, version = tutor.build_messages(kind, context())  # type: ignore[arg-type]
        assert messages[0].role == "system" and "Derivatives" in messages[1].content
        assert name == TEMPLATES[kind] and version == "1.0+1.0"
    retry, _, _ = tutor.build_messages(
        "retry", context(), previous_explanation="Earlier text", student_error="-2x"
    )
    assert "---PREVIOUS---" in retry[1].content and "-2x" in retry[1].content
