"""Mastery model: BKT update (AC-4.1, AC-4.2), decay, confidence (LEARNING_ENGINE §4–5)."""

from __future__ import annotations

import itertools

import pytest

from app.services.student_model.mastery_tracker import (
    MasteryUpdateInput,
    apply_mastery_decay,
    confidence_for,
    update_mastery,
)


def upd(
    mastery: float, correct: bool, score: float | None = None, difficulty: float = 0.5
) -> float:
    return update_mastery(
        MasteryUpdateInput(
            current_mastery=mastery,
            is_correct=correct,
            score=score if score is not None else (1.0 if correct else 0.0),
            question_difficulty=difficulty,
        )
    )


class TestMasteryUpdate:
    def test_correct_answer_increases_mastery(self) -> None:
        assert upd(0.5, True) > 0.5

    def test_incorrect_answer_decreases_mastery(self) -> None:
        assert upd(0.5, False) < 0.5

    @pytest.mark.parametrize("mastery", [0.0, 0.01, 0.05, 0.2, 0.5, 0.79, 0.95, 0.999])
    @pytest.mark.parametrize("difficulty", [0.0, 0.3, 0.9])
    def test_direction_holds_everywhere(self, mastery: float, difficulty: float) -> None:
        """AC-4.1 across the whole range — including very low mastery, where the raw BKT
        formula's learning transition would otherwise raise mastery after a wrong answer."""
        assert upd(mastery, True, difficulty=difficulty) > mastery
        if mastery > 0:
            assert upd(mastery, False, difficulty=difficulty) < mastery
        else:
            assert upd(mastery, False, difficulty=difficulty) == 0.0

    def test_incorrect_at_full_mastery_still_decreases(self) -> None:
        assert upd(1.0, False) < 1.0

    @pytest.mark.parametrize(
        ("mastery", "correct", "score", "difficulty"),
        list(itertools.product([0.0, 0.5, 0.99, 1.0], [True, False], [0.0, 0.5, 1.0], [0.0, 1.0])),
    )
    def test_stays_in_bounds(
        self, mastery: float, correct: bool, score: float, difficulty: float
    ) -> None:
        """AC-4.2."""
        assert 0.0 <= upd(mastery, correct, score, difficulty) <= 1.0

    def test_out_of_range_inputs_are_clamped(self) -> None:
        assert 0.0 <= upd(1.7, True, 3.0, 5.0) <= 1.0
        assert 0.0 <= upd(-0.4, False, -1.0, -2.0) <= 1.0

    def test_harder_questions_give_more_signal(self) -> None:
        easy, hard = upd(0.5, True, difficulty=0.2), upd(0.5, True, difficulty=0.8)
        assert hard > easy
        assert upd(0.5, False, difficulty=0.8) < upd(0.5, False, difficulty=0.2)

    def test_partial_credit_sits_between(self) -> None:
        assert upd(0.5, True, 1.0) >= upd(0.5, True, 0.6) > 0.5
        assert upd(0.5, False, 0.0) <= upd(0.5, False, 0.4) <= 0.5

    def test_repeated_success_converges_towards_one(self) -> None:
        m = 0.1
        for _ in range(25):
            m = upd(m, True, difficulty=0.7)
        assert m > 0.9

    def test_repeated_failure_converges_towards_zero(self) -> None:
        m = 0.9
        for _ in range(25):
            m = upd(m, False)
        assert m < 0.1

    def test_parameters_change_the_update(self) -> None:
        base = MasteryUpdateInput(0.5, True, 1.0, 0.5)
        guessy = MasteryUpdateInput(0.5, True, 1.0, 0.5, guess_rate=0.6)
        assert update_mastery(guessy) < update_mastery(base)  # a likely guess proves less


class TestDecay:
    def test_no_time_no_decay(self) -> None:
        assert apply_mastery_decay(0.8, 0, 2.5) == 0.8
        assert apply_mastery_decay(0.8, -3, 2.5) == 0.8

    def test_decays_over_time_and_never_below_zero(self) -> None:
        one_week = apply_mastery_decay(0.8, 7, 2.5)
        one_year = apply_mastery_decay(0.8, 365, 2.5)
        assert 0.0 <= one_year < one_week < 0.8

    def test_matches_formula(self) -> None:
        import math

        assert apply_mastery_decay(0.9, 10, 2.5) == pytest.approx(0.9 * math.exp(-10 / 25))

    def test_higher_ease_decays_slower(self) -> None:
        assert apply_mastery_decay(0.8, 10, 3.0) > apply_mastery_decay(0.8, 10, 1.3)

    def test_ease_floor(self) -> None:
        assert apply_mastery_decay(0.8, 10, 0.1) == apply_mastery_decay(0.8, 10, 1.3)


def test_confidence_grows_with_attempts() -> None:
    assert confidence_for(0) == 0.0
    assert confidence_for(3) == 0.5
    assert confidence_for(1) < confidence_for(5) < confidence_for(50) < 1.0


@pytest.mark.parametrize(
    ("mastery", "correct", "difficulty", "expected"),
    [
        (0.5, True, 0.5, 0.73),
        (0.5, True, 0.8, 0.77),
        (0.5, False, 0.5, 0.21),
        (0.05, False, 0.5, 0.02),
    ],
)
def test_documented_examples(
    mastery: float, correct: bool, difficulty: float, expected: float
) -> None:
    """The example table in LEARNING_ENGINE §4.2 — keep docs and code in step."""
    assert round(upd(mastery, correct, difficulty=difficulty), 2) == expected
