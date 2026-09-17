# Learning Engine — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Purpose

The Learning Engine is the core adaptive intelligence of School in a Box. It orchestrates the flow of a learning session, deciding what to teach, when to quiz, how to adapt, and when to stop. It is the component that makes the platform *adaptive*.

---

## 2. Learning Engine Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      LEARNING ENGINE                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                  Session Orchestrator                       │  │
│  │                  (LangGraph Workflow)                       │  │
│  │                                                             │  │
│  │  INIT → PLAN → [EXPLAIN|PRACTICE|REVIEW] → EVALUATE       │  │
│  │                  → UPDATE → DECIDE → ... → WRAPUP → SCHED │  │
│  └───────┬──────────────┬───────────────┬─────────────────────┘  │
│          │              │               │                        │
│  ┌───────▼──────┐ ┌────▼────────┐ ┌────▼───────────┐           │
│  │ Concept      │ │ Difficulty  │ │ Study Plan     │           │
│  │ Selector     │ │ Calibrator  │ │ Generator      │           │
│  └───────┬──────┘ └────┬────────┘ └────┬───────────┘           │
│          │              │               │                        │
│  ┌───────▼──────────────▼───────────────▼───────────────────┐   │
│  │                  Student Model                            │   │
│  │  ┌──────────┐  ┌───────────┐  ┌─────────────────────┐   │   │
│  │  │ Mastery  │  │ Misconc.  │  │ Spaced Repetition   │   │   │
│  │  │ Tracker  │  │ Tracker   │  │ Scheduler           │   │   │
│  │  └──────────┘  └───────────┘  └─────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  Content Retriever                        │   │
│  │  ┌──────────┐  ┌───────────┐  ┌─────────────────────┐   │   │
│  │  │ Vector   │  │ Keyword   │  │ Concept Graph       │   │   │
│  │  │ Search   │  │ Search    │  │ Traversal           │   │   │
│  │  └──────────┘  └───────────┘  └─────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Concept Selection Algorithm

### 3.1 Overview

When a session begins, the system must decide *which concepts to teach*. This is the Concept Selector's job.

### 3.2 Input

```python
@dataclass
class ConceptSelectionInput:
    user_id: str
    learning_goal: LearningGoal | None         # active goal (if any)
    due_reviews: list[ReviewItem]               # overdue + today's reviews
    mastery_snapshot: dict[str, float]           # concept_id → mastery_level
    concept_graph: ConceptGraph                  # prerequisite relationships
    session_type: str                            # teach | practice | review | mixed
    max_concepts: int                            # max concepts per session (default: 5)
```

### 3.3 Selection Rules (Priority Order)

```python
def select_concepts(input: ConceptSelectionInput) -> list[str]:
    candidates = []
    
    # Priority 1: Overdue reviews (spaced repetition obligations)
    overdue = [r for r in input.due_reviews if r.status == 'overdue']
    candidates.extend(sorted(overdue, key=lambda r: r.priority, reverse=True))
    
    # Priority 2: Today's scheduled reviews
    today_reviews = [r for r in input.due_reviews if r.status == 'pending']
    candidates.extend(sorted(today_reviews, key=lambda r: r.priority, reverse=True))
    
    # Priority 3: Goal-driven concepts (lowest mastery first, prerequisites satisfied)
    if input.learning_goal:
        goal_concepts = get_goal_concepts(input.learning_goal)
        teachable = filter_prerequisites_met(goal_concepts, input.mastery_snapshot, input.concept_graph)
        candidates.extend(sorted(teachable, key=lambda c: input.mastery_snapshot.get(c, 0)))
    
    # Priority 4: Weakest concepts across all subjects
    all_concepts = get_all_user_concepts(input.user_id)
    weakest = sorted(all_concepts, key=lambda c: input.mastery_snapshot.get(c, 0))
    candidates.extend(weakest)
    
    # Deduplicate and limit
    seen = set()
    result = []
    for c in candidates:
        concept_id = c if isinstance(c, str) else c.concept_id
        if concept_id not in seen:
            seen.add(concept_id)
            result.append(concept_id)
        if len(result) >= input.max_concepts:
            break
    
    return result
```

