"""Difficulty calibration and question-type selection (LEARNING_ENGINE §6, AI_SYSTEM_DESIGN §4.3).

Questions target the Zone of Proximal Development: roughly a 70% expected success rate,
moved by the recent score trend and the session mode, and lowered while the frustration
guard is active.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.common import mastery_label

QUESTION_TYPES = ("mcq", "short_answer", "true_false", "worked_problem", "open_ended")
OBJECTIVE_TYPES = ("mcq", "true_false")

#: Recognition before recall, then application and transfer (LEARNING_ENGINE §6.2).
TYPE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "novice": ("true_false", "mcq"),
    "beginner": ("mcq", "short_answer"),
    "intermediate": ("short_answer", "worked_problem"),
    "proficient": ("worked_problem", "open_ended"),
    "mastered": ("open_ended", "worked_problem"),
}

MIN_DIFFICULTY, MAX_DIFFICULTY = 0.1, 0.95


def compute_target_difficulty(
    mastery: float,
    concept_difficulty: float,
    recent_scores: Sequence[float] = (),
    session_mode: str = "practice",
    offset: float = 0.0,
) -> float:
    base = mastery * 0.7 + concept_difficulty * 0.3
    if len(recent_scores) >= 3:
        base += (recent_scores[-1] - recent_scores[0]) * 0.1  # improving → harder
    if session_mode == "review":
        base -= 0.1
    base += offset
    return round(max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, base)), 3)


def select_question_type(mastery: float, recent_types: Sequence[str] = ()) -> str:
    """Preferred type for the mastery band, alternating to avoid repeating the last type."""
    preferred = TYPE_PREFERENCES[mastery_label(max(0.0, min(1.0, mastery)))]
    last = recent_types[-1] if recent_types else None
    for kind in preferred:
        if kind != last:
            return kind
    return preferred[0]


@dataclass(frozen=True, slots=True)
class AttemptSignal:
    is_correct: bool
    time_taken_seconds: int | None = None


def check_frustration(
    attempts: Sequence[AttemptSignal], *, consecutive_failures: int = 5, time_factor: float = 2.5
) -> bool:
    """LEARNING_ENGINE §7.3: N consecutive failures, or response times ballooning."""
    recent = list(attempts)[-consecutive_failures:]
    if len(recent) >= consecutive_failures and not any(a.is_correct for a in recent):
        return True
    timed = [a.time_taken_seconds for a in list(attempts)[-3:] if a.time_taken_seconds]
    return len(timed) >= 3 and timed[0] > 0 and timed[-1] > timed[0] * time_factor
