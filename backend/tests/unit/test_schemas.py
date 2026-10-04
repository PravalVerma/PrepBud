"""Pydantic request/response model validation (Phase 2 unit tests)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain.curriculum import (
    ChapterCreate,
    CourseCreate,
    CourseUpdate,
    SectionRead,
    SubjectCreate,
    SubjectRead,
    SubjectUpdate,
)
from app.domain.user import AuthCallbackRequest, ProfileUpdate


class TestSubjectCreate:
    def test_strips_whitespace(self) -> None:
        s = SubjectCreate(name="  Mathematics  ", description="  HS maths ", icon=" 📐 ")
        assert (s.name, s.description, s.icon) == ("Mathematics", "HS maths", "📐")

    def test_optional_fields_default_to_none(self) -> None:
        s = SubjectCreate(name="Physics")
        assert s.description is None
        assert s.icon is None

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201, "tab\x07bell", "nul\x00byte"])
    def test_rejects_invalid_names(self, name: str) -> None:
        with pytest.raises(ValidationError):
            SubjectCreate(name=name)

    def test_name_required(self) -> None:
        with pytest.raises(ValidationError) as exc:
            SubjectCreate.model_validate({})
        assert exc.value.errors()[0]["loc"] == ("name",)

    def test_accepts_newlines_in_description(self) -> None:
        assert SubjectCreate(name="n", description="line1\nline2").description == "line1\nline2"

    def test_rejects_overlong_description_and_icon(self) -> None:
        with pytest.raises(ValidationError):
            SubjectCreate(name="n", description="d" * 5001)
        with pytest.raises(ValidationError):
            SubjectCreate(name="n", icon="i" * 33)

    def test_forbids_unknown_fields(self) -> None:
        """Clients cannot smuggle ownership or ids through the body."""
        with pytest.raises(ValidationError):
            SubjectCreate.model_validate({"name": "n", "user_id": str(uuid.uuid4())})


class TestSubjectUpdate:
    def test_empty_update_is_valid(self) -> None:
        assert SubjectUpdate().model_dump(exclude_unset=True) == {}

    def test_null_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubjectUpdate.model_validate({"name": None})

    def test_description_can_be_cleared(self) -> None:
        u = SubjectUpdate.model_validate({"description": None})
        assert u.model_dump(exclude_unset=True) == {"description": None}


class TestCurriculumNodes:
    def test_sort_order_defaults_to_zero(self) -> None:
        assert CourseCreate(name="Algebra").sort_order == 0

    @pytest.mark.parametrize("value", [-1, 1_000_001])
    def test_sort_order_bounds(self, value: int) -> None:
        with pytest.raises(ValidationError):
            ChapterCreate(name="c", sort_order=value)

    def test_update_null_name_rejected_but_sort_order_optional(self) -> None:
        with pytest.raises(ValidationError):
            CourseUpdate.model_validate({"name": None})
        assert CourseUpdate.model_validate({"sort_order": 3}).sort_order == 3

    def test_read_models_build_from_attributes(self) -> None:
        class Obj:
            id = uuid.uuid4()
            chapter_id = uuid.uuid4()
            name = "Formula"
            description = None
            sort_order = 2
            created_at = None
            updated_at = None

        read = SectionRead.model_validate(Obj())
        assert read.chapter_id == Obj.chapter_id
        assert read.sort_order == 2

    def test_subject_read_defaults_stats(self) -> None:
        r = SubjectRead(
            id=uuid.uuid4(),
            name="n",
            description=None,
            icon=None,
            created_at=None,
            updated_at=None,
        )
        assert (r.course_count, r.concept_count, r.avg_mastery) == (0, 0, 0.0)


class TestProfileUpdate:
    def test_valid_full_update(self) -> None:
        u = ProfileUpdate(
            grade_level="10th grade",
            difficulty_band="advanced",
            language="pt-BR",
            timezone="Asia/Kolkata",
            preferences={"theme": "dark"},
            onboarding_state="complete",
            display_name="Jane",
        )
        assert u.timezone == "Asia/Kolkata"

    @pytest.mark.parametrize("tz", ["Not/AZone", "UTC+5", ""])
    def test_invalid_timezone(self, tz: str) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(timezone=tz)

    @pytest.mark.parametrize("band", ["expert", "Advanced", ""])
    def test_invalid_difficulty_band(self, band: str) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate.model_validate({"difficulty_band": band})

    @pytest.mark.parametrize("lang", ["en", "hi", "zh-Hant", "en-GB"])
    def test_valid_languages(self, lang: str) -> None:
        assert ProfileUpdate(language=lang).language == lang

    @pytest.mark.parametrize("lang", ["EN", "english", "e", "en_GB"])
    def test_invalid_languages(self, lang: str) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(language=lang)

    @pytest.mark.parametrize(
        "field", ["difficulty_band", "language", "timezone", "preferences", "onboarding_state"]
    )
    def test_defaulted_fields_cannot_be_nulled(self, field: str) -> None:
        with pytest.raises(ValidationError, match="cannot be null"):
            ProfileUpdate.model_validate({field: None})

    def test_grade_level_can_be_nulled(self) -> None:
        assert ProfileUpdate.model_validate({"grade_level": None}).model_dump(
            exclude_unset=True
        ) == {"grade_level": None}

    def test_preferences_key_limit(self) -> None:
        ProfileUpdate(preferences={str(i): i for i in range(50)})
        with pytest.raises(ValidationError):
            ProfileUpdate(preferences={str(i): i for i in range(51)})

    def test_unknown_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate.model_validate({"cumulative_stats": {"total_sessions": 999}})


class TestAuthCallbackRequest:
    def test_all_optional_and_extra_ignored(self) -> None:
        r = AuthCallbackRequest.model_validate({"something": "else"})
        assert r.auth_id is None
        assert r.email is None

    def test_email_validated(self) -> None:
        with pytest.raises(ValidationError):
            AuthCallbackRequest(email="nope")  # type: ignore[arg-type]