### 3.4 Prerequisite Check

```python
def filter_prerequisites_met(
    concepts: list[str],
    mastery: dict[str, float],
    graph: ConceptGraph,
    threshold: float = 0.6,   # prerequisite must be at this mastery to proceed
) -> list[str]:
    """Filter to concepts whose prerequisites are sufficiently mastered."""
    result = []
    for concept_id in concepts:
        prereqs = graph.get_prerequisites(concept_id)
        if all(mastery.get(p, 0) >= threshold for p in prereqs):
            result.append(concept_id)
    return result
```

---

## 4. Mastery Update Algorithm

### 4.1 Overview

After every student response (question attempt), the system updates the student's mastery for the relevant concept.

### 4.2 Bayesian Knowledge Tracing (Simplified)

```python
@dataclass
class MasteryUpdateInput:
    current_mastery: float       # P(known) ∈ [0, 1]
    is_correct: bool
    score: float                 # partial credit ∈ [0, 1]
    question_difficulty: float   # ∈ [0, 1]
    slip_rate: float = 0.1      # P(incorrect | known)
    guess_rate: float = 0.25    # P(correct | not known)
    learning_rate: float = 0.1  # P(transition to known)

def update_mastery(input: MasteryUpdateInput) -> float:
    """Bayesian Knowledge Tracing update."""
    p_known = input.current_mastery
    
    if input.is_correct:
        # P(known | correct) using Bayes' theorem
        p_correct_given_known = 1 - input.slip_rate
        p_correct_given_unknown = input.guess_rate
        p_correct = p_known * p_correct_given_known + (1 - p_known) * p_correct_given_unknown
        p_known_updated = (p_known * p_correct_given_known) / p_correct
    else:
        # P(known | incorrect)
        p_incorrect_given_known = input.slip_rate
        p_incorrect_given_unknown = 1 - input.guess_rate
        p_incorrect = p_known * p_incorrect_given_known + (1 - p_known) * p_incorrect_given_unknown
        p_known_updated = (p_known * p_incorrect_given_known) / p_incorrect
    
    # Apply learning transition (opportunity to learn from the interaction)
    p_known_after_learning = p_known_updated + (1 - p_known_updated) * input.learning_rate
    
    # Difficulty adjustment: harder questions give more signal
    confidence_weight = 0.5 + 0.5 * input.question_difficulty
    final_mastery = (
        input.current_mastery * (1 - confidence_weight) +
        p_known_after_learning * confidence_weight
    )
    
    return max(0.0, min(1.0, final_mastery))
```

### 4.3 Mastery Level Labels

| Range | Label | Meaning |
|---|---|---|
| 0.00 – 0.20 | `novice` | No demonstrated understanding |
| 0.20 – 0.40 | `beginner` | Some familiarity, frequent errors |
| 0.40 – 0.60 | `intermediate` | Partial understanding, inconsistent |
| 0.60 – 0.80 | `proficient` | Solid understanding, occasional gaps |
| 0.80 – 1.00 | `mastered` | Reliable knowledge, ready for review scheduling |

### 4.4 Mastery Threshold

- **"Learned" threshold:** 0.80 (concept moves from active teaching to review mode).
- **Prerequisite threshold:** 0.60 (sufficient to proceed to dependent concepts).
- **Review trigger:** mastery drops below 0.70 due to time decay → re-enters review queue.

---

## 5. Spaced Repetition (SM-2 Variant)

### 5.1 Algorithm

