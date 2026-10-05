"""Semantic chunking (AI_SYSTEM_DESIGN §5.3)."""

from __future__ import annotations

import itertools

import pytest

from app.ai.providers.base import estimate_tokens
from app.services.content.chunker import (
    Block,
    chunk_document,
    parse_blocks,
    split_block,
    split_sentences,
)
from app.services.content.text_extractor import PageText


def sentences(n: int, topic: str = "quadratic equations") -> str:
    return " ".join(
        f"Sentence {i} explains a fact about {topic} and their roots." for i in range(n)
    )


def kinds(text: str) -> list[str]:
    return [b.kind for b in parse_blocks([PageText(1, text)])]


class TestParseBlocks:
    @pytest.mark.parametrize(
        "line",
        [
            "# Introduction",
            "### 2.1 Limits",
            "CHAPTER 5: QUADRATICS",
            "Chapter 3 Linear Equations",
            "Section 2.4: Vectors",
            "1.2 The Quadratic Formula",
            "THE DISCRIMINANT",
        ],
    )
    def test_headings_detected(self, line: str) -> None:
        assert kinds(f"{line}\n\nSome body text here.") == ["heading", "paragraph"]

    @pytest.mark.parametrize(
        "line",
        [
            "This is an ordinary sentence that happens to be short.",
            "A.",
            "Use 1.2 kg of flour and stir well.",
            "THE END.",
        ],
    )
    def test_ordinary_lines_are_not_headings(self, line: str) -> None:
        assert kinds(line) == ["paragraph"]

    def test_markdown_heading_text_is_stripped_of_hashes(self) -> None:
        [heading, _] = parse_blocks([PageText(1, "## Limits\nbody")])
        assert heading.text == "Limits"

    def test_paragraph_lines_are_joined(self) -> None:
        [block] = parse_blocks([PageText(3, "first line of a\nwrapped paragraph")])
        assert block == Block("paragraph", "first line of a wrapped paragraph", 3)

    def test_blank_line_separates_paragraphs(self) -> None:
        assert kinds("one\n\ntwo") == ["paragraph", "paragraph"]

    def test_lists_group_items_and_continuations(self) -> None:
        [block] = parse_blocks([PageText(1, "- apples\n- pears\n  and quinces\n1) plums")])
        assert block.kind == "list"
        assert block.text == "- apples\n- pears and quinces\n1) plums"

    def test_table_code_and_equation_blocks(self) -> None:
        text = (
            "intro\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
            "```python\nx = 1\n\ny = 2\n```\n"
            "$$\nx = \\frac{-b}{2a}\n$$\n"
            "$$ E = mc^2 $$\noutro"
        )
        assert kinds(text) == ["paragraph", "table", "code", "equation", "equation", "paragraph"]
        blocks = parse_blocks([PageText(1, text)])
        assert blocks[2].text == "```python\nx = 1\n\ny = 2\n```"  # blank line kept inside code

    def test_unterminated_fence_runs_to_end_of_page(self) -> None:
        assert kinds("```\ncode without end") == ["code"]

    def test_page_numbers_follow_blocks(self) -> None:
        blocks = parse_blocks([PageText(1, "alpha"), PageText(2, "beta")])
        assert [b.page for b in blocks] == [1, 2]


class TestSplitting:
    def test_sentence_split(self) -> None:
        assert split_sentences("One fact. Two facts! Three? four") == [
            "One fact.",
            "Two facts!",
            "Three? four",
        ]

    def test_small_block_untouched(self) -> None:
        block = Block("paragraph", "short", 1)
        assert split_block(block, 100) == [block]

    def test_long_paragraph_splits_on_sentences_within_budget(self) -> None:
        parts = split_block(Block("paragraph", sentences(80), 1), 200)
        assert len(parts) > 1
        assert all(p.tokens <= 200 for p in parts)
        assert all(p.text.endswith("roots.") for p in parts)

    def test_giant_sentence_falls_back_to_words(self) -> None:
        parts = split_block(Block("paragraph", "word " * 2000, 1), 100)
        assert all(p.tokens <= 100 for p in parts)

    def test_atomic_block_kept_intact_up_to_twice_budget(self) -> None:
        table = "\n".join(["| h1 | h2 |", "| --- | --- |"] + [f"| {i} | {i} |" for i in range(40)])
        block = Block("table", table, 1)
        assert block.tokens > 100
        assert split_block(block, block.tokens - 10) == [block]

    def test_huge_table_splits_by_rows_repeating_header(self) -> None:
        rows = [f"| row {i} value | {i * i} |" for i in range(300)]
        table = "\n".join(["| name | square |", "| --- | --- |", *rows])
        parts = split_block(Block("table", table, 1), 200)
        assert len(parts) > 2
        for part in parts:
            assert part.text.startswith("| name | square |\n| --- | --- |")
            assert part.kind == "table"
        body_rows = [line for p in parts for line in p.text.split("\n")[2:]]
        assert body_rows == rows  # nothing lost, nothing duplicated


