"""Concept extraction from document chunks (AI_SYSTEM_DESIGN §5.2, ADR-007).

Chunks are grouped into batches of ~``extraction_batch_tokens`` and sent to the
``concept_extraction`` LLM task, several batches concurrently. Model output is
parsed leniently (invalid items are dropped, not fatal) and merged across batches
by normalised name. Each batch result is cached in Redis keyed by a hash of the
prompt version + batch text, so a retried task does not pay for the same calls twice.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from redis.asyncio import Redis

from app.ai.cost_tracker import AICallContext, sha256
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMMessage
from app.config import ContentProcessingSettings
from app.core.logging import get_logger
from app.services.content.chunker import Chunk

logger = get_logger(__name__)

TASK = "concept_extraction"
SYSTEM_PROMPT = "content/system"
EXTRACT_PROMPT = "content/extract_concepts"

DIFFICULTY_LEVELS = {
    "low": 0.3,
    "easy": 0.3,
    "medium": 0.5,
    "moderate": 0.5,
    "high": 0.75,
    "hard": 0.75,
}
RELATED_TYPES = ("related", "generalisation", "specialisation")
_TYPE_ALIASES = {"generalization": "generalisation", "specialization": "specialisation"}

# --- Name normalisation ----------------------------------------------------------------

_KEEP = re.compile(r"[^\w\s+#]")
_ARTICLE = re.compile(r"^(the|a|an)\s+")


def normalize_name(name: str) -> str:
    """Key used to recognise the same concept across batches and documents.

    Case/accents/punctuation-insensitive, ignores a leading article and a simple
    plural on the last word ("The Quadratic Equations" == "quadratic equation").
    Keeps ``+``/``#`` so "C++" and "C#" stay distinct from "C".
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = _KEEP.sub(" ", text)
    text = _ARTICLE.sub("", " ".join(text.split()))
    words = text.split()
    if words:
        last = words[-1]
        if len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")):
            words[-1] = last[:-1]
    return " ".join(words)


# --- Lenient output schema -------------------------------------------------------------------


