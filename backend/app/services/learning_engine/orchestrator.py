"""Session orchestration as a LangGraph state graph (ADR-002, AI_SYSTEM_DESIGN §6,
PRODUCT_REQUIREMENTS §4).

One graph invocation = one *turn*: it starts from the student's latest message (or from
INIT for a new session) and runs until the student has to respond again::

    START ─▶ init ─▶ plan ─┐
      │                    ├─▶ explain ───────────────▶ END   (awaiting acknowledgement)
      ├─▶ acknowledge ─────┤─▶ practice / review ─────▶ END   (awaiting answer)
      ├─▶ evaluate ─▶ update ─▶ decide ─┘
      ├─▶ respond (follow-up question / hint) ────────▶ END
      └─▶ wrap ─▶ schedule ───────────────────────────▶ END   (session ended)

State is checkpointed by the session manager after every turn (Redis + PostgreSQL), so
LangGraph needs no checkpointer of its own and stays replaceable (ADR-002).

Decisions (`decide_next`) are deterministic rules from LEARNING_ENGINE §7 and
AI_SYSTEM_DESIGN §6.3, kept pure for unit testing.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.cost_tracker import AICallContext
from app.ai.llm_client import LLMClient
from app.ai.prompt_manager import PromptManager
from app.ai.providers.base import LLMError, LLMMessage
from app.config import LearningEngineSettings, Settings
from app.core.logging import get_logger
from app.db.models import (
    Concept,
    ConceptRelationship,
    LearningGoal,
    Question,
    QuestionAttempt,
    ReviewItem,
    StudentConceptMastery,
    StudentProfile,
    StudyPlan,
)
from app.db.repositories.concept import ConceptRepository
from app.domain.common import mastery_label
from app.services.assessment.answer_evaluator import AnswerEvaluator, answer_text
from app.services.assessment.difficulty_calibrator import (
    AttemptSignal,
    check_frustration,
    compute_target_difficulty,
    select_question_type,
)
from app.services.assessment.question_generator import (
    QuestionGenerator,
    QuestionRequest,
    public_options,
)
from app.services.content.retriever import HybridRetriever, retrieve_for_concept
from app.services.learning_engine.concept_selector import (
    ConceptGraph,
    ConceptSelectionInput,
    ReviewCandidate,
    select_concepts,
)
from app.services.learning_engine.state import NODE_STATES, SessionState
from app.services.student_model.mastery_tracker import MasteryTracker
from app.services.student_model.misconception_tracker import (
    RESOLVE_AFTER_STREAK,
    DetectedMisconception,
    MisconceptionTracker,
)
from app.services.study_plan.plan_service import goal_concept_ids
from app.services.tutor.context_builder import (
    ConceptBrief,
    TutorContext,
    snippets_text,
    student_level,
)
from app.services.tutor.tutor import Tutor

logger = get_logger(__name__)

Emitter = Callable[[dict[str, Any]], Awaitable[None]]
HISTORY_LIMIT = 12
SUMMARY_TASK = "session_summary"
SUMMARY_PROMPT = "session/summarise_session"


class SessionPlanningError(Exception):
    """No concepts can be studied (e.g. nothing uploaded yet)."""


# --- Pure decision logic ------------------------------------------------------------------------


@dataclass(slots=True)
class Decision:
    action: str  # explain | practice | review | wrap
    reason: str
    explain_kind: str | None = None
    switch_to: str | None = None
    end_reason: str | None = None
    message: str | None = None
    frustration: bool = False


def elapsed_minutes(state: SessionState, now: datetime) -> float:
    """Active study time: wall-clock since start minus time spent paused."""
    started = datetime.fromisoformat(state["started_at"])
    paused = float(state.get("paused_seconds") or 0.0)
    if state.get("paused_at"):
        paused += max(0.0, (now - datetime.fromisoformat(str(state["paused_at"]))).total_seconds())
    return max(0.0, ((now - started).total_seconds() - paused) / 60)


def initial_action(state: SessionState, concept_id: str, cfg: LearningEngineSettings) -> str:
    """LEARNING_ENGINE §7.1 for the first activity on a concept, by session type."""
    mastery = state["mastery_snapshot"].get(concept_id, 0.0)
    kind = state["session_type"]
    is_review = concept_id in state["review_concepts"]
    if kind == "review" or (is_review and kind in ("mixed", "practice")):
        return "review"
    if kind == "practice":
        return "practice"
    if kind == "teach":
        return "practice" if mastery >= cfg.mastery_learned_threshold else "explain"
    return "explain" if mastery < 0.2 else "practice"


def explain_kind_after_failure(state: SessionState, concept_id: str) -> str:
    count = state["explanations"].get(concept_id, 0)
    if count >= 2:
        return "worked_example"
    if state["mastery_snapshot"].get(concept_id, 0.0) >= 0.6:
        return "socratic"
    return "retry" if count >= 1 else "intro"


def _easiest_remaining(state: SessionState) -> str | None:
    remaining = state["concepts_remaining"]
    if not remaining:
        return None
    info = state["concept_info"]
    return min(
        remaining, key=lambda c: (info.get(c, {}).get("difficulty", 0.5), remaining.index(c))
    )


def decide_next(state: SessionState, cfg: LearningEngineSettings, now: datetime) -> Decision:
    concept = state["current_concept_id"]
    mastery = state["mastery_snapshot"].get(concept or "", 0.0)
    action = state["current_action"]

    if state["interaction_count"] >= state["max_interactions"]:
        return Decision("wrap", "interaction_limit", end_reason="interaction_limit")
    if elapsed_minutes(state, now) >= state["time_budget_minutes"]:
        return Decision("wrap", "time_budget", end_reason="time_budget")
    targets = state["target_concepts"]
    if targets and all(
        state["mastery_snapshot"].get(c, 0.0) >= cfg.mastery_learned_threshold for c in targets
    ):
        return Decision("wrap", "all_mastered", end_reason="all_mastered")

    since = state.get("frustration_reset_index", 0)  # only evidence after the last activation
    signals = [
        AttemptSignal(a["is_correct"], a.get("time_taken")) for a in state["attempts"][since:]
    ]
    failing = state["consecutive_failures"] >= cfg.frustration_consecutive_failures
    if failing or check_frustration(
        signals,
        consecutive_failures=cfg.frustration_consecutive_failures,
        time_factor=cfg.frustration_response_time_factor,
    ):
        if state["frustration_activations"] + 1 >= cfg.frustration_max_activations:
            return Decision(
                "wrap",
                "frustration",
                end_reason="frustration",
                frustration=True,
                message=(
                    "You've worked really hard on this. Tough concepts often click after a break — "
                    "let's stop here and come back to it fresh."
                ),
            )
        easier = _easiest_remaining(state)
        return Decision(
            "explain" if easier is None else "switch",
            "frustration",
            explain_kind="worked_example" if easier is None else None,
            switch_to=easier,
            frustration=True,
            message=(
                "This one is tricky — that's completely normal. Let's take it step by step with "
                "something a little easier."
            ),
        )

    attempts_here = state["concept_attempts"].get(concept or "", 0)
    if mastery >= cfg.mastery_learned_threshold or attempts_here >= cfg.max_attempts_per_concept:
        reason = "concept_mastered" if mastery >= cfg.mastery_learned_threshold else "attempt_limit"
        if state["concepts_remaining"]:
            return Decision("switch", reason, switch_to=state["concepts_remaining"][0])
        return Decision("wrap", "concepts_complete", end_reason="concepts_complete")

    last = state["attempts"][-1] if state["attempts"] else None
    if action in ("practice", "review") and last and last["concept_id"] == concept:
        # Re-teach after a poor answer, or a wrong one while mastery is still low; a correct
        # answer keeps the student practising (AI_SYSTEM_DESIGN §6.3) even at low mastery.
        if last["score"] < 0.3 or (mastery < 0.4 and not last["is_correct"]):
            return Decision(
                "explain",
                "struggling",
                explain_kind=explain_kind_after_failure(state, concept or ""),
            )
        return Decision("review" if action == "review" else "practice", "keep_practising")
    if action == "explain":
        return Decision("practice", "check_understanding")
    return Decision(initial_action(state, concept or "", cfg), "default")


def misconception_message(name: str, description: str) -> str:
    """Non-judgemental wording (AI_SYSTEM_DESIGN §8.2)."""
    text = description.strip().rstrip(".")
    detail = "" if not text or text.lower() == name.strip().lower() else f" ({text})"
    return (
        f"I notice a common mix-up here: {name}{detail}. "
        "That's a normal step in learning this — let's look at why it doesn't quite work."
    )


# --- Engine ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class EngineDeps:
    session: AsyncSession
    settings: Settings
    llm: LLMClient
    prompts: PromptManager
    retriever: HybridRetriever
    ai: AICallContext
    emit: Emitter | None = None
    redis: Redis | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


class SessionEngine:
    def __init__(self, deps: EngineDeps) -> None:
        self.d = deps
        self.cfg = deps.settings.learning_engine
        self.tracker = MasteryTracker(deps.session, self.cfg, redis=deps.redis, now=deps.now)
        self.misconceptions = MisconceptionTracker(deps.session, now=deps.now)
        self.questions = QuestionGenerator(
            deps.session, deps.llm, deps.prompts, reuse_window=self.cfg.question_reuse_window
        )
        self.evaluator = AnswerEvaluator(deps.llm, deps.prompts)
        self.tutor = Tutor(deps.llm, deps.prompts, context_tokens=self.cfg.context_token_budget)

    # ---- helpers -------------------------------------------------------------------------------

    @property
    def user_id(self) -> uuid.UUID:
        return self.d.ai.user_id

    async def _emit(
        self, state: SessionState, type_: str, payload: dict[str, Any], *, record: bool = True
    ) -> None:
        event = {"type": type_, "payload": payload}
        if record:
            state["outbox"].append(event)
        if self.d.emit is not None:
            await self.d.emit(event)

    def _log(
        self, state: SessionState, event_type: str, concept_id: str | None, payload: dict[str, Any]
    ) -> None:
        state["log"].append(
            {"event_type": event_type, "concept_id": concept_id, "payload": payload}
        )

    def _enter(self, state: SessionState, node: str) -> None:
        if node in NODE_STATES:
            state["state_trail"] = [*state["state_trail"], NODE_STATES[node]][-200:]

    def _remember(self, state: SessionState, line: str) -> None:
        state["history"] = [*state["history"], line[:300]][-HISTORY_LIMIT:]

    def _concept(self, state: SessionState, concept_id: str) -> ConceptBrief:
        info = state["concept_info"].get(concept_id, {})
        return ConceptBrief(
            id=uuid.UUID(concept_id),
            name=info.get("name", "this concept"),
            description=info.get("description"),
            difficulty=float(info.get("difficulty", 0.5)),
        )

    async def _material(self, brief: ConceptBrief) -> list[Any]:
        return await retrieve_for_concept(
            self.d.retriever,
            self.user_id,
            concept_id=brief.id,
            concept_name=brief.name,
            concept_description=brief.description,
            limit=self.cfg.max_content_chunks,
            ctx=self.d.ai,
        )

    async def _tutor_context(self, state: SessionState, concept_id: str) -> TutorContext:
        brief = self._concept(state, concept_id)
        repo = ConceptRepository(self.d.session)
        prereqs = await repo.prerequisites(brief.id, self.user_id)
        levels = await self.tracker.levels(self.user_id, [p.id for p in prereqs])
        profile = state["student_profile"]
        return TutorContext(
            concept=brief,
            mastery=state["mastery_snapshot"].get(concept_id, 0.0),
            student_level=student_level(profile.get("grade_level"), profile.get("difficulty_band")),
            prerequisites=[(p.name, levels.get(p.id, 0.0)) for p in prereqs],
            misconceptions=await self.misconceptions.known_for_concept(self.user_id, brief.id),
            material=await self._material(brief),
            history=list(state["history"]),
        )

    def _switch_concept(self, state: SessionState, concept_id: str) -> None:
        state["concepts_remaining"] = [c for c in state["concepts_remaining"] if c != concept_id]
        previous = state["current_concept_id"]
        # Unfinished concepts go back in the queue only when switched away from early.
        if (
            previous
            and previous != concept_id
            and previous not in state["concepts_remaining"]
            and state["mastery_snapshot"].get(previous, 0.0) < self.cfg.mastery_learned_threshold
            and state["concept_attempts"].get(previous, 0) < self.cfg.max_attempts_per_concept
        ):
            state["concepts_remaining"] = [*state["concepts_remaining"], previous]
        state["current_concept_id"] = concept_id
        state["current_action"] = None
        self._log(state, "concept_changed", concept_id, {"from": previous, "to": concept_id})
        self._remember(
            state,
            f"Moved on to {state['concept_info'].get(concept_id, {}).get('name', concept_id)}",
        )

    # ---- nodes ----------------------------------------------------------------------------------

    async def init(self, state: SessionState) -> SessionState:
        self._enter(state, "init")
        profile = await self.d.session.scalar(
            select(StudentProfile).where(StudentProfile.user_id == self.user_id)
        )
        state["student_profile"] = {
            "grade_level": profile.grade_level if profile else None,
            "difficulty_band": profile.difficulty_band if profile else None,
        }
        return state

    async def plan(self, state: SessionState) -> SessionState:
        self._enter(state, "plan")
        session, user = self.d.session, self.user_id
        concepts = (
            await session.execute(
                select(
                    Concept.id,
                    Concept.name,
                    Concept.description,
                    Concept.difficulty_estimate,
                    Concept.subject_id,
                ).where(Concept.user_id == user)
            )
        ).all()
        if not concepts:
            raise SessionPlanningError("No concepts yet — upload some study material first")
        info = {
            str(c.id): {
                "name": c.name,
                "description": c.description,
                "difficulty": float(c.difficulty_estimate or 0.5),
            }
            for c in concepts
        }
        ids = [c.id for c in concepts]
        edges = (
            await session.execute(
                select(ConceptRelationship.source_concept_id, ConceptRelationship.target_concept_id)
                .join(Concept, Concept.id == ConceptRelationship.target_concept_id)
                .where(
                    Concept.user_id == user, ConceptRelationship.relationship_type == "prerequisite"
                )
            )
        ).all()
        snapshots = await self.tracker.snapshot(user, ids)
        mastery = {c: s.level for c, s in snapshots.items()}

        goal_concepts: list[uuid.UUID] | None = None
        if state["learning_goal_id"]:
            goal = await session.scalar(
                select(LearningGoal).where(
                    LearningGoal.id == uuid.UUID(state["learning_goal_id"]),
                    LearningGoal.user_id == user,
                )
            )
            if goal is not None:
                goal_concepts = await goal_concept_ids(session, user, goal)

        now = self.d.now()
        due: dict[uuid.UUID, ReviewCandidate] = {}
        plan_items = (
            await session.execute(
                select(
                    ReviewItem.id,
                    ReviewItem.concept_id,
                    ReviewItem.priority,
                    ReviewItem.scheduled_date,
                    ReviewItem.status,
                    StudyPlan.generation_metadata,
                )
                .join(StudyPlan, StudyPlan.id == ReviewItem.study_plan_id)
                .where(
                    StudyPlan.user_id == user,
                    StudyPlan.status == "active",
                    ReviewItem.status.in_(("pending", "overdue")),
                    ReviewItem.scheduled_date <= now.date(),
                )
                .order_by(ReviewItem.scheduled_date, ReviewItem.priority.desc())
            )
        ).all()
        # Due plan items: reviews feed spaced repetition; concepts to *learn* today stand in
        # for goal concepts when the session has no goal of its own (Phase 6).
        plan_learn: list[uuid.UUID] = []
        for item_id, concept_id, priority, scheduled, status, meta in plan_items:
            kind = ((meta or {}).get("item_kinds") or {}).get(str(item_id), "review")
            if kind == "learn":
                plan_learn.append(concept_id)
                continue
            due[concept_id] = ReviewCandidate(
                concept_id, float(priority or 0.5), status == "overdue" or scheduled < now.date()
            )
        if goal_concepts is None and plan_learn:
            goal_concepts = list(dict.fromkeys(plan_learn))
        for snap in snapshots.values():  # SM-2 due dates on the mastery records themselves
            if snap.next_review_at and snap.next_review_at <= now and snap.concept_id not in due:
                overdue = snap.next_review_at.date() < now.date()
                due[snap.concept_id] = ReviewCandidate(snap.concept_id, 0.5, overdue)

        selection = select_concepts(
            ConceptSelectionInput(
                concepts=ids,
                mastery=mastery,
                graph=ConceptGraph.from_edges(edges),
                session_type=state["session_type"],
                max_concepts=state["max_concepts"],
                due_reviews=list(due.values()),
                goal_concepts=goal_concepts,
                requested=[uuid.UUID(c) for c in state["requested_concepts"]] or None,
                difficulty={uuid.UUID(k): v["difficulty"] for k, v in info.items()},
                prerequisite_threshold=self.cfg.mastery_prerequisite_threshold,
                learned_threshold=self.cfg.mastery_learned_threshold,
            )
        )
        if not selection.concept_ids:
            raise SessionPlanningError(
                "Nothing to review yet"
                if state["session_type"] == "review"
                else "No concepts available to study"
            )
        targets = [str(c) for c in selection.concept_ids]
        state["concept_info"] = {c: info[c] for c in targets}
        state["mastery_snapshot"] = {c: mastery[uuid.UUID(c)] for c in targets}
        state["initial_mastery"] = dict(state["mastery_snapshot"])
        state["target_concepts"] = targets
        state["review_concepts"] = [str(c) for c in selection.review_ids]
        state["concepts_remaining"] = targets[1:]
        state["current_concept_id"] = targets[0]
        state["status"] = "initialising" if state.get("plan_only") else "active"
        actions = {c: initial_action(state, c, self.cfg) for c in targets}
        state["objective"] = {
            "target_concepts": [
                {
                    "id": c,
                    "name": info[c]["name"],
                    "action": {"explain": "teach"}.get(actions[c], actions[c]),
                    "reason": selection.reasons[uuid.UUID(c)],
                    "mastery": round(state["mastery_snapshot"][c], 4),
                }
                for c in targets
            ],
            "session_type": state["session_type"],
            "estimated_duration_minutes": min(state["time_budget_minutes"], 6 * len(targets) + 4),
        }
        state["next_action"] = actions[targets[0]]
        self._log(
            state,
            "concept_changed",
            targets[0],
            {"from": None, "to": targets[0], "objective": state["objective"]},
        )
        await self._emit(
            state,
            "session_started",
            {"session_id": state["session_id"], "objective": state["objective"]},
        )
        return state

    async def begin(self, state: SessionState) -> SessionState:
        """First action of a session created with ``plan_only`` (the client is now listening)."""
        state["plan_only"] = False
        state["status"] = "active"
        return state

    async def acknowledge(self, state: SessionState) -> SessionState:
        understood = bool((state["pending_input"] or {}).get("payload", {}).get("understood"))
        concept = state["current_concept_id"] or ""
        self._remember(
            state, "Student said they understood" if understood else "Student is still confused"
        )
        if understood:
            state["next_action"] = "review" if concept in state["review_concepts"] else "practice"
        else:
            state["next_action"] = "explain"
            state["explain_kind"] = (
                "worked_example" if state["explanations"].get(concept, 0) >= 2 else "retry"
            )
        return state

    async def decide(self, state: SessionState) -> SessionState:
        self._enter(state, "decide")
        decision = decide_next(state, self.cfg, self.d.now())
        if decision.frustration:
            state["frustration_activations"] += 1
            state["difficulty_offset"] = round(
                state["difficulty_offset"] - self.cfg.frustration_difficulty_drop, 3
            )
            state["consecutive_failures"] = 0
            state["frustration_reset_index"] = len(state["attempts"])
            self._log(
                state,
                "frustration_detected",
                state["current_concept_id"],
                {"activation": state["frustration_activations"]},
            )
        if decision.message:
            await self._emit(
                state, "tutor_message", {"kind": "encouragement", "content": decision.message}
            )
        if decision.action == "switch" and decision.switch_to:
            self._switch_concept(state, decision.switch_to)
            state["next_action"] = initial_action(state, decision.switch_to, self.cfg)
            state["explain_kind"] = None
        else:
            state["next_action"] = decision.action
            state["explain_kind"] = decision.explain_kind
        if decision.end_reason:
            state["end_reason"] = decision.end_reason
        return state

    async def explain(self, state: SessionState) -> SessionState:
        self._enter(state, "explain")
        concept = state["current_concept_id"] or ""
        context = await self._tutor_context(state, concept)
        kind = state.get("explain_kind") or (
            "intro" if not state["explanations"].get(concept) else "retry"
        )
        extra: dict[str, Any] = {}
        last_eval = state.get("last_evaluation") or {}
        if kind == "retry":
            extra = {
                "previous_explanation": state["last_explanation"][:1500],
                "student_error": str(last_eval.get("response", ""))[:1000]
                if last_eval.get("concept_id") == concept
                else "",
            }
        elif kind == "socratic":
            extra = {"student_response": str(last_eval.get("response", ""))[:1000]}
        await self._emit(
            state,
            "explanation_start",
            {"concept_id": concept, "concept_name": context.concept.name, "kind": kind},
        )

        async def on_delta(delta: str) -> None:
            await self._emit(state, "explanation_chunk", {"content": delta}, record=False)

        text = await self.tutor.explain(kind, context, self.d.ai, on_delta=on_delta, **extra)  # type: ignore[arg-type]
        state["outbox"].append({"type": "explanation_chunk", "payload": {"content": text}})
        await self._emit(state, "explanation_end", {})
        state["last_explanation"] = text[:4000]
        state["explanations"] = {
            **state["explanations"],
            concept: state["explanations"].get(concept, 0) + 1,
        }
        state["current_action"] = "explain"
        state["explain_kind"] = None
        state["action_history"] = [*state["action_history"], f"explain:{concept}"]
        if concept not in state["concepts_covered"]:
            state["concepts_covered"] = [*state["concepts_covered"], concept]
        self._log(
            state,
            "explanation_given",
            concept,
            {"kind": kind, "characters": len(text), "materials": len(context.material)},
        )
        self._remember(state, f"Tutor gave a {kind} explanation of {context.concept.name}")
        state["awaiting"] = "acknowledgement"
        return state

    async def _ask(self, state: SessionState, mode: str) -> SessionState:
        concept = state["current_concept_id"] or ""
        brief = self._concept(state, concept)
        mastery = state["mastery_snapshot"].get(concept, 0.0)
        recent = [a["score"] for a in state["attempts"] if a["concept_id"] == concept][-5:]
        target = compute_target_difficulty(
            mastery, brief.difficulty, recent, mode, state["difficulty_offset"]
        )
        qtype = select_question_type(mastery, state["question_types"].get(concept, []))
        material = await self._material(brief)
        profile = state["student_profile"]
        question, reused = await self.questions.next_question(
            self.user_id,
            QuestionRequest(
                concept_id=brief.id,
                concept_name=brief.name,
                concept_description=brief.description or "",
                target_difficulty=target,
                question_type=qtype,
                content_snippets=snippets_text(material),
                student_level=student_level(
                    profile.get("grade_level"), profile.get("difficulty_band")
                ),
                known_misconceptions=await self.misconceptions.known_for_concept(
                    self.user_id, brief.id
                ),
                avoid=state["recent_question_texts"],
            ),
            self.d.ai,
            exclude=[uuid.UUID(q) for q in state["asked_question_ids"]],
            session_id=uuid.UUID(state["session_id"]),
        )
        qid = str(question.id)
        state["current_question"] = {
            "id": qid,
            "concept_id": concept,
            "type": question.question_type,
            "difficulty": question.difficulty,
            "target_difficulty": target,
            "content": question.content,
            "options": public_options(question),
            "hints": list(question.hints or []),
            "hints_given": 0,
            "mode": mode,
            "asked_at": self.d.now().isoformat(),
        }
        state["asked_question_ids"] = [*state["asked_question_ids"], qid]
        state["recent_question_texts"] = [*state["recent_question_texts"], question.content[:200]][
            -5:
        ]
        state["question_types"] = {
            **state["question_types"],
            concept: [*state["question_types"].get(concept, []), qtype][-5:],
        }
        state["current_action"] = mode
        state["action_history"] = [*state["action_history"], f"{mode}:{concept}"]
        if concept not in state["concepts_covered"]:
            state["concepts_covered"] = [*state["concepts_covered"], concept]
        await self._emit(
            state,
            "question",
            {
                "question_id": qid,
                "concept_id": concept,
                "type": question.question_type,
                "difficulty": round(question.difficulty, 3),
                "content": question.content,
                "options": public_options(question),
                "hints_available": len(question.hints or []),
                "mode": mode,
            },
        )
        self._log(
            state,
            "question_asked",
            concept,
            {
                "question_id": qid,
                "type": qtype,
                "difficulty": question.difficulty,
                "target_difficulty": target,
                "reused": reused,
                "mode": mode,
            },
        )
        self._remember(
            state,
            f"Asked a {qtype} {mode} question on {brief.name} "
            f"(difficulty {question.difficulty:.2f})",
        )
        state["awaiting"] = "answer"
        return state

    async def practice(self, state: SessionState) -> SessionState:
        self._enter(state, "practice")
        return await self._ask(state, "practice")

    async def review(self, state: SessionState) -> SessionState:
        self._enter(state, "review")
        return await self._ask(state, "review")

    async def respond(self, state: SessionState) -> SessionState:
        """Follow-up questions and hints — answered without leaving the current step."""
        msg = state["pending_input"] or {}
        concept = state["current_concept_id"] or ""
        question = state.get("current_question")
        if msg.get("type") == "request_hint":
            if question and question["hints_given"] < len(question["hints"]):
                number = question["hints_given"] + 1
                hint = question["hints"][number - 1]
                state["current_question"] = {**question, "hints_given": number}
                await self._emit(
                    state,
                    "hint",
                    {"hint_number": number, "content": hint, "question_id": question["id"]},
                )
                self._log(
                    state,
                    "hint_given",
                    concept,
                    {"question_id": question["id"], "hint_number": number},
                )
            else:
                await self._emit(
                    state,
                    "tutor_message",
                    {
                        "kind": "no_more_hints",
                        "content": "No more hints for this one — give it your best try!",
                    },
                )
            return state

        text = str(msg.get("payload", {}).get("content", ""))
        self._log(state, "follow_up_question", concept, {"content": text[:1000]})
        context = await self._tutor_context(state, concept)
        if question:
            context.history.append("An assessment question is open — do not give away its answer.")
        await self._emit(
            state,
            "explanation_start",
            {"concept_id": concept, "concept_name": context.concept.name, "kind": "followup"},
        )

        async def on_delta(delta: str) -> None:
            await self._emit(state, "explanation_chunk", {"content": delta}, record=False)

        answer = await self.tutor.explain(
            "followup", context, self.d.ai, on_delta=on_delta, student_question=text
        )
        state["outbox"].append({"type": "explanation_chunk", "payload": {"content": answer}})
        await self._emit(state, "explanation_end", {})
        self._log(state, "follow_up_answer", concept, {"characters": len(answer)})
        self._remember(state, f"Student asked: {text[:120]}")
        return state

    async def evaluate(self, state: SessionState) -> SessionState:
        self._enter(state, "evaluate")
        msg = state["pending_input"] or {}
        payload = msg.get("payload", {})
        current = state["current_question"] or {}
        question = await self.d.session.scalar(
            select(Question).where(
                Question.id == uuid.UUID(current["id"]), Question.user_id == self.user_id
            )
        )
        if question is None:  # pragma: no cover - deleted underneath the session
            raise SessionPlanningError("The question is no longer available")
        concept = current["concept_id"]
        result = await self.evaluator.evaluate(
            question,
            str(payload.get("content", "")),
            self.d.ai,
            concept_name=state["concept_info"].get(concept, {}).get("name", ""),
            mastery=state["mastery_snapshot"].get(concept, 0.0),
        )
        state["last_evaluation"] = {
            **result.to_dict(),
            "question_id": current["id"],
            "concept_id": concept,
            "response": str(payload.get("content", ""))[:4000],
            "time_taken": payload.get("time_taken_seconds"),
            "difficulty": question.difficulty,
            "correct_answer": answer_text(question),
        }
        return state

    async def update(self, state: SessionState) -> SessionState:
        self._enter(state, "update")
        ev = state["last_evaluation"] or {}
        concept = ev["concept_id"]
        concept_uuid = uuid.UUID(concept)
        mode = (state["current_question"] or {}).get("mode", "practice")
        change = await self.tracker.record_attempt(
            self.user_id,
            concept_uuid,
            is_correct=ev["is_correct"],
            score=ev["score"],
            difficulty=ev["difficulty"],
            event=mode,
        )
        attempt = QuestionAttempt(
            question_id=uuid.UUID(ev["question_id"]),
            user_id=self.user_id,
            session_id=uuid.UUID(state["session_id"]),
            student_response=ev["response"] or "(empty)",
            is_correct=ev["is_correct"],
            score=ev["score"],
            ai_evaluation={
                k: ev[k]
                for k in ("explanation", "method", "follow_up_suggestion", "selected_option")
            },
            misconceptions_detected=[],
            time_taken_seconds=ev.get("time_taken"),
            mastery_delta=change.delta,
        )
        self.d.session.add(attempt)
        await self.d.session.flush()

        detected = [DetectedMisconception(**m) for m in ev.get("misconceptions", [])]
        recorded = await self.misconceptions.record(
            self.user_id, concept_uuid, detected, attempt_id=attempt.id
        )
        attempt.misconceptions_detected = [
            {
                "misconception_id": str(r.misconception_id),
                "name": r.name,
                "confidence": r.confidence,
            }
            for r in recorded
        ]
        resolved = 0
        if change.streak >= RESOLVE_AFTER_STREAK:
            resolved = await self.misconceptions.resolve_for_concept(self.user_id, concept_uuid)

        state["mastery_snapshot"] = {**state["mastery_snapshot"], concept: change.new_level}
        state["attempts"] = [
            *state["attempts"],
            {
                "concept_id": concept,
                "question_id": ev["question_id"],
                "is_correct": ev["is_correct"],
                "score": ev["score"],
                "difficulty": ev["difficulty"],
                "time_taken": ev.get("time_taken"),
                "mode": mode,
            },
        ]
        state["concept_attempts"] = {
            **state["concept_attempts"],
            concept: state["concept_attempts"].get(concept, 0) + 1,
        }
        state["consecutive_failures"] = 0 if ev["is_correct"] else state["consecutive_failures"] + 1
        update = {
            "concept_id": concept,
            "old_mastery": change.old_level,
            "new_mastery": change.new_level,
            "label": mastery_label(change.new_level),
        }
        state["mastery_updates"] = [*state["mastery_updates"], update]
        state["current_question"] = None
        state["awaiting"] = "none"

        await self._emit(
            state,
            "evaluation",
            {
                "question_id": ev["question_id"],
                "is_correct": ev["is_correct"],
                "score": ev["score"],
                "explanation": ev["explanation"],
                "correct_answer": None if ev["is_correct"] else ev["correct_answer"],
                "mastery_update": update,
            },
        )
        self._log(
            state,
            "answer_received",
            concept,
            {
                "question_id": ev["question_id"],
                "attempt_id": str(attempt.id),
                "is_correct": ev["is_correct"],
                "score": ev["score"],
                "method": ev["method"],
            },
        )
        self._log(state, "mastery_updated", concept, update)
        for r in recorded:
            item = {
                "misconception_id": str(r.misconception_id),
                "name": r.name,
                "status": r.status,
                "confidence": r.confidence,
                "concept_id": concept,
            }
            state["misconceptions_found"] = [*state["misconceptions_found"], item]
            await self._emit(
                state,
                "misconception_detected",
                {
                    "misconception_name": r.name,
                    "explanation": misconception_message(r.name, r.description),
                    "status": r.status,
                },
            )
            self._log(state, "misconception_detected", concept, item)
        if resolved:
            self._log(state, "misconceptions_resolved", concept, {"count": resolved})
        self._remember(
            state,
            f"Student answered {'correctly' if ev['is_correct'] else 'incorrectly'} "
            f"(score {ev['score']:.2f}); "
            f"mastery {change.old_level:.2f} → {change.new_level:.2f}",
        )
        return state

    async def wrap(self, state: SessionState) -> SessionState:
        self._enter(state, "wrap")
        now = self.d.now()
        end_reason = state.get("end_reason") or (
            "student_ended"
            if (state.get("pending_input") or {}).get("type") == "end_session"
            else "completed"
        )
        state["end_reason"] = end_reason
        attempts = state["attempts"]
        covered = list(state["concepts_covered"])
        changes = [
            {
                "concept_id": c,
                "concept": state["concept_info"].get(c, {}).get("name", c),
                "from": round(state["initial_mastery"].get(c, 0.0), 4),
                "to": round(state["mastery_snapshot"].get(c, 0.0), 4),
            }
            for c in covered
        ]
        minutes = elapsed_minutes(state, now)
        summary: dict[str, Any] = {
            "duration_minutes": round(minutes, 1),
            "concepts_covered": len(covered),
            "questions_answered": len(attempts),
            "accuracy": round(sum(1 for a in attempts if a["is_correct"]) / len(attempts), 3)
            if attempts
            else 0.0,
            "mastery_changes": changes,
            "misconceptions_found": len(state["misconceptions_found"]),
            "end_reason": end_reason,
        }
        summary["text"] = await self._summary_text(state, summary)
        state["summary"] = summary
        state["status"] = "completed"
        state["current_question"] = None
        self._log(
            state, "session_completed", None, {k: v for k, v in summary.items() if k != "text"}
        )
        return state

    async def _summary_text(self, state: SessionState, summary: dict[str, Any]) -> str:
        concepts = [
            {
                "name": c["concept"],
                "from_percent": round(c["from"] * 100),
                "to_percent": round(c["to"] * 100),
            }
            for c in summary["mastery_changes"]
        ]
        fallback = (
            f"You studied for {round(summary['duration_minutes'])} minutes and answered "
            f"{summary['questions_answered']} question(s). "
            + (
                "Progress: "
                + ", ".join(
                    f"{c['name']} {c['from_percent']}% → {c['to_percent']}%" for c in concepts
                )
                + ". "
                if concepts
                else ""
            )
            + "Great effort — come back soon to keep building on it!"
        )
        if state.get("no_llm") or (not summary["questions_answered"] and not concepts):
            return fallback
        prompt = self.d.prompts.render(
            SUMMARY_PROMPT,
            duration_minutes=round(summary["duration_minutes"]),
            questions_answered=summary["questions_answered"],
            accuracy_percent=round(summary["accuracy"] * 100),
            end_reason=summary["end_reason"],
            concepts=concepts,
        )
        try:
            response = await self.d.llm.complete(
                SUMMARY_TASK,
                [LLMMessage(role="user", content=prompt.text)],
                self.d.ai.for_purpose(
                    "session_summary", prompt_name=SUMMARY_PROMPT, prompt_version=prompt.version
                ),
                json_mode=False,
            )
        except LLMError as exc:
            logger.warning("session summary fell back to template", extra={"error": exc.code})
            return fallback
        return response.content.strip()[:2000] or fallback

    async def schedule(self, state: SessionState) -> SessionState:
        self._enter(state, "schedule")
        today = self.d.now().date()
        reviews = []
        by_concept: dict[str, list[float]] = {}
        for a in state["attempts"]:
            by_concept.setdefault(a["concept_id"], []).append(a["score"])
        for concept, scores in by_concept.items():
            schedule = await self.tracker.schedule_review(
                self.user_id, uuid.UUID(concept), mean(scores), today=today
            )
            reviews.append(
                {
                    "concept_id": concept,
                    "next_review_date": schedule.next_review_date.isoformat(),
                    "interval_days": schedule.next_interval_days,
                    "ease_factor": schedule.new_ease_factor,
                }
            )
        summary = dict(state["summary"] or {})
        summary["reviews"] = reviews
        state["summary"] = summary
        await self._update_profile_stats(summary, today)
        state["awaiting"] = "ended"
        await self._emit(
            state,
            "session_ended",
            {
                "summary": {
                    k: summary[k]
                    for k in (
                        "duration_minutes",
                        "concepts_covered",
                        "questions_answered",
                        "accuracy",
                        "mastery_changes",
                        "text",
                        "reviews",
                        "end_reason",
                    )
                }
            },
        )
        return state

    async def _update_profile_stats(self, summary: dict[str, Any], today: date) -> None:
        profile = await self.d.session.scalar(
            select(StudentProfile).where(StudentProfile.user_id == self.user_id)
        )
        if profile is None:
            return
        mastered = await self.d.session.scalar(
            select(func.count(StudentConceptMastery.id)).where(
                StudentConceptMastery.user_id == self.user_id,
                StudentConceptMastery.mastery_level >= self.cfg.mastery_learned_threshold,
            )
        )
        stats = dict(profile.cumulative_stats or {})
        stats["total_sessions"] = int(stats.get("total_sessions", 0)) + 1
        stats["total_minutes"] = round(
            float(stats.get("total_minutes", 0)) + summary["duration_minutes"], 1
        )
        stats["total_questions"] = (
            int(stats.get("total_questions", 0)) + summary["questions_answered"]
        )
        stats["concepts_mastered"] = int(mastered or 0)
        stats["last_session_date"] = today.isoformat()
        profile.cumulative_stats = stats
        await self.d.session.flush()

    # ---- graph ---------------------------------------------------------------------------------

    @staticmethod
    def route_entry(state: SessionState) -> str:
        if not state["target_concepts"]:
            return "init"
        kind = (state.get("pending_input") or {}).get("type")
        return {
            "begin": "begin",
            "end_session": "wrap",
            "student_question": "respond",
            "request_hint": "respond",
            "student_acknowledge": "acknowledge",
            "student_response": "evaluate",
        }.get(kind or "", END)

    @staticmethod
    def route_after_plan(state: SessionState) -> str:
        return END if state.get("plan_only") else SessionEngine.route_action(state)

    @staticmethod
    def route_action(state: SessionState) -> str:
        action = state.get("next_action") or "wrap"
        return action if action in ("explain", "practice", "review", "wrap") else "wrap"

    def graph(self) -> Any:
        g: StateGraph[SessionState] = StateGraph(SessionState)
        for name in (
            "init",
            "plan",
            "begin",
            "acknowledge",
            "decide",
            "explain",
            "practice",
            "review",
            "respond",
            "evaluate",
            "update",
            "wrap",
            "schedule",
        ):
            g.add_node(name, getattr(self, name))
        targets: dict[Hashable, str] = {}
        for action in ("explain", "practice", "review", "wrap"):
            targets[action] = action
        entry: dict[Hashable, str] = {END: END}
        for node in ("init", "begin", "wrap", "respond", "acknowledge", "evaluate"):
            entry[node] = node
        g.add_conditional_edges(
            START,
            self.route_entry,
            entry,
        )
        g.add_edge("init", "plan")
        g.add_conditional_edges("plan", self.route_after_plan, {**targets, END: END})
        g.add_conditional_edges("begin", self.route_action, targets)
        g.add_conditional_edges("acknowledge", self.route_action, targets)
        g.add_conditional_edges("decide", self.route_action, targets)
        g.add_edge("evaluate", "update")
        g.add_edge("update", "decide")
        g.add_edge("wrap", "schedule")
        for terminal in ("explain", "practice", "review", "respond", "schedule"):
            g.add_edge(terminal, END)
        return g.compile()

    async def run_turn(self, state: SessionState) -> SessionState:
        result = await self.graph().ainvoke(state, {"recursion_limit": 50})
        return cast(SessionState, dict(result))
