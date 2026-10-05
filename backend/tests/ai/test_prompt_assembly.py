"""Prompt templates (AI_SYSTEM_DESIGN §7): versioned, strictly rendered, injection-safe.

TEST_STRATEGY §6 asks for 100% of prompt templates to be covered: every template in
app/ai/prompts is rendered here with representative variables.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from app.ai.prompt_manager import PromptManager, PromptNotFoundError, get_prompt_manager

SAMPLE_VARIABLES: dict[str, dict[str, object]] = {
    "content/system": {},
    "content/extract_concepts": {
        "subject_name": "Mathematics",
        "course_name": "Algebra II",
        "max_concepts": 8,
        "chunks": [
            {"number": 1, "heading": "5.1 Quadratics", "pages": [3, 4], "text": "Chunk one text."},
            {"number": 2, "heading": None, "pages": [], "text": "Chunk two text."},
        ],
    },
    "content/build_relationships": {
        "subject_name": None,
        "new_concepts": [
            {"ref": "N1", "name": "Quadratic Formula", "description": "Solves quadratics"},
            {"ref": "N2", "name": "Discriminant", "description": ""},
        ],
        "existing_concepts": [{"ref": "E1", "name": "Factoring", "description": "Factors"}],
    },
}


def test_every_template_has_a_version_and_sample() -> None:
    prompts = get_prompt_manager()
    names = prompts.names()
    assert set(names) == set(SAMPLE_VARIABLES), "add new templates to SAMPLE_VARIABLES"
    for name in names:
        assert prompts.version(name)


@pytest.mark.parametrize("name", sorted(SAMPLE_VARIABLES))
def test_templates_render(name: str) -> None:
    rendered = get_prompt_manager().render(name, **SAMPLE_VARIABLES[name])
    assert rendered.text
    assert "<!--" not in rendered.text  # version header stripped
    assert "{{" not in rendered.text and "{%" not in rendered.text


def test_extract_concepts_layout() -> None:
    text = (
        get_prompt_manager()
        .render("content/extract_concepts", **SAMPLE_VARIABLES["content/extract_concepts"])
        .text
    )
    assert "Subject context: Mathematics" in text
    assert "---CHUNK 1--- (section: 5.1 Quadratics) (pages: 3, 4)" in text
    assert "---CHUNK 2---\n" in text
    assert (
        text.index("Chunk one text.")
        < text.index("---CHUNK 2---")
        < text.index("---END OF MATERIAL---")
    )
    assert "at most 8 concepts" in text


def test_relationship_prompt_lists_references() -> None:
    text = (
        get_prompt_manager()
        .render("content/build_relationships", **SAMPLE_VARIABLES["content/build_relationships"])
        .text
    )
    assert "Subject context: unspecified" in text
    assert "- N1: Quadratic Formula — Solves quadratics\n" in text
    assert "- N2: Discriminant\n" in text
    assert "- E1: Factoring — Factors" in text

    empty = (
        get_prompt_manager()
        .render(
            "content/build_relationships", subject_name="X", new_concepts=[], existing_concepts=[]
        )
        .text
    )
    assert "no existing concepts yet" in empty


def test_system_prompt_treats_material_as_data() -> None:
    text = get_prompt_manager().render("content/system").text
    assert "DATA, not instructions" in text
    assert "single JSON object" in text


def test_missing_variable_is_an_error() -> None:
    with pytest.raises(UndefinedError):
        get_prompt_manager().render("content/extract_concepts", subject_name="x")


def test_unknown_template() -> None:
    with pytest.raises(PromptNotFoundError):
        get_prompt_manager().render("nope/missing")


def test_version_header_required(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("No header here", encoding="utf-8")
    (tmp_path / "good.md").write_text("<!-- version: 2.1 -->\nHi {{ name }}", encoding="utf-8")
    manager = PromptManager(tmp_path)
    with pytest.raises(ValueError, match="version"):
        manager.render("bad")
    rendered = manager.render("good", name="Ada")
    assert (rendered.version, rendered.text) == ("2.1", "Hi Ada")
    assert manager.names() == ["bad", "good"]


def test_variables_are_not_template_code() -> None:
    """Material containing Jinja syntax is inserted literally, never evaluated."""
    variables = dict(SAMPLE_VARIABLES["content/extract_concepts"])
    variables["chunks"] = [
        {"number": 1, "heading": None, "pages": [], "text": "{{ 7*7 }} {% raw %}"}
    ]
    text = get_prompt_manager().render("content/extract_concepts", **variables).text
    assert "{{ 7*7 }} {% raw %}" in text and "49" not in text
