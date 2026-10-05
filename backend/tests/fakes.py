"""Deterministic stand-ins for external AI / OCR / queue services, plus file builders.

`FakeLLMProvider` understands the two content prompts:

* concept extraction — it reads lines like
  ``Concept: Quadratic Formula | Solves ax^2+bx+c=0 | requires: Factoring``
  from each ``---CHUNK n---`` block and answers with the JSON the prompt asks for;
* relationship detection — it answers ``prerequisite`` edges for the ``requires``
  seen during extraction, and duplicates from a configurable synonym table.

Embeddings are a hashed bag-of-words (so similar texts are close), with an optional
synonym table that maps words to a shared bucket (to exercise semantic-only matches).
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
import uuid
import zlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.providers.base import (
    EmbeddingResponse,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    StreamEvent,
    estimate_tokens,
)

# "Concept: <name> | <description> [| requires: A, B] | end" — `[^|]` spans wrapped lines.
CONCEPT_LINE = re.compile(
    r"Concept:\s*(?P<name>[^|]+?)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*"
    r"(?:requires:\s*(?P<req>[^|]*?)\s*\|\s*)?end\b"
)
CHUNK_MARK = re.compile(r"---CHUNK (\d+)---")
REF_LINE = re.compile(r"^- (?P<ref>[NE]\d+): (?P<name>.+?)(?: — .*)?$", re.MULTILINE)
TEST_DIMENSIONS = 64
PROMPT_KINDS = (
    ("NEW concepts just extracted", "relationships"),
    ("---CHUNK", "extraction"),
    ("## Task: generate questions", "question"),
    ("## Task: evaluate an answer", "evaluation"),
    ("## Task: diagnose a misconception", "misconception"),
    ("## Task: summarise the session", "summary"),
)


def _line(prompt: str, prefix: str) -> str | None:
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def hash_embedding(
    text: str, dims: int = TEST_DIMENSIONS, synonyms: dict[str, str] | None = None
) -> list[float]:
    vec = [0.0] * dims
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        word = (synonyms or {}).get(word, word)
        if len(word) < 3:
            continue
        vec[zlib.crc32(word.encode()) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _norm(name: str) -> str:
    from app.services.content.concept_extractor import normalize_name

    return normalize_name(name)


@dataclass
class FakeLLMProvider(LLMProvider):
    name: str = "fake"
    latency_s: float = 0.0
    synonyms: dict[str, str] = field(default_factory=dict)  # embedding word synonyms
    duplicates: dict[str, str] = field(default_factory=dict)  # new name -> existing name
    fail_with: LLMError | None = None
    fail_times: int = 0
    embed_fail_with: LLMError | None = None
    raw_response: str | None = None
    calls: list[str] = field(default_factory=list)
    requires: dict[str, set[str]] = field(default_factory=dict)
    dims: int = TEST_DIMENSIONS
    on_complete: Callable[[list[LLMMessage]], str] | None = None
    # Phase 4 behaviour: tutor stream text, question shapes, failures by call kind.
    stream_text: str | None = None
    stream_fail_with: LLMError | None = None
    fail_kinds: dict[str, LLMError] = field(default_factory=dict)
    question_counter: int = 0

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> LLMResponse:
        prompt = messages[-1].content
        kind = next((k for marker, k in PROMPT_KINDS if marker in prompt), "other")
        self.calls.append(kind)
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.fail_with is not None and self.fail_times != 0:
            self.fail_times -= 1
            raise self.fail_with
        if kind in self.fail_kinds:
            raise self.fail_kinds[kind]
        if self.on_complete is not None:
            content = self.on_complete(messages)
        elif self.raw_response is not None:
            content = self.raw_response
        elif kind == "extraction":
            content = self._extract(prompt)
        elif kind == "relationships":
            content = self._relate(prompt)
        elif kind == "question":
            content = self._question(prompt)
        elif kind == "evaluation":
            content = self._evaluate(prompt)
        elif kind == "misconception":
            content = json.dumps(
                {
                    "misconceptions": [
                        {
                            "name": "picked_plausible_distractor",
                            "description": "Chose an answer that only looks right",
                            "confidence": 0.8,
                            "evidence": "wrong option chosen",
                        }
                    ]
                }
            )
        elif kind == "summary":
            content = "You made solid progress today. Keep going!"
        else:
            content = "{}"
        return LLMResponse(
            content=content,
            model=model,
            input_tokens=sum(estimate_tokens(m.content) for m in messages),
            output_tokens=estimate_tokens(content),
            latency_ms=int(self.latency_s * 1000),
            provider=self.name,
            finish_reason="stop",
        )

    def _extract(self, prompt: str) -> str:
        parts = CHUNK_MARK.split(prompt)
        concepts: dict[str, dict[str, Any]] = {}
        # parts = [preamble, n1, text1, n2, text2, ...]
        for number, text in zip(parts[1::2], parts[2::2], strict=False):
            body = text.split("---END OF MATERIAL---")[0]
            for m in CONCEPT_LINE.finditer(body):
                name = " ".join(m.group("name").split())
                entry = concepts.setdefault(
                    name,
                    {
                        "name": name,
                        "description": " ".join(m.group("desc").split()),
                        "difficulty_estimate": "medium",
                        "prerequisites": [],
                        "relationships": [],
                        "chunks": [],
                    },
                )
                entry["chunks"].append(int(number))
                if req := m.group("req"):
                    for r in (" ".join(x.split()) for x in req.split(",")):
                        if r and r not in entry["prerequisites"]:
                            entry["prerequisites"].append(r)
                            self.requires.setdefault(_norm(name), set()).add(_norm(r))
        return json.dumps({"concepts": list(concepts.values())})

    def _relate(self, prompt: str) -> str:
        refs = {m.group("ref"): m.group("name").strip() for m in REF_LINE.finditer(prompt)}
        by_key: dict[str, str] = {}
        for ref, name in refs.items():
            by_key.setdefault(_norm(name), ref)
        duplicates = []
        for ref, name in refs.items():
            if ref.startswith("N") and name in self.duplicates:
                target = by_key.get(_norm(self.duplicates[name]))
                if target and target.startswith("E"):
                    duplicates.append({"new": ref, "existing": target})
        relationships = []
        for ref, name in refs.items():
            if not ref.startswith("N"):
                continue
            for req in sorted(self.requires.get(_norm(name), ())):
                if (src := by_key.get(req)) is not None:
                    relationships.append(
                        {"source": src, "target": ref, "type": "prerequisite", "strength": 0.9}
                    )
        return json.dumps({"duplicates": duplicates, "relationships": relationships})

    def _question(self, prompt: str) -> str:
        concept = _line(prompt, "Concept:") or "the concept"
        qtype = _line(prompt, "Question type:") or "short_answer"
        difficulty = float((_line(prompt, "Target difficulty:") or "0.5").split()[0])
        items = []
        for _ in range(2):
            self.question_counter += 1
            n = self.question_counter
            item: dict[str, Any] = {
                "type": qtype,
                "difficulty": difficulty,
                "explanation": f"Because of how {concept} works.",
                "hints": [f"Think about {concept}.", "Look at the definition again."],
                "misconceptions_tested": ["confuses_terms"],
            }
            if qtype == "mcq":
                item |= {
                    "content": f"Q{n}: Which statement about {concept} is right?",
                    "correct_answer": "B",
                    "options": [
                        {
                            "label": "A",
                            "text": "a tempting wrong idea",
                            "is_correct": False,
                            "misconception": "confuses_terms",
                        },
                        {
                            "label": "B",
                            "text": "correct-answer",
                            "is_correct": True,
                            "misconception": None,
                        },
                        {
                            "label": "C",
                            "text": "an unrelated claim",
                            "is_correct": False,
                            "misconception": None,
                        },
                        {
                            "label": "D",
                            "text": "the opposite claim",
                            "is_correct": False,
                            "misconception": None,
                        },
                    ],
                }
            elif qtype == "true_false":
                item |= {
                    "content": f"Q{n}: True or false: {concept} is useful.",
                    "correct_answer": True,
                }
            else:
                item |= {
                    "content": f"Q{n}: Explain {concept} in your own words.",
                    "correct_answer": "correct-answer",
                }
            items.append(item)
        return json.dumps({"questions": items})

    @staticmethod
    def _evaluate(prompt: str) -> str:
        answer = (
            prompt.split("---USER INPUT---")[-1].split("---END USER INPUT---")[0].strip().lower()
        )
        if "correct-answer" in answer:
            result: dict[str, Any] = {"is_correct": True, "score": 1.0, "explanation": "Spot on!"}
        elif "partial" in answer:
            result = {"is_correct": False, "score": 0.5, "explanation": "Half right."}
        else:
            result = {"is_correct": False, "score": 0.0, "explanation": "Not yet."}
        result["misconceptions_detected"] = (
            [
                {
                    "name": "sign_error",
                    "description": "Flips the sign",
                    "confidence": 0.9,
                    "evidence": answer[:50],
                }
            ]
            if "misc" in answer
            else []
        )
        result["follow_up_suggestion"] = "practice_more"
        return json.dumps(result)

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append("stream")
        if self.stream_fail_with is not None:
            raise self.stream_fail_with
        words = (
            ["Hello", " student", "!"]
            if self.stream_text is None
            else [w + " " for w in self.stream_text.split()]
        )
        for word in words:
            yield StreamEvent(delta=word)
        yield StreamEvent(input_tokens=10, output_tokens=3, model=model)

    async def embed(
        self, texts: list[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResponse:
        self.calls.append("embedding")
        if self.embed_fail_with is not None:
            raise self.embed_fail_with
        return EmbeddingResponse(
            vectors=[hash_embedding(t, dimensions or self.dims, self.synonyms) for t in texts],
            model=model,
            input_tokens=sum(estimate_tokens(t) for t in texts),
            latency_ms=1,
            provider=self.name,
        )


class FakeOCR:
    def __init__(
        self, text: str = "Concept: Scanned Idea | Recognised by OCR | end", available: bool = True
    ) -> None:
        self.text = text
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def image_to_text(self, image: Any) -> str:
        self.calls += 1
        return self.text


@dataclass
class FakeQueue:
    documents: list[tuple[uuid.UUID, uuid.UUID, str]] = field(default_factory=list)
    reindexed: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    fail: bool = False

    async def enqueue_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, task_id: str | None = None
    ) -> str:
        if self.fail:
            raise ConnectionError("broker down")
        task_id = task_id or str(uuid.uuid4())
        self.documents.append((document_id, user_id, task_id))
        return task_id

    async def enqueue_reindex(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, countdown: int = 60
    ) -> str:
        self.reindexed.append((document_id, user_id))
        return str(uuid.uuid4())


# --- File builders ---------------------------------------------------------------------


def make_pdf(
    pages: list[str], *, table_rows: list[list[str]] | None = None, image_page: bool = False
) -> bytes:
    """A real PDF (fpdf2). ``table_rows`` adds a ruled table on the first page;
    ``image_page`` appends a page containing only an image (no text layer)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=11)
    for i, text in enumerate(pages):
        pdf.add_page()
        pdf.multi_cell(0, 6, text)
        if i == 0 and table_rows:
            pdf.ln(4)
            with pdf.table() as table:
                for row in table_rows:
                    cells = table.row()
                    for cell in row:
                        cells.cell(cell)
    if image_page:
        pdf.add_page()
        pdf.image(io.BytesIO(make_png("scanned page")), x=10, y=10, w=150)
    return bytes(pdf.output())


