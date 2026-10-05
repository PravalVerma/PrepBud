"""Difficulty calibration, question-type choice, frustration detection (AC-4.3, AC-4.8)."""

from __future__ import annotations

import pytest

from app.services.assessment.difficulty_calibrator import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    AttemptSignal,
    check_frustration,
    compute_target_difficulty,
    select_question_type,
)


class TestTargetDifficulty:
    def test_increases_with_mastery(self) -> None:
        levels = [compute_target_difficulty(m, 0.5) for m in (0.0, 0.3, 0.6, 0.9)]
        assert levels == sorted(levels) and levels[0] < levels[-1]

    def test_formula(self) -> None:
        assert compute_target_difficulty(0.5, 0.5) == pytest.approx(0.5)
        assert compute_target_difficulty(0.6, 0.2) == pytest.approx(0.48)

    def test_respects_bounds(self) -> None:
        assert compute_target_difficulty(0.0, 0.0) == MIN_DIFFICULTY
        assert compute_target_difficulty(1.0, 1.0, [0.0, 0.5, 1.0]) == MAX_DIFFICULTY

    def test_trend_adjustment(self) -> None:
        flat = compute_target_difficulty(0.5, 0.5, [0.5, 0.5, 0.5])
        improving = compute_target_difficulty(0.5, 0.5, [0.0, 0.5, 1.0])
        declining = compute_target_difficulty(0.5, 0.5, [1.0, 0.5, 0.0])
        assert declining < flat < improving
        assert compute_target_difficulty(0.5, 0.5, [0.0, 1.0]) == flat  # needs 3+ scores

    def test_review_mode_is_easier(self) -> None:
        assert compute_target_difficulty(0.5, 0.5, session_mode="review") == pytest.approx(0.4)
        assert compute_target_difficulty(0.5, 0.5, session_mode="practice") == pytest.approx(0.5)

    def test_frustration_offset(self) -> None:
        assert compute_target_difficulty(0.5, 0.5, offset=-0.2) == pytest.approx(0.3)


class TestQuestionType:
    @pytest.mark.parametrize(
        ("mastery", "expected"),
        [
            (0.1, "true_false"),
            (0.3, "mcq"),
            (0.5, "short_answer"),
            (0.7, "worked_problem"),
            (0.9, "open_ended"),
        ],
    )
    def test_follows_mastery_band(self, mastery: float, expected: str) -> None:
        assert select_question_type(mastery) == expected

    def test_alternates_to_avoid_repetition(self) -> None:
        assert select_question_type(0.1, ["true_false"]) == "mcq"
        assert select_question_type(0.1, ["mcq"]) == "true_false"
        assert select_question_type(0.5, ["short_answer", "worked_problem"]) == "short_answer"

    def test_clamps_mastery(self) -> None:
        assert select_question_type(1.5) == "open_ended"
        assert select_question_type(-1) == "true_false"


class TestFrustration:
    def test_five_consecutive_failures(self) -> None:
        fails = [AttemptSignal(False)] * 5
        assert check_frustration(fails)
        assert not check_frustration(fails[:4])
        assert not check_frustration([AttemptSignal(True), *fails[:4]])
        assert check_frustration([AttemptSignal(True), *fails])

    def test_custom_threshold(self) -> None:
        assert check_frustration([AttemptSignal(False)] * 3, consecutive_failures=3)

    def test_response_time_spike(self) -> None:
        slow = [AttemptSignal(True, 10), AttemptSignal(True, 15), AttemptSignal(True, 30)]
        assert check_frustration(slow)
        assert not check_frustration(
            [AttemptSignal(True, 10), AttemptSignal(True, 12), AttemptSignal(True, 20)]
        )
        assert not check_frustration(
            [AttemptSignal(True, None), AttemptSignal(True, 5), AttemptSignal(True, 50)]
        )
