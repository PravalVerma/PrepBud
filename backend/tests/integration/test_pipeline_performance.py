"""AC-3.6: processing a 50-page PDF completes within 5 minutes.

The pipeline runs for real on a generated 50-page textbook-like PDF (text extraction,
table detection, chunking, persistence, embeddings, Qdrant) with a mocked LLM that
*sleeps* for each call. Two checks:

1. Measured: the whole run, including simulated LLM latency, stays well under budget.
2. Projected: with pessimistic real-world latencies per call (``REAL_LATENCY``), the
   number of LLM calls the pipeline makes, at its configured concurrency, still fits
   in 5 minutes together with the measured non-LLM time.
"""

from __future__ import annotations

import math
import time

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concept, DocumentSection
from app.services.content.document_processor import DocumentProcessor
from tests.fakes import FakeLLMProvider, FakeQueue, make_pdf
from tests.integration.conftest import Uploader, drain
from tests.support import auth

PAGES = 50
BUDGET_SECONDS = 300
SIMULATED_LATENCY = 0.25
# Pessimistic latencies (seconds) of a hosted model for one call of each kind.
REAL_LATENCY = {"extraction": 25.0, "relationships": 40.0, "embedding": 3.0}

TOPICS = [
    "Limits", "Continuity", "Derivatives", "Chain Rule", "Product Rule", "Quotient Rule",
    "Implicit Differentiation", "Related Rates", "Optimisation", "Mean Value Theorem",
    "Integrals", "Riemann Sums", "Fundamental Theorem", "Substitution", "Integration by Parts",
    "Partial Fractions", "Improper Integrals", "Sequences", "Series", "Taylor Series",
    "Power Series", "Polar Coordinates", "Parametric Curves", "Vectors", "Dot Product",
]  # fmt: skip


def textbook_page(n: int) -> str:
    topic = TOPICS[n % len(TOPICS)]
    prior = TOPICS[(n - 1) % len(TOPICS)]
    paragraph = " ".join(
        f"In section {n}.{i} we study {topic.lower()} in depth, relating it to {prior.lower()} "
        f"through worked example {i}, careful reasoning about each step, and exercises."
        for i in range(14)
    )
    return (
        f"Chapter {n}: {topic}\n\n"
        f"Concept: {topic} | The theory of {topic.lower()} as covered in chapter {n} | "
        f"requires: {prior} | end\n\n{paragraph}\n\n{paragraph}"
    )


async def test_fifty_page_pdf_within_five_minutes(
    app: FastAPI,
    uploader: Uploader,
    processor: DocumentProcessor,
    fake_queue: FakeQueue,
    fake_llm: FakeLLMProvider,
    db: AsyncSession,
) -> None:
    pdf = make_pdf([textbook_page(n) for n in range(1, PAGES + 1)])
    fake_llm.latency_s = SIMULATED_LATENCY
    await uploader.upload(auth("perf"), pdf, filename="calculus.pdf", mime_type="application/pdf")

    start = time.monotonic()
    assert await drain(processor, fake_queue) == ["ready"]
    elapsed = time.monotonic() - start

    sections = int(await db.scalar(select(func.count()).select_from(DocumentSection)) or 0)
    concepts = int(await db.scalar(select(func.count()).select_from(Concept)) or 0)
    assert sections >= PAGES // 2
    assert concepts == len(TOPICS)

    calls = {kind: fake_llm.calls.count(kind) for kind in REAL_LATENCY}
    simulated = sum(calls.values()) * SIMULATED_LATENCY
    non_llm = max(0.0, elapsed - simulated)  # upper bound: ignores concurrency savings
    concurrency = app.state.settings.content.extraction_concurrency
    projected = (
        non_llm
        + math.ceil(calls["extraction"] / concurrency) * REAL_LATENCY["extraction"]
        + calls["relationships"] * REAL_LATENCY["relationships"]
        + calls["embedding"] * REAL_LATENCY["embedding"]
    )
    print(  # visible with -s; documents the measurement
        f"\n50-page PDF: {elapsed:.1f}s measured, {sections} sections, {concepts} concepts, "
        f"LLM calls {calls}, projected with real latency {projected:.0f}s"
    )
    # Generous: coverage tracing slows pdfplumber a lot; the projection is the real check.
    assert elapsed < BUDGET_SECONDS / 2
    assert projected < BUDGET_SECONDS