class TestChunkDocument:
    def test_respects_token_budget(self) -> None:
        pages = [PageText(i, sentences(60)) for i in range(1, 6)]
        chunks = chunk_document(pages, min_tokens=200, max_tokens=400, overlap_tokens=50)
        assert len(chunks) > 5
        assert all(c.token_count <= 400 for c in chunks)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_consecutive_chunks_overlap(self) -> None:
        chunks = chunk_document(
            [PageText(1, sentences(120))], min_tokens=200, max_tokens=400, overlap_tokens=60
        )
        for prev, nxt in itertools.pairwise(chunks):
            assert nxt.metadata["overlap_tokens"] > 0
            first_sentence = nxt.content.split(". ")[0]
            assert first_sentence in prev.content

    def test_no_overlap_across_section_headings(self) -> None:
        text = f"# Part A\n{sentences(40)}\n\n# Part B\n{sentences(40, 'vectors')}"
        chunks = chunk_document(
            [PageText(1, text)], min_tokens=100, max_tokens=800, overlap_tokens=50
        )
        part_b = next(c for c in chunks if c.heading == "Part B")
        assert part_b.metadata["overlap_tokens"] == 0
        assert part_b.content.startswith("## Part B")

    def test_heading_metadata_and_page_numbers(self) -> None:
        pages = [
            PageText(1, f"# Limits\n{sentences(30, 'limits')}"),
            PageText(2, sentences(30, "limits")),
            PageText(3, f"# Derivatives\n{sentences(30, 'derivatives')}"),
        ]
        chunks = chunk_document(pages, min_tokens=100, max_tokens=500, overlap_tokens=40)
        assert chunks[0].heading == "Limits"
        assert chunks[0].page_numbers[0] == 1
        derivative_chunks = [c for c in chunks if c.heading == "Derivatives"]
        assert derivative_chunks and all(c.page_numbers == [3] for c in derivative_chunks)
        assert any(2 in c.page_numbers for c in chunks if c.heading == "Limits")

    def test_small_sections_merge_until_min_tokens(self) -> None:
        text = "\n\n".join(f"# Topic {i}\nA short note about topic {i}." for i in range(6))
        chunks = chunk_document(
            [PageText(1, text)], min_tokens=200, max_tokens=800, overlap_tokens=20
        )
        assert len(chunks) == 1
        assert chunks[0].content.count("## Topic") == 6

    def test_table_is_never_split_mid_element(self) -> None:
        table = "\n".join(
            ["| x | y |", "| --- | --- |"] + [f"| {i} | {2 * i} |" for i in range(30)]
        )
        text = f"{sentences(25)}\n\n{table}\n\n{sentences(25)}"
        chunks = chunk_document(
            [PageText(1, text)], min_tokens=100, max_tokens=300, overlap_tokens=30
        )
        holding = [c for c in chunks if "| --- | --- |" in c.content]
        assert len(holding) == 1
        assert table in holding[0].content

    def test_trailing_heading_only_is_kept(self) -> None:
        chunks = chunk_document(
            [PageText(1, "body text\n\n# Appendix")], min_tokens=10, max_tokens=50, overlap_tokens=0
        )
        assert chunks[-1].content.endswith("## Appendix")

    def test_empty_input(self) -> None:
        assert chunk_document([PageText(1, "")]) == []

    @pytest.mark.parametrize(
        ("kwargs"),
        [
            {"min_tokens": 0, "max_tokens": 10},
            {"min_tokens": 20, "max_tokens": 10},
            {"min_tokens": 10, "max_tokens": 20, "overlap_tokens": 10},
        ],
    )
    def test_invalid_parameters(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError, match="require"):
            chunk_document([PageText(1, "x")], **kwargs)

    def test_default_sizes_match_design(self) -> None:
        """500–1000 token chunks with 100-token overlap (AI_SYSTEM_DESIGN §5.3)."""
        pages = [PageText(i, sentences(80)) for i in range(1, 11)]
        chunks = chunk_document(pages)
        assert all(c.token_count <= 1000 for c in chunks)
        assert all(c.token_count >= 500 for c in chunks[:-1])
        assert 0 < chunks[1].metadata["overlap_tokens"] <= 100
        assert estimate_tokens(chunks[0].content) == chunks[0].token_count
