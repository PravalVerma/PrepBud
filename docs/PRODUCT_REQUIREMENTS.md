# Product Requirements — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Product Vision

**Build an AI adaptive learning platform that helps a student learn from their curriculum and study material by continuously assessing knowledge, teaching concepts, diagnosing misconceptions, adapting difficulty, and scheduling revision.**

School in a Box is a personal AI tutor that:

- Accepts any curriculum or study material a student uploads.
- Decomposes material into a structured concept graph.
- Continuously assesses what the student knows and doesn't know.
- Teaches, quizzes, explains, and reviews — adapting in real time.
- Maintains a persistent model of the student's mastery.
- Schedules future revision using spaced-repetition principles.

---

## 2. Core Actors

| Actor | Role | Agency |
|---|---|---|
| **Student** | The primary human user. Uploads material, sets goals, engages in learning sessions, answers questions, reads explanations. | Human |
| **Learning Orchestrator** | The top-level AI coordinator. Decides *what* to do next in a session: teach, quiz, review, or end. Selects concepts, adapts difficulty. | AI (stateful workflow) |
| **Tutor** | Generates explanations, worked examples, analogies, and Socratic dialogue. Adapts language to the student's level. | AI (LLM-driven) |
| **Assessment Engine** | Generates questions at calibrated difficulty, evaluates answers, diagnoses misconceptions, and scores mastery updates. | AI + deterministic logic |
| **Student Model** | A persistent data structure representing what the student knows. Tracks concept mastery, misconceptions, learning velocity, and review schedule. | Data + algorithms |
| **Content / Retrieval System** | Ingests, chunks, embeds, and retrieves learning material. Provides relevant context to the Tutor and Assessment Engine. | Deterministic + vector search |
| **Background Processing System** | Handles asynchronous work: document ingestion, embedding generation, study-plan recalculation, analytics aggregation. | Infrastructure |

---

## 3. Core Product Entities

### 3.1 User & Profile

| Entity | Description |
|---|---|
| `User` | Authentication identity. Email, OAuth, password hash reference. Links to Supabase Auth. |
| `StudentProfile` | Learning preferences, grade level / difficulty band, language, timezone, onboarding state, cumulative stats. |

### 3.2 Curriculum Structure

| Entity | Description |
|---|---|
| `LearningGoal` | A student-defined or system-suggested objective. E.g., "Master Quadratic Equations" or "Prepare for Chapter 5 Test." |
| `Subject` | Top-level academic domain. E.g., Mathematics, Physics, History. |
| `Course` | A named course within a subject. E.g., "Algebra II", "AP Physics C." |
| `Chapter` | A logical grouping within a course. |
| `Section` | A subdivision of a chapter. |
| `Concept` | The atomic unit of knowledge. A single idea, skill, or fact that can be independently assessed. |
| `ConceptRelationship` | A directed edge between two concepts: `prerequisite`, `related`, `generalisation`, `specialisation`. |

### 3.3 Content

| Entity | Description |
|---|---|
| `Document` | A student-uploaded file (PDF, image, text). Metadata: title, source, upload timestamp, processing status. |
| `DocumentSection` | A chunk of a processed document, linked to one or more Concepts. Includes embedding reference. |

### 3.4 Learning Session

| Entity | Description |
|---|---|
| `LearningSession` | A bounded interaction. Has a start time, end time, session type (`teach`, `practice`, `review`, `mixed`), objective, and outcome summary. |
| `SessionEvent` | An ordered log entry within a session: `explanation_given`, `question_asked`, `answer_received`, `hint_given`, `misconception_detected`, `mastery_updated`, `concept_changed`. |

### 3.5 Assessment

| Entity | Description |
|---|---|
| `Question` | A generated or templated question. Linked to one or more Concepts. Has difficulty level, question type (`mcq`, `short_answer`, `true_false`, `worked_problem`, `open_ended`), and content. |
| `QuestionAttempt` | A student's answer to a question. Includes raw response, correctness, time taken, AI evaluation, and mastery delta. |
| `Misconception` | A catalogued incorrect mental model. E.g., "Believes multiplication always makes numbers bigger." Linked to Concepts. |
| `StudentMisconception` | A join between Student and Misconception, with detection timestamp, status (`active`, `resolved`, `recurring`), and evidence trail. |

### 3.6 Mastery & Scheduling

