"""Semantic chunking (AI_SYSTEM_DESIGN §5.3).

1. Pages are parsed into *blocks*: headings, paragraphs, lists, and atomic elements
   (markdown tables, fenced code, ``$$`` display equations).
2. Blocks are packed greedily into chunks of ``max_tokens``. A heading starts a new
   chunk once the current one has reached ``min_tokens`` (semantic boundary).
3. Oversized paragraphs/lists split at sentence (then word) boundaries. Atomic
   elements are never split mid-element unless larger than twice ``max_tokens``, in
   which case they split by rows/lines (tables repeat their header row).
4. Consecutive chunks inside one section share ~``overlap_tokens`` of trailing
   sentences; no overlap is taken from atomic elements or across section headings.

Metadata kept per chunk: page numbers, section heading, position index, token count.
Token counts are a provider-independent estimate (~4 chars/token).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.ai.providers.base import estimate_tokens
from app.services.content.text_extractor import PageText

BlockKind = Literal["heading", "paragraph", "list", "table", "code", "equation"]
ATOMIC: frozenset[str] = frozenset({"table", "code", "equation"})

_MD_HEADING = re.compile(r"^#{1,6}\s+(\S.*)$")
_NAMED_HEADING = re.compile(
    r"^(chapter|section|unit|part|lesson|module|topic)\s+[\dIVXLivxl]+(\.\d+)*\b[.:\-–—]?\s*\S.{0,80}$",
    re.IGNORECASE,
)
_MULTI_NUMBERED = re.compile(r"^\d+(\.\d+)+\.?\s+[A-Z][^.!?]{0,80}$")
_LIST_ITEM = re.compile(r"^(\s*)([-*•▪◦]|\d{1,3}[.)]|[a-z][.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=[\"'(\[]?[A-Z0-9$\\])")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass(slots=True)
class Block:
    kind: BlockKind
    text: str
    page: int | None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(slots=True)
class Chunk:
    index: int
    content: str
    page_numbers: list[int]
    heading: str | None
    token_count: int
    metadata: dict[str, object] = field(default_factory=dict)


# --- Block parsing ----------------------------------------------------------------------


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 100:
        return False
    if _MD_HEADING.match(s) or _NAMED_HEADING.match(s) or _MULTI_NUMBERED.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    return (
        len(letters) >= 4
        and s == s.upper()
        and len(s.split()) <= 8
        and not s.endswith((".", ",", ";"))
    )


def _heading_text(line: str) -> str:
    s = line.strip()
    m = _MD_HEADING.match(s)
    return m.group(1).strip() if m else s


class _PageParser:
    """Turns one page of text into blocks (single pass over its lines)."""

    FENCES: tuple[tuple[str, BlockKind], ...] = (("```", "code"), ("$$", "equation"))

    def __init__(self, page: PageText) -> None:
        self.page = page.page_number
        self.lines = page.text.split("\n")
        self.blocks: list[Block] = []
        self.para: list[str] = []
        self.items: list[str] = []

    def _flush(self) -> None:
        if self.para:
            self.blocks.append(Block("paragraph", " ".join(self.para), self.page))
            self.para = []
        if self.items:
            self.blocks.append(Block("list", "\n".join(self.items), self.page))
            self.items = []

    def _collect_fenced(self, i: int, fence: str, kind: BlockKind) -> int:
        body = [self.lines[i]]
        first = self.lines[i].strip()
        closed = first.endswith(fence) and len(first) > len(fence)
        i += 1
        while not closed and i < len(self.lines):
            body.append(self.lines[i])
            closed = self.lines[i].strip().endswith(fence)
            i += 1
        self.blocks.append(Block(kind, "\n".join(body).strip(), self.page))
        return i

    def _collect_table(self, i: int) -> int:
        rows = []
        while i < len(self.lines) and _TABLE_LINE.match(self.lines[i]):
            rows.append(self.lines[i].strip())
            i += 1
        self.blocks.append(Block("table", "\n".join(rows), self.page))
        return i

    def parse(self) -> list[Block]:
        i = 0
        while i < len(self.lines):
            line = self.lines[i]
            stripped = line.strip()
            fence = next(((f, k) for f, k in self.FENCES if stripped.startswith(f)), None)
            if fence is not None:
                self._flush()
                i = self._collect_fenced(i, *fence)
                continue
            if _TABLE_LINE.match(line):
                self._flush()
                i = self._collect_table(i)
                continue
            if not stripped:
                self._flush()
            elif _is_heading(stripped) and not self.items:
                self._flush()
                self.blocks.append(Block("heading", _heading_text(stripped), self.page))
            elif _LIST_ITEM.match(line):
                if self.para:
                    self._flush()
                self.items.append(stripped)
            elif self.items and line.startswith((" ", "\t")):
                self.items[-1] += " " + stripped  # wrapped list item continuation
            else:
                if self.items:
                    self._flush()
                self.para.append(stripped)
            i += 1
        self._flush()
        return self.blocks


def parse_blocks(pages: Sequence[PageText]) -> list[Block]:
    return [block for page in pages for block in _PageParser(page).parse()]


# --- Splitting oversized blocks ------------------------------------------------------------


def _split_words(text: str, max_tokens: int) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for word in text.split():
        if current and estimate_tokens(" ".join([*current, word])) > max_tokens:
            out.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        out.append(" ".join(current))
    return out


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _pack(units: Iterable[str], max_tokens: int, joiner: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for unit in units:
        if estimate_tokens(unit) > max_tokens:
            if current:
                out.append(joiner.join(current))
                current = []
            out.extend(_split_words(unit, max_tokens))
            continue
        if current and estimate_tokens(joiner.join([*current, unit])) > max_tokens:
            out.append(joiner.join(current))
            current = []
        current.append(unit)
    if current:
        out.append(joiner.join(current))
    return out


def split_block(block: Block, max_tokens: int) -> list[Block]:
    if block.tokens <= max_tokens:
        return [block]
    if block.kind in ATOMIC:
        if block.tokens <= 2 * max_tokens:
            return [block]  # keep tables / code / equations intact
        lines = block.text.split("\n")
        header: list[str] = lines[:2] if block.kind == "table" else []
        body = lines[2:] if header else lines
        parts = _pack(body, max_tokens - estimate_tokens("\n".join(header)), "\n")
        return [Block(block.kind, "\n".join([*header, p]), block.page) for p in parts]
    if block.kind == "list":
        parts = _pack(block.text.split("\n"), max_tokens, "\n")
    else:
        parts = _pack(split_sentences(block.text), max_tokens, " ")
    return [Block(block.kind, p, block.page) for p in parts]


def _overlap_tail(block: Block, overlap_tokens: int) -> str:
    """Trailing whole sentences of ``block`` totalling at most ``overlap_tokens``."""
    if overlap_tokens <= 0 or block.kind in ATOMIC or block.kind == "heading":
        return ""
    units = block.text.split("\n") if block.kind == "list" else split_sentences(block.text)
    tail: list[str] = []
    for unit in reversed(units):
        if estimate_tokens(" ".join([unit, *tail])) > overlap_tokens:
            break
        tail.insert(0, unit)
    return " ".join(tail)


# --- Packing ---------------------------------------------------------------------------------


def chunk_document(
    pages: Sequence[PageText],
    *,
    min_tokens: int = 500,
    max_tokens: int = 1000,
    overlap_tokens: int = 100,
) -> list[Chunk]:
    if not 0 < min_tokens <= max_tokens:
        raise ValueError("require 0 < min_tokens <= max_tokens")
    if not 0 <= overlap_tokens < min_tokens:
        raise ValueError("require 0 <= overlap_tokens < min_tokens")

    chunks: list[Chunk] = []
    parts: list[Block] = []
    tokens = 0
    overlap_text = ""
    heading: str | None = None
    chunk_heading: str | None = None
    pending_headings: list[Block] = []

    def flush(next_overlap: str) -> None:
        nonlocal parts, tokens, overlap_text
        content_parts = list(parts)
        if content_parts:
            texts = ([overlap_text] if overlap_text else []) + [
                (f"## {b.text}" if b.kind == "heading" else b.text) for b in content_parts
            ]
            content = "\n\n".join(texts)
            pages_ = sorted({b.page for b in content_parts if b.page is not None})
            chunks.append(
                Chunk(
                    index=len(chunks),
                    content=content,
                    page_numbers=pages_,
                    heading=chunk_heading,
                    token_count=estimate_tokens(content),
                    metadata={
                        "block_types": sorted({b.kind for b in content_parts}),
                        "overlap_tokens": estimate_tokens(overlap_text) if overlap_text else 0,
                        "char_count": len(content),
                    },
                )
            )
        parts = []
        tokens = estimate_tokens(next_overlap) if next_overlap else 0
        overlap_text = next_overlap

    for block in parse_blocks(pages):
        if block.kind == "heading":
            pending_headings.append(block)
            continue
        # Leave room for the overlap carried into a chunk so text never exceeds max_tokens.
        for piece in split_block(block, max_tokens - overlap_tokens):
            starts_section = bool(pending_headings)
            if starts_section and tokens >= min_tokens:
                flush("")  # semantic boundary: new section, no overlap across it
            head_tokens = sum(h.tokens for h in pending_headings)
            if parts and tokens + head_tokens + piece.tokens > max_tokens:
                tail = "" if starts_section else _overlap_tail(parts[-1], overlap_tokens)
                flush(tail)
            if not parts:
                chunk_heading = pending_headings[-1].text if pending_headings else heading
            for h in pending_headings:
                parts.append(h)
                tokens += h.tokens
                heading = h.text
            pending_headings = []
            parts.append(piece)
            tokens += piece.tokens

    if pending_headings:  # trailing headings with no body
        if not parts:
            chunk_heading = pending_headings[-1].text
        parts.extend(pending_headings)
    flush("")
    return chunks