```python
@dataclass
class SM2Input:
    quality: int                # 0-5 rating (derived from score)
    repetition_count: int
    ease_factor: float          # ≥ 1.3, default 2.5
    interval_days: float        # current interval

@dataclass
class SM2Output:
    next_interval_days: float
    new_ease_factor: float
    new_repetition_count: int
    next_review_date: date

def sm2_schedule(input: SM2Input) -> SM2Output:
    """SM-2 spaced repetition scheduling."""
    quality = input.quality
    
    if quality < 3:
        # Failed review: reset
        new_repetition = 0
        new_interval = 1.0
    else:
        if input.repetition_count == 0:
            new_interval = 1.0
        elif input.repetition_count == 1:
            new_interval = 6.0
        else:
            new_interval = input.interval_days * input.ease_factor
        new_repetition = input.repetition_count + 1
    
    # Update ease factor
    new_ease = input.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ease = max(1.3, new_ease)
    
    return SM2Output(
        next_interval_days=new_interval,
        new_ease_factor=new_ease,
        new_repetition_count=new_repetition,
        next_review_date=date.today() + timedelta(days=int(new_interval)),
    )

def score_to_quality(score: float) -> int:
    """Convert a 0.0-1.0 score to SM-2's 0-5 quality rating."""
    if score >= 0.95: return 5
    if score >= 0.80: return 4
    if score >= 0.60: return 3
    if score >= 0.40: return 2
    if score >= 0.20: return 1
    return 0
```

### 5.2 Time-Based Mastery Decay

Between sessions, mastery decays to simulate forgetting:

```python
def apply_mastery_decay(
    mastery: float,
    days_since_review: int,
    ease_factor: float,
) -> float:
    """Ebbinghaus-inspired forgetting curve."""
    stability = ease_factor * 10  # higher ease = slower decay
    retention = math.exp(-days_since_review / stability)
    decayed_mastery = mastery * retention
    return max(0.0, decayed_mastery)
```

**Decay is applied:**
- At session start (refresh mastery for concepts being studied).
- By a daily Celery Beat job (batch update all mastery records).

---

## 6. Difficulty Calibration

### 6.1 Target Difficulty Selection

```python
def compute_target_difficulty(
    mastery: float,
    concept_difficulty: float,
    recent_scores: list[float],    # last 3-5 scores on this concept
    session_mode: str,
) -> float:
    """Compute target question difficulty for Zone of Proximal Development."""
    
    # Base: aim for ~70% expected success rate
    base_difficulty = mastery * 0.7 + concept_difficulty * 0.3
    
    # Trend adjustment
    if len(recent_scores) >= 3:
        trend = recent_scores[-1] - recent_scores[0]  # positive = improving
        base_difficulty += trend * 0.1
    
    # Mode adjustment
    if session_mode == 'review':
        base_difficulty -= 0.1   # Slightly easier for review
    elif session_mode == 'practice':
        pass                     # Standard difficulty
    
    return max(0.1, min(0.95, base_difficulty))
```

### 6.2 Question Type Selection

| Mastery Level | Preferred Question Types | Rationale |
|---|---|---|
| novice (0.0–0.2) | `true_false`, `mcq` (easy) | Recognition before recall |
| beginner (0.2–0.4) | `mcq`, `short_answer` (simple) | Guided recall with options |
| intermediate (0.4–0.6) | `short_answer`, `worked_problem` | Active recall required |
| proficient (0.6–0.8) | `worked_problem`, `open_ended` | Application and synthesis |
| mastered (0.8–1.0) | `open_ended`, `worked_problem` (hard) | Transfer and edge cases |

---

## 7. Session Decision Logic

### 7.1 Action Selection: EXPLAIN vs PRACTICE vs REVIEW

```python
def decide_action(
    concept_id: str,
    mastery: float,
    session_type: str,
    previous_action: str | None,
    is_review_item: bool,
) -> str:
    """Decide the next pedagogical action."""
    
    # Review items always start with review
    if is_review_item and previous_action is None:
        return 'review'
    
    # First encounter with a concept: explain first
    if mastery < 0.2 and previous_action != 'explain':
        return 'explain'
    
    # After explanation, test comprehension
    if previous_action == 'explain':
        return 'practice'
    
    # Low mastery after practice: re-explain
    if mastery < 0.4 and previous_action == 'practice':
        return 'explain'
    
    # Default: practice
    return 'practice'
```