| Entity | Description |
|---|---|
| `StudentConceptMastery` | Per-student, per-concept mastery record. Fields: mastery level (0.0–1.0), confidence, attempt count, last assessed, next review due, streak. |
| `StudyPlan` | A generated schedule of what to study and when. Contains ordered `ReviewItem` entries. Recalculated periodically. |
| `ReviewItem` | A single entry in a study plan: concept, scheduled date, priority, status (`pending`, `completed`, `skipped`, `overdue`). |

### 3.7 AI Observability

| Entity | Description |
|---|---|
| `AIInteraction` | A single LLM call. Records: prompt (or prompt hash), response, model used, latency, token counts, cost estimate, purpose tag. |
| `AITrace` | Groups multiple `AIInteraction` records into a logical unit of work. E.g., "Generate 3 questions for Concept X at difficulty 0.6." |

---

## 4. Primary Learning Session Flow

```
Student starts a session
  → System identifies session objective (from goal, study plan, or student choice)
  → Inspect current mastery state for relevant concepts
  → Select target concepts (weakest prerequisite-first, or due-for-review)
  → Retrieve relevant learning material from content system
  → Decide action: EXPLAIN / PRACTICE / REVIEW
  → Interact with student (teach concept, ask question, provide hint)
  → Evaluate student response
  → Update mastery model (Bayesian update or equivalent)
  → Decide next action (continue, switch concept, escalate difficulty, end)
  → Finish session (generate summary, log events)
  → Schedule future review (update spaced-repetition schedule)
```

### 4.1 Session State Machine

```
                    ┌─────────────┐
                    │  INITIALISE  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
              ┌─────│  PLAN_SESSION │─────┐
              │     └──────┬──────┘      │
              │            │              │
     ┌────────▼───┐  ┌────▼─────┐  ┌────▼──────┐
     │  EXPLAIN    │  │ PRACTICE │  │  REVIEW   │
     └────────┬───┘  └────┬─────┘  └────┬──────┘
              │            │              │
              └────────┬───┘──────────────┘
                       │
              ┌────────▼────────┐
              │ EVALUATE_RESPONSE│
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  UPDATE_MASTERY  │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  DECIDE_NEXT     │──── (loop back to EXPLAIN / PRACTICE / REVIEW)
              └────────┬────────┘
                       │ (session complete)
              ┌────────▼────────┐
              │  WRAP_UP         │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │ SCHEDULE_REVIEW  │
              └─────────────────┘
```

### 4.2 Session States — Definitions

| State | Entry Condition | Exit Condition | Key Actions |
|---|---|---|---|
| `INITIALISE` | Student requests a session | Objective identified | Load student profile, check study plan, determine session type |
| `PLAN_SESSION` | Objective known | Target concepts selected | Query mastery, rank concepts, select material, choose action mode |
| `EXPLAIN` | Concept selected, action = teach | Explanation delivered | Retrieve content, generate explanation via Tutor, log event |
| `PRACTICE` | Concept selected, action = quiz | Question answered | Generate question via Assessment Engine, present to student |
| `REVIEW` | Concept due for review | Review interaction complete | Surface previously learned concept, quick check |
| `EVALUATE_RESPONSE` | Student response received | Evaluation complete | Score answer, detect misconceptions, compute mastery delta |
| `UPDATE_MASTERY` | Evaluation complete | Mastery persisted | Write `StudentConceptMastery`, log `StudentMisconception` if any |
| `DECIDE_NEXT` | Mastery updated | Next action chosen or session ends | Check time budget, fatigue signals, mastery progress, concept queue |
| `WRAP_UP` | Session ending | Summary generated | Aggregate session stats, generate natural-language summary |
| `SCHEDULE_REVIEW` | Session wrapped up | Review items persisted | Compute next review dates using spaced-repetition algorithm |

### 4.3 Valid State Transitions

```
INITIALISE       → PLAN_SESSION
PLAN_SESSION     → EXPLAIN | PRACTICE | REVIEW
EXPLAIN          → EVALUATE_RESPONSE | PRACTICE (follow-up check)
PRACTICE         → EVALUATE_RESPONSE
REVIEW           → EVALUATE_RESPONSE
EVALUATE_RESPONSE → UPDATE_MASTERY
UPDATE_MASTERY   → DECIDE_NEXT
DECIDE_NEXT      → EXPLAIN | PRACTICE | REVIEW | WRAP_UP
WRAP_UP          → SCHEDULE_REVIEW
SCHEDULE_REVIEW  → (terminal)
```

