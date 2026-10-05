"""Tutor context assembly (AI_SYSTEM_DESIGN §3.2, LEARNING_ENGINE §9.2).

Everything the tutor knows about the student and the concept is packed into one text
block within a token budget, most valuable parts first:

1. the concept (always),
2. prerequisites with the student's mastery, and known misconceptions,
3. retrieved learning material (most relevant first, until the budget is spent),
4. recent session history (if it still fits, with a small buffer).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.ai.providers.base import estimate_tokens
from app.domain.common import mastery_label
from app.services.content.retriever import RetrievedSection

HISTORY_BUFFER_TOKENS = 500


@dataclass(slots=True)
class ConceptBrief:
    id: uuid.UUID
    name: str
    description: str | None = None
    difficulty: float = 0.5


@dataclass(slots=True)
class TutorContext:
    concept: ConceptBrief
    mastery: float
    student_level: str = "intermediate"
    prerequisites: list[tuple[str, float]] = field(default_factory=list)
    misconceptions: list[dict[str, str]] = field(default_factory=list)
    material: list[RetrievedSection] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @property
    def mastery_label(self) -> str:
        return mastery_label(max(0.0, min(1.0, self.mastery)))


def student_level(grade_level: str | None, difficulty_band: str | None) -> str:
    parts = [p for p in (grade_level, f"{difficulty_band}-level" if difficulty_band else None) if p]
    return ", ".join(parts) or "intermediate-level"


def render_context(ctx: TutorContext, *, max_tokens: int = 4000) -> str:
    parts: list[str] = []
    used = 0

    def add(text: str) -> None:
        nonlocal used
        parts.append(text)
        used += estimate_tokens(text)

    concept = f"Concept: {ctx.concept.name}"
    if ctx.concept.description:
        concept += f"\n{ctx.concept.description}"
    add(concept)

    if ctx.prerequisites:
        add(
            "Prerequisites and the student's mastery:\n"
            + "\n".join(
                f"- {name} ({mastery_label(level)}, {round(level * 100)}%)"
                for name, level in ctx.prerequisites
            )
        )
    if ctx.misconceptions:
        add(
            "Known misconceptions to watch for:\n"
            + "\n".join(f"- {m['name']}: {m.get('description', '')}" for m in ctx.misconceptions)
        )

    material: list[str] = []
    for section in ctx.material:
        where = section.document_title + (f" — {section.heading}" if section.heading else "")
        if section.page_numbers:
            where += f" (p. {', '.join(map(str, section.page_numbers))})"
        block = f"[{where}]\n{section.content}"
        if used + estimate_tokens(block) > max_tokens:
            break
        material.append(block)
        used += estimate_tokens(block)
    if material:
        parts.append(
            "Learning material from the student's documents:\n---MATERIAL---\n"
            + "\n\n".join(material)
            + "\n---END MATERIAL---"
        )
    else:
        parts.append("No learning material from the student's documents covers this concept.")

    if ctx.history:
        history = "Recent session history:\n" + "\n".join(f"- {h}" for h in ctx.history[-5:])
        if used + estimate_tokens(history) <= max_tokens + HISTORY_BUFFER_TOKENS:
            parts.append(history)
    return "\n\n".join(parts)


def snippets_text(material: Sequence[RetrievedSection], *, max_tokens: int = 1500) -> str:
    """Plain material excerpt for question generation."""
    out: list[str] = []
    used = 0
    for section in material:
        tokens = estimate_tokens(section.content)
        if used + tokens > max_tokens:
            break
        out.append(section.content)
        used += tokens
    return "\n\n".join(out)
