"""Document schemas and rules (API_CONTRACT §3.5, SECURITY_MODEL §5.2, DOMAIN_MODEL §3.1)."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.common import ORMModel
from app.domain.types import Name

ProcessingStatus = Literal["pending", "processing", "ready", "failed"]

#: Whitelisted upload types: MIME type -> allowed file extensions.
ALLOWED_UPLOADS: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "text/plain": (".txt",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
}

#: Valid lifecycle transitions: pending → processing → ready | failed; failed may be retried.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"processing"}),
    "processing": frozenset({"ready", "failed"}),
    "failed": frozenset({"processing"}),
    "ready": frozenset(),
}

#: processing_metadata keys exposed through the API (internal bookkeeping stays private).
PUBLIC_METADATA_KEYS = (
    "page_count",
    "chunk_count",
    "concept_count",
    "new_concept_count",
    "relationship_count",
    "ocr_pages",
    "stage",
    "progress",
    "error",
    "embedding_status",
    "warnings",
)

_UNSAFE = re.compile(r"[^\w.\- ()]+")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def can_transition(current: str | None, new: str) -> bool:
    return new in STATUS_TRANSITIONS.get(current or "pending", frozenset())


def sanitize_filename(raw: str) -> str:
    """Basename only, no path separators / control chars, safe for an S3 key."""
    base = unicodedata.normalize("NFKC", PureWindowsPath(PurePosixPath(raw).name).name)
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    ext = _NON_ALNUM.sub("", ext).lower()[:10]
    stem = _UNSAFE.sub("_", stem).strip(" ._-")[:150] or "document"
    return f"{stem}.{ext}" if ext else stem


def title_from_filename(filename: str) -> str:
    """Human title from the *original* file name (keeps unicode punctuation)."""
    base = PureWindowsPath(PurePosixPath(filename).name).name
    stem = base.rsplit(".", 1)[0] if "." in base else base
    title = " ".join(stem.replace("_", " ").split())
    return title[:200] or "Untitled document"


def public_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (metadata or {}).items() if k in PUBLIC_METADATA_KEYS}


# --- Requests --------------------------------------------------------------------------


class UploadUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    file_size_bytes: int = Field(gt=0)
    subject_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    title: Name | None = None

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_UPLOADS:
            raise ValueError(f"unsupported file type; allowed: {', '.join(ALLOWED_UPLOADS)}")
        return v

    @field_validator("filename")
    @classmethod
    def _filename(cls, v: str) -> str:
        if any(ord(ch) < 32 for ch in v):
            raise ValueError("must not contain control characters")
        return v

    @model_validator(mode="after")
    def _extension_matches(self) -> UploadUrlRequest:
        safe = sanitize_filename(self.filename)
        ext = "." + safe.rsplit(".", 1)[-1] if "." in safe else ""
        if ext not in ALLOWED_UPLOADS[self.mime_type]:
            allowed = ", ".join(ALLOWED_UPLOADS[self.mime_type])
            raise ValueError(f"file extension must be one of {allowed} for {self.mime_type}")
        return self


# --- Responses ------------------------------------------------------------------------------


class UploadUrlResponse(BaseModel):
    upload_url: str
    document_id: uuid.UUID
    s3_key: str
    expires_in_seconds: int
    # Headers the client must send with the PUT (they are part of the signature).
    upload_headers: dict[str, str]


class ConfirmUploadResponse(BaseModel):
    document_id: uuid.UUID
    processing_status: str
    task_id: str | None


class DocumentRead(ORMModel):
    id: uuid.UUID
    title: str
    source_filename: str
    mime_type: str
    file_size_bytes: int | None
    processing_status: str
    processing_metadata: dict[str, Any]
    subject_id: uuid.UUID | None
    course_id: uuid.UUID | None
    uploaded_at: datetime | None
    processed_at: datetime | None

    @field_validator("processing_metadata", mode="before")
    @classmethod
    def _public(cls, v: Any) -> dict[str, Any]:
        return public_metadata(v if isinstance(v, dict) else {})

    @field_validator("processing_status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> str:
        return v or "pending"


class DocumentConceptRef(BaseModel):
    id: uuid.UUID
    name: str
    section_count: int


class DocumentDetail(DocumentRead):
    section_count: int = 0
    concepts: list[DocumentConceptRef] = Field(default_factory=list)