def make_png(text: str = "Hello OCR") -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 80), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def concept_text(name: str, description: str, requires: str | None = None, filler: int = 6) -> str:
    """A paragraph that the fake extractor recognises as teaching ``name``."""
    line = (
        f"Concept: {name} | {description}"
        + (f" | requires: {requires}" if requires else "")
        + " | end"
    )
    body = " ".join(
        f"{name} is studied by working through example {i} and checking each step carefully."
        for i in range(filler)
    )
    return f"{line}\n\n{body}"


class MemoryRecorder:
    """AIUsageRecorder stand-in that keeps interactions in memory (no DB)."""

    def __init__(self, budget_exceeded: bool = False) -> None:
        self.records: list[tuple[Any, Any]] = []
        self.traces: dict[uuid.UUID, str] = {}
        self.budget_exceeded = budget_exceeded

    async def start_trace(self, user_id: uuid.UUID, operation: str, **_: Any) -> uuid.UUID:
        trace_id = uuid.uuid4()
        self.traces[trace_id] = "started"
        return trace_id

    async def finish_trace(self, trace_id: uuid.UUID, status: str = "completed") -> None:
        self.traces[trace_id] = status

    async def record(self, ctx: Any, rec: Any) -> float:
        self.records.append((ctx, rec))
        return 0.0

    async def check_budget(self, user_id: uuid.UUID) -> None:
        from app.ai.providers.base import AIBudgetExceededError

        if self.budget_exceeded:
            raise AIBudgetExceededError("budget")