def _difficulty(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return min(1.0, max(0.0, float(value)))
    if isinstance(value, str):
        key = value.strip().lower()
        if key in DIFFICULTY_LEVELS:
            return DIFFICULTY_LEVELS[key]
        try:
            return min(1.0, max(0.0, float(key)))
        except ValueError:
            pass
    return 0.5


class ExtractedRelation(BaseModel):
    concept: str = Field(min_length=1, max_length=200)
    type: str

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        t = str(v).strip().lower()
        t = _TYPE_ALIASES.get(t, t)
        if t not in RELATED_TYPES:
            raise ValueError(f"unknown relationship type {v!r}")
        return t


class ExtractedConcept(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    difficulty_estimate: float = 0.5
    prerequisites: list[str] = Field(default_factory=list)
    relationships: list[ExtractedRelation] = Field(default_factory=list)
    chunks: list[int] = Field(default_factory=list)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return " ".join(str(v).split()) if v is not None else ""

    @field_validator("difficulty_estimate", mode="before")
    @classmethod
    def _diff(cls, v: Any) -> float:
        return _difficulty(v)

    @field_validator("prerequisites", mode="before")
    @classmethod
    def _prereqs(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [" ".join(str(x).split())[:200] for x in v if isinstance(x, str) and x.strip()]

    @field_validator("relationships", mode="before")
    @classmethod
    def _rels(cls, v: Any) -> list[Any]:
        if not isinstance(v, list):
            return []
        valid = []
        for item in v:
            try:
                valid.append(ExtractedRelation.model_validate(item))
            except ValidationError:
                continue
        return valid

    @field_validator("chunks", mode="before")
    @classmethod
    def _chunks(cls, v: Any) -> list[int]:
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out


class ConceptExtractionOutput(BaseModel):
    concepts: list[ExtractedConcept] = Field(default_factory=list)


def parse_extraction(raw: Any, *, chunk_count: int, max_concepts: int) -> list[ExtractedConcept]:
    """Validate the model's JSON, dropping invalid concepts instead of failing the batch."""
    items = raw.get("concepts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("expected a 'concepts' array")
    out: list[ExtractedConcept] = []
    seen: set[str] = set()
    for item in items:
        try:
            concept = ExtractedConcept.model_validate(item)
        except ValidationError:
            continue
        key = normalize_name(concept.name)
        if not key or key in seen:
            continue
        seen.add(key)
        concept.chunks = sorted({n for n in concept.chunks if 1 <= n <= chunk_count})
        out.append(concept)
        if len(out) >= max_concepts:
            break
    return out


# --- Merged candidates --------------------------------------------------------------------


@dataclass(slots=True)
class CandidateConcept:
    """A concept found in this document, merged across batches."""

    key: str
    name: str
    description: str
    difficulty_values: list[float] = field(default_factory=list)
    prerequisites: set[str] = field(default_factory=set)  # normalised names
    related: dict[str, str] = field(default_factory=dict)  # normalised name -> type
    sections: dict[int, float] = field(default_factory=dict)  # chunk index -> relevance
    aliases: set[str] = field(default_factory=set)

    @property
    def difficulty(self) -> float:
        vals = self.difficulty_values or [0.5]
        return round(sum(vals) / len(vals), 3)


def merge_candidates(
    batches: Sequence[tuple[Sequence[Chunk], list[ExtractedConcept]]],
) -> dict[str, CandidateConcept]:
    merged: dict[str, CandidateConcept] = {}
    for chunks, concepts in batches:
        for c in concepts:
            key = normalize_name(c.name)
            cand = merged.get(key)
            if cand is None:
                cand = merged[key] = CandidateConcept(
                    key=key, name=c.name, description=c.description
                )
            elif len(c.description) > len(cand.description):
                cand.description = c.description
            if c.name != cand.name:
                cand.aliases.add(c.name)
            cand.difficulty_values.append(c.difficulty_estimate)
            cand.prerequisites.update(
                k for p in c.prerequisites if (k := normalize_name(p)) and k != key
            )
            for rel in c.relationships:
                rk = normalize_name(rel.concept)
                if rk and rk != key:
                    cand.related.setdefault(rk, rel.type)
            # Chunks the model cited are primary; otherwise link the whole batch weakly.
            cited = [chunks[n - 1] for n in c.chunks]
            for chunk in cited or chunks:
                score = 1.0 if cited else 0.5
                cand.sections[chunk.index] = max(cand.sections.get(chunk.index, 0.0), score)
    return merged


# --- Batching -----------------------------------------------------------------------------


def batch_chunks(chunks: Sequence[Chunk], max_tokens: int) -> list[list[Chunk]]:
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    tokens = 0
    for chunk in chunks:
        if current and tokens + chunk.token_count > max_tokens:
            batches.append(current)
            current, tokens = [], 0
        current.append(chunk)
        tokens += chunk.token_count
    if current:
        batches.append(current)
    return batches


class ExtractionCache:
    """Redis cache of parsed batch results (idempotent, cost-free task retries)."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl = ttl_seconds

    @staticmethod
    def key(user_id: str, fingerprint: str) -> str:
        return f"extract:{user_id}:{fingerprint}"

    async def get(self, user_id: str, fingerprint: str) -> list[ExtractedConcept] | None:
        try:
            raw = await self.redis.get(self.key(user_id, fingerprint))
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return [ExtractedConcept.model_validate(c) for c in json.loads(raw)]
        except (ValueError, ValidationError):
            return None

    async def set(self, user_id: str, fingerprint: str, concepts: list[ExtractedConcept]) -> None:
        try:
            await self.redis.set(
                self.key(user_id, fingerprint),
                json.dumps([c.model_dump() for c in concepts]),
                ex=self.ttl,
            )
        except Exception as exc:
            logger.warning("extraction cache unavailable", extra={"error": type(exc).__name__})


class ConceptExtractor:
    def __init__(
        self,
        llm: LLMClient,
        prompts: PromptManager,
        settings: ContentProcessingSettings,
        cache: ExtractionCache | None = None,
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.settings = settings
        self.cache = cache

    def build_messages(
        self, batch: Sequence[Chunk], *, subject_name: str | None, course_name: str | None
    ) -> tuple[list[LLMMessage], str]:
        system = self.prompts.render(SYSTEM_PROMPT)
        prompt = self.prompts.render(
            EXTRACT_PROMPT,
            subject_name=subject_name,
            course_name=course_name,
            max_concepts=max(3, min(15, 4 * len(batch))),
            chunks=[
                {
                    "number": i,
                    "heading": c.heading,
                    "pages": c.page_numbers,
                    "text": c.content,
                }
                for i, c in enumerate(batch, start=1)
            ],
        )
        version = f"{system.version}+{prompt.version}"
        return [
            LLMMessage(role="system", content=system.text),
            LLMMessage(role="user", content=prompt.text),
        ], version

    async def _extract_batch(
        self,
        batch: Sequence[Chunk],
        ctx: AICallContext,
        *,
        subject_name: str | None,
        course_name: str | None,
    ) -> list[ExtractedConcept]:
        messages, version = self.build_messages(
            batch, subject_name=subject_name, course_name=course_name
        )
        cfg = self.llm.task_config(TASK)
        fingerprint = sha256(
            "\x1e".join([version, cfg.provider, cfg.model, *(m.content for m in messages)])
        )
        user = str(ctx.user_id)
        if self.cache and (cached := await self.cache.get(user, fingerprint)) is not None:
            return cached

        max_concepts = max(3, min(15, 4 * len(batch)))
        output = await self.llm.complete_json(
            TASK,
            messages,
            ctx.for_purpose(
                "concept_extraction", prompt_name=EXTRACT_PROMPT, prompt_version=version
            ),
            ConceptExtractionOutput,
            coerce=lambda raw: {
                "concepts": parse_extraction(raw, chunk_count=len(batch), max_concepts=max_concepts)
            },
        )
        if self.cache:
            await self.cache.set(user, fingerprint, output.concepts)
        return output.concepts

    async def extract(
        self,
        chunks: Sequence[Chunk],
        ctx: AICallContext,
        *,
        subject_name: str | None = None,
        course_name: str | None = None,
    ) -> dict[str, CandidateConcept]:
        batches = batch_chunks(chunks, self.settings.extraction_batch_tokens)
        semaphore = asyncio.Semaphore(max(1, self.settings.extraction_concurrency))

        async def run(batch: list[Chunk]) -> tuple[list[Chunk], list[ExtractedConcept]]:
            async with semaphore:
                return batch, await self._extract_batch(
                    batch, ctx, subject_name=subject_name, course_name=course_name
                )

        tasks = [asyncio.ensure_future(run(b)) for b in batches]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            # One batch failed for good: stop paying for the others.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        merged = merge_candidates(results)
        limit = self.settings.max_concepts_per_document
        if len(merged) > limit:
            # Keep the concepts that are taught across the most material.
            ranked = sorted(merged.values(), key=lambda c: (-len(c.sections), c.key))[:limit]
            merged = {c.key: c for c in ranked}
        return merged