---

## 5. MVP Scope

### 5.1 In Scope

| Capability | Notes |
|---|---|
| Student-uploaded material | PDF, images (with OCR), plain text |
| Curriculum-aligned learning | Concept extraction from uploaded material |
| Multiple subjects | No hard-coded subject logic |
| Concept-level mastery tracking | Per-student, per-concept, persistent |
| Adaptive quizzes | Difficulty adjusts based on mastery |
| Interactive tutoring | Explanations, hints, Socratic dialogue |
| Spaced repetition | SM-2 variant or similar algorithm |
| Study plans | Auto-generated, manually adjustable |
| Persistent student state | Survives sessions, supports long-term learning |
| Single-student use | One student per account |

### 5.2 Explicit Non-Goals (MVP)

| Non-Goal | Rationale |
|---|---|
| Institutional LMS features | No class management, rostering, or admin dashboards |
| Teacher analytics | No teacher-facing views or reporting |
| Full mobile applications | Responsive web only; no native iOS/Android apps |
| Social learning | No peer interaction, forums, or collaborative features |
| Content marketplace | No buying/selling/sharing of content |
| Payment / subscription infrastructure | Unless strictly needed for deployment |
| Autonomous web browsing | The system does not crawl the web for learning material |
| Gamification / badges / leaderboards | Deferred to post-MVP |
| Multi-language UI | English-only UI for MVP (content can be any language) |
| Offline mode | Requires internet connection |

---

## 6. User Stories (MVP)

### Onboarding
- **US-01**: As a student, I can create an account and set my learning preferences (grade level, subjects of interest).
- **US-02**: As a student, I can upload a PDF textbook or study notes so the system can learn from my material.
- **US-03**: As a student, I can see the processing status of my uploaded documents.

### Goal Setting
- **US-04**: As a student, I can create a learning goal (e.g., "Master Chapter 3 by October 15").
- **US-05**: As a student, I can see a list of concepts extracted from my material.
- **US-06**: As a student, I can view my concept mastery dashboard.

### Learning Sessions
- **US-07**: As a student, I can start a learning session and the system chooses what to teach me based on my goals and mastery.
- **US-08**: As a student, I receive explanations adapted to my level when learning a new concept.
- **US-09**: As a student, I can answer quiz questions and receive immediate feedback.
- **US-10**: As a student, I see my mastery update after answering questions.
- **US-11**: As a student, I can ask follow-up questions during a session.

### Review & Scheduling
- **US-12**: As a student, I can view my study plan showing what to review and when.
- **US-13**: As a student, I receive review prompts for concepts approaching their review deadline.
- **US-14**: As a student, I can complete a review session focused on spaced-repetition items.

### Progress
- **US-15**: As a student, I can see my overall progress across subjects and courses.
- **US-16**: As a student, I can see which misconceptions the system has identified and whether they're resolved.

---

## 7. Quality Attributes

| Attribute | Target |
|---|---|
| **Response latency** (tutor interaction) | < 3 seconds for explanation generation |
| **Response latency** (quiz evaluation) | < 2 seconds |
| **Document processing** | < 5 minutes for a 200-page PDF |
| **Mastery accuracy** | Mastery score correlates with actual performance (validated by periodic calibration checks) |
| **Availability** | 99.5% uptime (appropriate for single-student, non-critical use) |
| **Data durability** | No student data loss; daily backups |
| **AI cost efficiency** | Track per-session LLM cost; target < $0.10/session average |

---

## 8. Assumptions & Open Questions

### Assumptions
1. The student has a reliable internet connection.
2. Uploaded materials are in English or a language the configured LLM supports.
3. The student is self-motivated (no gamification in MVP).
4. One student per account (no multi-user sharing).

### Open Questions
1. **Concept granularity**: How fine-grained should concept extraction be? (e.g., "Quadratic Formula" vs. "Solving Quadratic Equations" vs. "Algebra")
2. **Mastery threshold**: At what mastery level (e.g., 0.8?) is a concept considered "learned"?
3. **Session duration**: Should sessions have a recommended or maximum duration?
4. **Content licensing**: Do we need to track licensing/copyright of uploaded materials?
5. **LLM fallback**: What happens if the LLM provider is unavailable? Graceful degradation or hard stop?
6. **Concept graph seeding**: For common subjects, should we provide pre-built concept graphs, or always derive from uploaded material?