### 7.2 Session Termination Conditions

A session ends when any of these conditions are met:

| Condition | Check |
|---|---|
| Time budget exceeded | `elapsed_minutes >= time_budget_minutes` |
| Interaction limit reached | `interaction_count >= max_interactions` |
| All concepts mastered | All target concepts have mastery ≥ 0.80 |
| No more concepts | `concepts_remaining` is empty and current concept is mastered |
| Student requests end | Explicit "end session" from student |
| Consecutive failures (frustration guard) | 5+ consecutive incorrect answers → suggest break |
| Error/timeout | LLM failure after retries → graceful end |

### 7.3 Frustration Detection

```python
def check_frustration(state: SessionState) -> bool:
    """Detect if student may be frustrated."""
    recent_attempts = get_recent_attempts(state, n=5)
    
    # 5 consecutive failures
    if len(recent_attempts) >= 5 and all(not a.is_correct for a in recent_attempts):
        return True
    
    # Response time increasing dramatically (fatigue signal)
    if len(recent_attempts) >= 3:
        times = [a.time_taken_seconds for a in recent_attempts]
        if times[-1] > times[0] * 2.5:
            return True
    
    return False
```

**Frustration response:**
- Reduce difficulty by 0.2.
- Switch to an easier concept.
- If persistent, suggest ending the session with an encouraging message.

---

## 8. Study Plan Generation

### 8.1 Overview

A study plan is a prioritised schedule of what to study and when.

### 8.2 Generation Algorithm

```python
def generate_study_plan(
    user_id: str,
    goal: LearningGoal,
    mastery_data: dict[str, StudentConceptMastery],
    concept_graph: ConceptGraph,
    days_until_deadline: int | None,
) -> StudyPlan:
    """Generate a study plan for a learning goal."""
    
    # 1. Get all concepts for this goal
    target_concepts = get_goal_concepts(goal)
    
    # 2. Topological sort by prerequisites
    sorted_concepts = topological_sort(target_concepts, concept_graph)
    
    # 3. Filter out already-mastered concepts
    to_learn = [c for c in sorted_concepts if mastery_data.get(c, 0) < 0.80]
    
    # 4. Estimate time per concept (based on difficulty and current mastery)
    time_estimates = {}
    for concept_id in to_learn:
        mastery = mastery_data.get(concept_id, 0)
        difficulty = get_concept_difficulty(concept_id)
        # Rough estimate: 15-45 minutes per concept, depending on gap
        time_estimates[concept_id] = int(15 + 30 * (1 - mastery) * difficulty)
    
    # 5. Distribute across available days
    if days_until_deadline:
        concepts_per_day = max(1, len(to_learn) // days_until_deadline)
    else:
        concepts_per_day = 3  # default: 3 new concepts per day
    
    # 6. Create review items
    review_items = []
    current_date = date.today()
    for i, concept_id in enumerate(to_learn):
        day_offset = i // concepts_per_day
        scheduled = current_date + timedelta(days=day_offset)
        priority = 1.0 - (i / len(to_learn))  # earlier items are higher priority
        review_items.append(ReviewItem(
            concept_id=concept_id,
            scheduled_date=scheduled,
            priority=priority,
            status='pending',
        ))
    
    # 7. Interleave review items for previously learned concepts (spaced repetition)
    due_reviews = get_due_reviews(user_id)
    for review in due_reviews:
        review_items.append(review)
    
    # Sort by date, then priority
    review_items.sort(key=lambda r: (r.scheduled_date, -r.priority))
    
    return StudyPlan(
        user_id=user_id,
        learning_goal_id=goal.id,
        items=review_items,
        status='active',
    )
```

