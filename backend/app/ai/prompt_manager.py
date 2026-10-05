"""Prompt templates: versioned markdown files rendered with Jinja2 (AI_SYSTEM_DESIGN §7).

Templates live in ``app/ai/prompts/<area>/<name>.md`` and start with a version
comment (``<!-- version: 1.0 -->``) that is recorded with every AI interaction so
prompt regressions can be traced. Rendering is strict: a missing variable is an
error, not an empty string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION = re.compile(r"^\s*<!--\s*version:\s*([\w.\-]+)\s*-->\s*\n?", re.IGNORECASE)


class PromptNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    name: str
    version: str
    text: str


class PromptManager:
    def __init__(self, root: Path = PROMPTS_DIR) -> None:
        self.root = root
        self._env = Environment(
            loader=FileSystemLoader(str(root)),
            undefined=StrictUndefined,
            autoescape=False,  # noqa: S701 - plain-text LLM prompts, never rendered as HTML
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

    def names(self) -> list[str]:
        return sorted(
            p.relative_to(self.root).with_suffix("").as_posix() for p in self.root.rglob("*.md")
        )

    def version(self, name: str) -> str:
        source = self._source(name)
        match = _VERSION.match(source)
        if match is None:
            raise ValueError(f"Prompt {name!r} is missing its '<!-- version: x -->' header")
        return match.group(1)

    def _source(self, name: str) -> str:
        path = self.root / f"{name}.md"
        if not path.is_file():
            raise PromptNotFoundError(name)
        return path.read_text(encoding="utf-8")

    def render(self, name: str, /, **variables: Any) -> RenderedPrompt:
        version = self.version(name)
        try:
            template = self._env.get_template(f"{name}.md")
        except TemplateNotFound as exc:  # pragma: no cover - guarded by _source
            raise PromptNotFoundError(name) from exc
        text = _VERSION.sub("", template.render(**variables), count=1).strip()
        return RenderedPrompt(name=name, version=version, text=text)


@lru_cache
def get_prompt_manager() -> PromptManager:
    return PromptManager()