### 8.3 Plan Recalculation Triggers

| Trigger | Action |
|---|---|
| Session completed | Recalculate based on new mastery data |
| Goal created/modified | Generate new plan |
| Student skips multiple reviews | Re-prioritise overdue items |
| New document processed | Add newly extracted concepts |

---

## 9. Content Retrieval for Learning

### 9.1 Retrieval Strategy

When the Tutor or Assessment Engine needs content for a concept:

```python
async def retrieve_learning_content(
    user_id: str,
    concept_id: str,
    purpose: str,      # 'explain' | 'generate_question' | 'provide_context'
    max_chunks: int = 5,
) -> list[DocumentSection]:
    """Hybrid retrieval of relevant content for a concept."""
    
    # 1. Get concept details
    concept = await concept_repo.get(concept_id, user_id)
    
    # 2. Vector search (semantic similarity)
    query_text = f"{concept.name}: {concept.description}"
    vector_results = await qdrant_client.search(
        collection="document_sections",
        query_vector=await embed(query_text),
        filter={"user_id": user_id},
        limit=max_chunks * 2,
    )
    
    # 3. Keyword search (PostgreSQL full-text)
    keyword_results = await doc_section_repo.full_text_search(
        user_id=user_id,
        query=concept.name,
        limit=max_chunks,
    )
    
    # 4. Concept-linked sections (direct associations)
    linked_sections = await doc_section_repo.get_by_concept(
        concept_id=concept_id,
        limit=max_chunks,
    )
    
    # 5. Merge, deduplicate, rank
    all_results = merge_and_rank(vector_results, keyword_results, linked_sections)
    
    return all_results[:max_chunks]
```

### 9.2 Context Window Management

Each AI call has a limited context window. The Learning Engine manages this budget:

```python
def build_context(
    concept: Concept,
    content: list[DocumentSection],
    student: StudentProfile,
    mastery: StudentConceptMastery,
    session_history: list[SessionEvent],
    max_tokens: int = 4000,        # context budget for retrieved content
) -> str:
    """Build the context string within token budget."""
    
    context_parts = []
    token_count = 0
    
    # Always include concept info (small)
    concept_text = f"Concept: {concept.name}\n{concept.description}"
    context_parts.append(concept_text)
    token_count += estimate_tokens(concept_text)
    
    # Add content chunks, most relevant first
    for chunk in content:
        chunk_tokens = estimate_tokens(chunk.content)
        if token_count + chunk_tokens > max_tokens:
            break
        context_parts.append(chunk.content)
        token_count += chunk_tokens
    
    # Add recent session history (last 5 events)
    recent_history = format_session_history(session_history[-5:])
    if token_count + estimate_tokens(recent_history) <= max_tokens + 500:  # small buffer
        context_parts.append(recent_history)
    
    return "\n\n---\n\n".join(context_parts)
```

---

## 10. Learning Engine Configuration

All Learning Engine parameters are configurable:

```yaml
learning_engine:
  # Mastery
  mastery_learned_threshold: 0.80
  mastery_prerequisite_threshold: 0.60
  mastery_review_trigger: 0.70
  
  # Session
  default_max_interactions: 20
  default_time_budget_minutes: 30
  max_concepts_per_session: 5
  frustration_consecutive_failures: 5
  
  # Difficulty
  target_success_rate: 0.70
  difficulty_adjustment_step: 0.1
  
  # BKT parameters
  bkt_slip_rate: 0.10
  bkt_guess_rate: 0.25
  bkt_learning_rate: 0.10
  
  # Spaced repetition
  sm2_initial_ease_factor: 2.5
  sm2_minimum_ease_factor: 1.3
  
  # Decay
  decay_enabled: true
  decay_check_interval_hours: 24
  
  # Content retrieval
  max_content_chunks: 5
  context_token_budget: 4000
  
  # Study plan
  default_concepts_per_day: 3
```
