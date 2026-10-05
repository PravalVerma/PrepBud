# API Contract — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-10-05  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Overview

The API is a **FastAPI** application exposing RESTful endpoints and WebSocket connections. All endpoints are prefixed with `/api/v1`.

**Base URL:** `https://api.schoolinabox.dev/api/v1`

**Authentication:** All endpoints (except `/auth/*` and `/health`) require a valid JWT in the `Authorization: Bearer <token>` header.

**Response Format:** JSON. All responses follow a consistent envelope:

```json
// Success
{
  "data": { ... },
  "meta": { "request_id": "...", "timestamp": "..." }
}

// Error
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Document not found",
    "details": { ... }
  },
  "meta": { "request_id": "...", "timestamp": "..." }
}

// Paginated list
{
  "data": [ ... ],
  "meta": {
    "request_id": "...",
    "timestamp": "...",
    "pagination": {
      "total": 42,
      "page": 1,
      "per_page": 20,
      "total_pages": 3
    }
  }
}
```

---

## 2. Error Codes

| HTTP Status | Error Code | Description |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Invalid request body or parameters |
| 401 | `AUTHENTICATION_REQUIRED` | Missing or invalid JWT |
| 403 | `FORBIDDEN` | Valid JWT but insufficient permissions |
| 404 | `RESOURCE_NOT_FOUND` | Resource doesn't exist or doesn't belong to user |
| 409 | `CONFLICT` | Resource state conflict (e.g., duplicate) |
| 422 | `UNPROCESSABLE_ENTITY` | Request is well-formed but semantically invalid |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Unexpected server error |
| 503 | `SERVICE_UNAVAILABLE` | Dependency failure (LLM, DB, etc.) |

---

## 3. Endpoints

### 3.1 Health

#### `GET /health`

No auth required.

**Response 200:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "services": {
    "database": "ok",
    "redis": "ok",
    "qdrant": "ok"
  }
}
```

---

### 3.2 Auth

#### `POST /auth/callback`

Sync a Supabase Auth user to the local database. Called after first login.

**Request:**
```json
{
  "auth_id": "supabase-uuid",
  "email": "student@example.com",
  "display_name": "Jane Doe"
}
```

**Response 201:**
```json
{
  "data": {
    "id": "uuid",
    "email": "student@example.com",
    "display_name": "Jane Doe",
    "onboarding_state": "new"
  }
}
```

---

### 3.3 Student Profile

#### `GET /profile`

Get current user's profile.

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "grade_level": "10th grade",
    "difficulty_band": "intermediate",
    "language": "en",
    "timezone": "Asia/Kolkata",
    "preferences": {},
    "cumulative_stats": {
      "total_sessions": 15,
      "total_minutes": 320,
      "concepts_mastered": 28
    },
    "onboarding_state": "complete"
  }
}
```

#### `PATCH /profile`

Update profile fields.

**Request:**
```json
{
  "grade_level": "11th grade",
  "difficulty_band": "advanced",
  "timezone": "Asia/Kolkata"
}
```

**Response 200:** Updated profile.

---

### 3.4 Subjects & Curriculum

#### `GET /subjects`

List user's subjects.

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Mathematics",
      "description": "...",
      "icon": "📐",
      "course_count": 2,
      "concept_count": 45,
      "avg_mastery": 0.62
    }
  ]
}
```

#### `POST /subjects`

Create a new subject.

**Request:**
```json
{
  "name": "Mathematics",
  "description": "High school mathematics",
  "icon": "📐"
}
```

**Response 201:** Created subject.

#### `GET /subjects/{subject_id}/courses`

List courses in a subject.

#### `POST /subjects/{subject_id}/courses`

Create a course.

#### `GET /courses/{course_id}/chapters`

List chapters in a course.

#### `GET /chapters/{chapter_id}/sections`

List sections in a chapter.

---

### 3.5 Documents

#### `POST /documents/upload-url`

Get a presigned S3 upload URL.

**Request:**
```json
{
  "filename": "chapter5.pdf",
  "mime_type": "application/pdf",
  "file_size_bytes": 2048000,
  "subject_id": "uuid",
  "course_id": "uuid"
}
```

**Response 200:**
```json
{
  "data": {
    "upload_url": "https://s3.amazonaws.com/...",
    "document_id": "uuid",
    "s3_key": "uploads/user-uuid/doc-uuid/chapter5.pdf",
    "expires_in_seconds": 3600
  }
}
```

#### `POST /documents/{document_id}/confirm-upload`

Confirm upload completed and trigger processing.

**Response 202:**
```json
{
  "data": {
    "document_id": "uuid",
    "processing_status": "processing",
    "task_id": "celery-task-uuid"
  }
}
```

#### `GET /documents`

List user's documents.

**Query params:** `?status=ready&subject_id=uuid&page=1&per_page=20`

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "title": "Chapter 5 - Quadratic Equations",
      "source_filename": "chapter5.pdf",
      "processing_status": "ready",
      "processing_metadata": {
        "page_count": 24,
        "chunk_count": 48,
        "concept_count": 12
      },
      "uploaded_at": "2026-09-17T10:00:00Z",
      "processed_at": "2026-09-17T10:03:42Z"
    }
  ]
}
```

#### `GET /documents/{document_id}`

Get document details including extracted concepts.

#### `DELETE /documents/{document_id}`

Delete a document and its associated data.

#### Document endpoints — implementation notes (Phase 3)

- **Upload rules:** `mime_type` ∈ `application/pdf`, `text/plain`, `image/png`, `image/jpeg`, with a matching file extension. Max 50 MB. Violations return `400 VALIDATION_ERROR`.
- **Ownership checks:** `subject_id`/`course_id` must be the caller's (`404` otherwise). If only `course_id` is given, `subject_id` is inferred from it; a course outside the given subject returns `422`. An optional `title` defaults to the file name.
- **Rate limit:** `upload-url` is limited to 5 per user per hour (`429`).
- **`upload_headers`:** the `upload-url` response also returns this map. These headers are part of the signature, so the client must send them with the `PUT`.
- **`confirm-upload` outcomes:**
  - It checks the stored object. Missing → `422`, `details.reason = "UPLOAD_NOT_FOUND"`. Size or type differs from what was declared → `422`, `FILE_SIZE_MISMATCH` / `FILE_TYPE_MISMATCH`, and the document is marked `failed`.
  - Calling it again while `processing` is idempotent: same `202` and `task_id`.
  - On a `failed` document it re-queues processing.
  - On a `ready` document it returns `409`.
- **`processing_metadata`:**
  - Keys: `page_count`, `chunk_count`, `concept_count`, `new_concept_count`, `relationship_count`, `ocr_pages`, `warnings`, `embedding_status` (`complete | failed | skipped`).
  - While processing: `stage` (`queued`, `extracting_text`, `extracting_concepts`, `linking_concepts`, `indexing`, …) and `progress` (0–1).
  - On failure: `error: {code, message}`.
- **`GET /documents/{id}`:** adds `section_count` and `concepts: [{id, name, section_count}]`.
- **`DELETE`:** removes the S3 object, the Qdrant vectors, the PostgreSQL rows, and extracted concepts left without any document and without learning data (mastery records or questions). If S3 or Qdrant is unreachable, nothing is deleted and the response is `503`.

---

### 3.6 Concepts

#### `GET /concepts`

List user's concepts.

**Query params:** `?subject_id=uuid&chapter_id=uuid&mastery_below=0.5&search=quadratic&page=1&per_page=50`

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Quadratic Formula",
      "description": "Using the formula x = (-b ± √(b²-4ac)) / 2a to solve quadratic equations",
      "difficulty_estimate": 0.6,
      "mastery": {
        "level": 0.45,
        "label": "intermediate",
        "last_assessed_at": "2026-09-16T14:00:00Z",
        "next_review_at": "2026-09-20T00:00:00Z"
      },
      "prerequisite_count": 3,
      "document_count": 2
    }
  ]
}
```

#### `GET /concepts/{concept_id}`

Get full concept details including prerequisites, related concepts, and mastery history.

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "name": "Quadratic Formula",
    "description": "...",
    "difficulty_estimate": 0.6,
    "mastery": {
      "level": 0.45,
      "confidence": 0.7,
      "attempt_count": 8,
      "correct_count": 5,
      "streak": 2,
      "last_assessed_at": "2026-09-16T14:00:00Z",
      "next_review_at": "2026-09-20T00:00:00Z",
      "history": [
        {"date": "2026-09-15", "mastery": 0.3, "event": "practice"},
        {"date": "2026-09-16", "mastery": 0.45, "event": "practice"}
      ]
    },
    "prerequisites": [
      {"id": "uuid", "name": "Factoring Quadratics", "mastery_level": 0.72}
    ],
    "related_concepts": [
      {"id": "uuid", "name": "Discriminant", "relationship": "related"}
    ],
    "misconceptions": [
      {"id": "uuid", "name": "Sign error in formula", "status": "active"}
    ],
    "documents": [
      {"id": "uuid", "title": "Chapter 5", "section_count": 3}
    ]
  }
}
```

#### `GET /concepts/{concept_id}/graph`

Get the concept's neighbourhood in the prerequisite graph.

**Response 200:**
```json
{
  "data": {
    "nodes": [
      {"id": "uuid", "name": "Quadratic Formula", "mastery": 0.45, "is_target": true},
      {"id": "uuid", "name": "Factoring Quadratics", "mastery": 0.72, "is_target": false}
    ],
    "edges": [
      {"source": "uuid", "target": "uuid", "type": "prerequisite"}
    ]
  }
}
```

#### Concept endpoints — implementation notes (Phase 3)

- **`GET /concepts`:**
  - Also accepts `document_id` and `sort` (`name | difficulty_estimate | mastery_level | created_at`) with `order` (`asc | desc`).
  - `search` is full-text (stemmed) plus a substring match on the name.
  - Concepts not yet assessed report `mastery.level = 0.0` (`novice`).
- **`GET /concepts/{id}`:** additionally returns `dependents` (concepts that list this one as a prerequisite), `subject_id`, `chapter_id`, `section_id`, `created_at`, and `metadata.{origin, aliases}`.
- **`related_concepts[].relationship`:** describes the other concept relative to this one: `related | generalisation | specialisation`.
- **Edge direction:** `source` is a prerequisite of `target`.
- **Generalisation and specialisation:** "B specialisation A" is stored as "A generalisation B", so each fact is stored once. `related_concepts` still reports both views: from A, B is a `specialisation`; from B, A is a `generalisation`.
- **`GET /concepts/{id}/graph`:** accepts `?depth=1..3` (default 1) and includes edges of all relationship types.

### 3.6.1 Content Search *(added in Phase 3 — AC-3.4)*

#### `GET /search`

Hybrid search over the user's document sections:

- **Semantic:** Qdrant vectors, always filtered by `user_id`.
- **Keyword:** PostgreSQL full-text search. Matches any query term, and ranks sections that cover more terms higher. A query containing a `-exclusion` uses strict AND matching.
- **Merging:** the two result lists are combined with reciprocal-rank fusion.

**Query params:** `?q=quadratic formula&mode=hybrid|keyword|semantic&subject_id=uuid&document_id=uuid&concept_id=uuid&page=1&per_page=20`

- `q` is 1–500 characters.
- `mode` defaults to `hybrid`.

**Response 200 (paginated):**

```json
{
  "data": [
    {
      "section_id": "uuid",
      "document_id": "uuid",
      "document_title": "Chapter 5",
      "section_index": 3,
      "heading": "5.2 The Quadratic Formula",
      "page_numbers": [12, 13],
      "snippet": "… the **quadratic formula** solves …",
      "score": 0.0325,
      "matched_by": ["keyword", "semantic"]
    }
  ],
  "meta": { "request_id": "...", "timestamp": "...", "pagination": { "total": 8, "page": 1, "per_page": 20, "total_pages": 1 } }
}
```

- **Snippets:** `**…**` marks highlighted keyword matches.
- **Response headers:** `X-Search-Mode` gives the mode actually used.
- **Degradation:** if semantic search is unavailable (Qdrant down, embedding failure or budget reached), `hybrid` falls back to keyword results and sets `X-Search-Degraded: true`. An explicit `mode=semantic` returns `503` instead.
- **Audit:** every query embedding is logged as an `AIInteraction`.

---

### 3.7 Learning Goals

Every goal change regenerates the study plan (§3.10).

#### `GET /goals`

List the user's goals, newest first.

**Query params:** `?status=active&page=1&per_page=20`

`status` is one of `draft | active | completed | paused | abandoned`. Each goal includes `progress` (below).

#### `POST /goals`

Create a learning goal.

**Request:**
```json
{
  "title": "Master Quadratic Equations",
  "description": "Complete understanding of Chapter 5",
  "goal_type": "mastery",
  "target_date": "2026-10-15",
  "subject_id": "uuid",
  "course_id": "uuid",
  "target_concept_ids": ["uuid1", "uuid2", "uuid3"]
}
```

Field rules:

- Only `title` is required (1–200 characters).
- `goal_type` is one of `mastery | deadline | exploration`. A `deadline` goal needs a `target_date`.

**Concept scope** is the first of these that applies:

1. `target_concept_ids`;
2. the course's concepts, meaning concepts in its chapters or from documents uploaded to it;
3. the subject's concepts;
4. every concept the student has.

**Errors:** a subject, course or concept that doesn't exist or belongs to someone else returns `404`. For concepts, `details.concept_ids` lists the ones that failed.

**Response 201:** the created goal, with its concepts and a summary of the auto-generated study plan:
```json
{
  "data": {
    "id": "uuid",
    "title": "Master Quadratic Equations",
    "description": "Complete understanding of Chapter 5",
    "goal_type": "deadline",
    "target_date": "2026-10-15",
    "status": "active",
    "subject_id": null,
    "course_id": null,
    "target_concept_ids": ["uuid1", "uuid2", "uuid3"],
    "progress": {
      "concept_count": 3,
      "mastered_count": 0,
      "average_mastery": 0.12,
      "progress": 0.15,
      "days_remaining": 10,
      "all_mastered": false
    },
    "concepts": [{"id": "uuid1", "name": "Quadratic Formula", "mastery_level": 0.2}],
    "study_plan": {"id": "uuid", "total_items": 3, "due_today": 1, "generated_at": "..."},
    "created_at": "...",
    "updated_at": "..."
  }
}
```

The progress fields are computed as follows:

- `progress` is the mean over the goal's concepts of `min(mastery / 0.80, 1)`, using decay-adjusted mastery.
- `mastered_count` counts concepts at or above the learned threshold (0.80).
- `all_mastered` is reported, but the goal's status is never changed automatically.

#### `GET /goals/{goal_id}`

The goal, with `progress` and `concepts`.

#### `PATCH /goals/{goal_id}`

Update any of the create fields, or `status`. Pausing, completing or abandoning a goal removes its concepts from the study plan. The response matches the create response.

#### `DELETE /goals/{goal_id}`

Delete the goal (`204`). The plan is regenerated without it.

---

### 3.8 Learning Sessions

A session is created in two steps:

1. `POST /sessions` plans it. This is fast and makes no LLM calls.
2. Opening a channel starts teaching. The channel is the WebSocket (§3.9) or an SSE `begin` turn.

The same message protocol runs over both channels.

#### `POST /sessions`

Plan a new learning session. Rate limit: 10 per user per hour.

**Request:**
```json
{
  "session_type": "mixed",
  "learning_goal_id": "uuid",
  "time_budget_minutes": 30,
  "concept_ids": ["uuid"],
  "preferences": {
    "max_concepts": 3
  }
}
```

Field rules:

- Every field is optional.
- `session_type` is one of `teach | practice | review | mixed`.
- `time_budget_minutes` is between 5 and 180.
- `concept_ids` (at most 10) lets the student choose the concepts. Otherwise they are selected from mastery and due reviews.

**Response 201:**
```json
{
  "data": {
    "session_id": "uuid",
    "status": "initialising",
    "websocket_url": "/api/v1/sessions/uuid/ws",
    "objective": {
      "target_concepts": [
        {"id": "uuid", "name": "Quadratic Formula", "action": "teach", "reason": "new", "mastery": 0.0}
      ],
      "session_type": "mixed",
      "estimated_duration_minutes": 25
    }
  }
}
```

`mastery` is the concept's mastery when the session starts. Clients use it as the baseline of the live mastery display.

Errors:

- `404` if the learning goal is not found.
- `422 UNPROCESSABLE_ENTITY` with `details.reason = "NO_CONCEPTS"` when there is nothing to study.
- `429` when the rate limit is exceeded.

#### `GET /sessions`

List past and current sessions, newest first.

**Query params:** `?status=completed&page=1&per_page=10`

`status` is one of `initialising | active | paused | completed | abandoned`.

Each item contains:

- `id`, `session_type`, `status`
- `started_at`, `ended_at`, `duration_seconds`
- `interaction_count`
- `concepts`: the target concept names
- `summary`: `null` until the session ends

#### `GET /sessions/{session_id}`

Get session details. The response contains the list item fields, plus:

- `learning_goal_id`, `objective`
- `awaiting`
- `current_question`: the public form, without the answer
- `events`: the session event log, up to 500 entries of `{index, type, concept_id, payload, created_at}`

#### `POST /sessions/{session_id}/end`

End the session. The student's mastery is updated, reviews are scheduled, and the summary is written. Returns a **session view** whose `events` contain `session_ended`. Any message to an ended session, `end` included, returns `409 CONFLICT` (`details.status`).

#### `POST /sessions/{session_id}/pause` · `POST /sessions/{session_id}/resume`

Pause and resume are idempotent.

- **Pause:** paused time does not count against the time budget. A dropped WebSocket pauses the session automatically.
- **Resume:** the response's `events` re-send what the student was looking at: the open question, or the explanation awaiting acknowledgement. Any message also resumes a paused session.

**Session view** is the response of end, pause, resume and JSON-mode messages, and the payload of `turn_complete`:
```json
{
  "session_id": "uuid",
  "status": "active",
  "awaiting": "answer",
  "objective": { "...": "as above" },
  "current_question": {"question_id": "uuid", "type": "mcq", "content": "...", "options": [{"label": "A", "text": "..."}], "hints_available": 2},
  "current_concept_id": "uuid",
  "interaction_count": 4,
  "summary": null,
  "events": []
}
```

`awaiting` says which message the session will accept next:

| `awaiting` | Accepted messages |
|---|---|
| `none` | `begin` (not started yet) |
| `acknowledgement` | `student_acknowledge`, `student_question` |
| `answer` | `student_response`, `request_hint`, `student_question` |
| `ended` | none |

`end_session` is accepted at any time.

#### `POST /sessions/{session_id}/ws-ticket`

Issue a single-use WebSocket ticket. It exists because browsers cannot send an `Authorization` header on a WebSocket. In this app the browser never holds the JWT at all: the Next.js BFF requests the ticket.

```json
{"data": {"ticket": "<opaque>", "expires_in_seconds": 60, "websocket_path": "/api/v1/sessions/uuid/ws"}}
```

The ticket is bound to the user and to this session. It is valid for `WS_TICKET_TTL_SECONDS` (default 60) and can be redeemed once. Requesting a ticket for an ended session returns `409`.

#### `POST /sessions/{session_id}/messages` (SSE)

Runs one turn over HTTP. Use it where a WebSocket is not possible, for example through the BFF.

**Body:** a client message (§3.9), or `{"type": "begin"}`. `begin` starts a planned session; on a running session it re-sends the current view.

**Response:**

- **With `Accept: text/event-stream`:** the turn's events as server-sent events (`event: <type>` / `data: <payload JSON>`). Explanation chunks are sent as they are generated. The stream ends with `turn_complete`, carrying the session view, or with `error`.
- **Without it:** JSON `{"data": <session view>}`, with explanation chunks merged.

The turn completes even if the client disconnects. A message that does not fit `awaiting` returns `409 CONFLICT` with `details.awaiting`.

---

### 3.9 Session WebSocket

#### `WS /sessions/{session_id}/ws?ticket=<ticket>`

Real-time bidirectional communication for learning sessions.

**Connecting:**

- The handshake is rejected (close code `1008`) in any of these cases:
  - the ticket is missing, unknown, expired, already used, or issued for another session;
  - the user is inactive;
  - the `Origin` header is not in the API's allowed origins (`FRONTEND_URL`).
- On connect, the server opens the channel:
  - a planned session starts, and its first explanation streams;
  - a paused session resumes;
  - a running session re-sends its current view.
- After that, every turn ends with a `turn_complete` event carrying the session view.
- When the session ends, the server sends `session_ended` and `turn_complete`, then closes with `1000`.
- If the connection drops, the session is paused. The client reconnects with a fresh ticket.

**Keep-alive and limits:**

- `{"type": "ping"}` is answered with `{"type": "pong", "payload": {}}`. Pings do not count toward the rate limit.
- Clients may send at most 60 messages per user per minute. Beyond that, the server replies with an `error` event (`RATE_LIMITED`) and keeps the connection open.
- Invalid JSON or an invalid message gets an `error` event (`VALIDATION_ERROR`).
- An out-of-order message gets an `error` event (`CONFLICT`, with `awaiting`). The connection stays open.

**Client → Server Messages:**

```json
// Student sends a response to a question
{
  "type": "student_response",
  "payload": {
    "content": "The derivative of x² is 2x",
    "question_id": "uuid"
  }
}

// Student asks a follow-up question
{
  "type": "student_question",
  "payload": {
    "content": "Can you explain that differently?"
  }
}

// Student acknowledges understanding
{
  "type": "student_acknowledge",
  "payload": {
    "understood": true
  }
}

// Student requests to end session
{
  "type": "end_session",
  "payload": {}
}
```

**Server → Client Messages:**

```json
// Tutor explanation (streamed)
{
  "type": "explanation_start",
  "payload": {
    "concept_id": "uuid",
    "concept_name": "Quadratic Formula"
  }
}
{
  "type": "explanation_chunk",
  "payload": {
    "content": "The quadratic formula is..."
  }
}
{
  "type": "explanation_end",
  "payload": {}
}

// Question presented
{
  "type": "question",
  "payload": {
    "question_id": "uuid",
    "concept_id": "uuid",
    "type": "short_answer",
    "difficulty": 0.5,
    "content": "What is the derivative of x²?",
    "options": null,
    "hints_available": 2
  }
}

// Evaluation result
{
  "type": "evaluation",
  "payload": {
    "is_correct": true,
    "score": 1.0,
    "explanation": "Correct! Using the power rule...",
    "mastery_update": {
      "concept_id": "uuid",
      "old_mastery": 0.45,
      "new_mastery": 0.52,
      "label": "intermediate"
    }
  }
}

// Hint
{
  "type": "hint",
  "payload": {
    "hint_number": 1,
    "content": "Think about the power rule..."
  }
}

// Misconception detected
{
  "type": "misconception_detected",
  "payload": {
    "misconception_name": "Sign error in differentiation",
    "explanation": "I notice you might be mixing up the sign..."
  }
}

// Session ended
{
  "type": "session_ended",
  "payload": {
    "summary": {
      "duration_minutes": 22,
      "concepts_covered": 3,
      "questions_answered": 8,
      "accuracy": 0.75,
      "mastery_changes": [
        {"concept": "Quadratic Formula", "from": 0.45, "to": 0.68}
      ]
    }
  }
}

// Error
{
  "type": "error",
  "payload": {
    "code": "LLM_TIMEOUT",
    "message": "The AI is taking longer than expected. Retrying..."
  }
}
```

#### Session protocol — implementation notes (Phases 4–5)

The learning engine (`app/services/learning_engine/`) implements this protocol. Phase 5 exposes it over the WebSocket and SSE (above). Additions to the message lists above:

- **Client → server, `request_hint`:** `{"type": "request_hint", "payload": {}}`, valid while a question is open.
- **Client → server, `student_response`:** may include `time_taken_seconds` (non-negative integer), used for fatigue detection.
- **Server → client, `session_started`:** `{"session_id", "objective"}`. It is the first event of a new session.
- **Server → client, `turn_complete`:** the session view (§3.8). It is the last event of every turn.
- **Server → client, `error`:** adds `retryable` (boolean). For a rejected message it also includes the details, for example `awaiting`.
- **Client → server, `ping`:** answered with `pong`.
- **Server → client, `tutor_message`:** `{"kind": "encouragement" | "no_more_hints", "content"}`, for example when the frustration guard activates.
- **Server → client, `explanation_start`:** carries `kind`: `intro | retry | worked_example | socratic | followup | resume`.
- **Server → client, `question`:** carries `mode` (`practice | review`). Options never reveal which one is correct.
- **Server → client, `evaluation`:** adds `question_id` and, when the answer was wrong, `correct_answer`.
- **Server → client, `misconception_detected`:** adds `status` (`active | recurring`).
- **Server → client, `session_ended`:** `summary` adds `text` (natural-language summary), `reviews` (next SM-2 review date per practised concept) and `end_reason`. The possible `end_reason` values are:
  - `student_ended`, `concepts_complete`, `all_mastered`, `time_budget`, `interaction_limit`, `frustration`
  - `token_budget`, `ai_unavailable`, `ai_budget_exceeded`

**Ordering rules:**

- A message that doesn't fit what the session is waiting for is rejected with `409 CONFLICT`. Examples: an answer when no question is open, or a stale `question_id`.
- Turns on one session are processed one at a time.

**AI failures:**

- **Transient:** an `error` event (`code`: `LLM_TIMEOUT | LLM_UNAVAILABLE | LLM_RATE_LIMITED`) is sent, and the session stays exactly where it was, so the client can resend.
- **Permanent:** an `error` event is followed by `session_ended`. This covers provider rejections and an exhausted daily or per-session budget (`SESSION_TOKEN_BUDGET`).

---

### 3.10 Study Plans

Each user has one **active** plan. It covers all of their active goals plus spaced-repetition reviews.

Regenerating the plan supersedes the old one and writes a new one, so plans form a history. Today's completed and skipped items carry over to the new plan.

**When the plan is regenerated:**

- a goal is created, changed or deleted;
- a session is completed;
- a document finishes processing (only if the user has an active goal);
- the daily maintenance job runs;
- `GET /study-plan` finds the plan was made on an earlier day.

#### `GET /study-plan`

Get the user's active study plan. It is generated on first use.

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "learning_goal_id": "uuid",
    "status": "active",
    "generated_at": "2026-09-17T10:00:00Z",
    "valid_until": "2026-10-01T10:00:00Z",
    "items": [
      {
        "id": "uuid",
        "concept_id": "uuid",
        "concept_name": "Quadratic Formula",
        "scheduled_date": "2026-09-18",
        "priority": 0.9,
        "status": "pending",
        "kind": "learn",
        "goal_id": "uuid",
        "mastery_level": 0.45,
        "mastery_label": "intermediate",
        "next_review_at": null,
        "completed_at": null
      }
    ],
    "stats": {
      "total_items": 15,
      "completed": 3,
      "skipped": 0,
      "overdue": 1,
      "upcoming_today": 2,
      "due_today": 3,
      "reviews_due": 1,
      "next_due_date": "2026-09-19",
      "estimated_minutes": 240
    }
  }
}
```

Plan-level fields:

- `learning_goal_id` is set only when exactly one goal is active.

Item fields:

- `kind` is `learn` (a goal concept) or `review` (spaced repetition, or a concept that has been forgotten).
- `status` is one of `pending | overdue | completed | skipped`.
- `mastery_level` is decay-adjusted.

Item ordering:

1. **Overdue items first**, oldest first.
2. Then by date and priority.
3. Completed and skipped items last.

Stats:

- `due_today` = `overdue` + `upcoming_today`.

#### `POST /study-plan/regenerate`

Force regeneration of the study plan. Returns the new plan.

#### `PATCH /study-plan/items/{item_id}`

Update an item of the active plan.

**Body:** `{"status": "skipped" | "completed" | "pending", "scheduled_date": "YYYY-MM-DD"}`. At least one field is required.

- `pending` un-skips an item.
- `scheduled_date` reschedules the item. It must be today or later; an earlier date returns `400`.

Returns the updated item. An item of a superseded plan, or of another user, returns `404`.

---

### 3.11 Mastery Dashboard

**Timezone.** Days are counted in the student's timezone (`student_profiles.timezone`, falling back to UTC). This applies to the activity series, "today" and the streak.

**Caching.** Responses are cached per user for 60 s. The cache is invalidated when a session completes, a document finishes processing, or the daily job runs.

#### `GET /mastery/overview`

Get the mastery overview across all subjects.

**Query params:** `?days=30`. This is how many days of `recent_activity` to return; the range is 7–365, the default 30.

**Response 200:**
```json
{
  "data": {
    "subjects": [
      {
        "id": "uuid",
        "name": "Mathematics",
        "avg_mastery": 0.62,
        "concept_count": 45,
        "mastered_count": 12,
        "struggling_count": 5
      },
      {"id": null, "name": "Unsorted", "avg_mastery": 0.1, "concept_count": 3, "mastered_count": 0, "struggling_count": 0}
    ],
    "overall_stats": {
      "total_concepts": 90,
      "total_mastered": 24,
      "in_progress": 40,
      "avg_mastery": 0.55,
      "streak_days": 5,
      "total_study_minutes": 320,
      "total_sessions": 14,
      "questions_answered": 210,
      "accuracy": 0.74
    },
    "mastery_distribution": {"novice": 20, "beginner": 15, "intermediate": 18, "proficient": 13, "mastered": 24},
    "recent_activity": [
      {
        "date": "2026-09-17",
        "sessions": 2,
        "minutes": 45,
        "questions": 12,
        "concepts_practiced": 5
      }
    ],
    "misconceptions": {"active": 2, "recurring": 1, "resolved": 6},
    "timezone": "Asia/Kolkata",
    "generated_at": "2026-09-17T10:00:00Z"
  }
}
```

Field rules:

- **`subjects`:** every subject the student has, including empty ones. Concepts without a subject are grouped as `{"id": null, "name": "Unsorted"}`.
  - `mastered_count` counts concepts at ≥ 0.80 mastery.
  - `struggling_count` counts practised concepts below 0.40.
- **`recent_activity`:** one entry per day, oldest first, ending today. Days without activity are included with zeros.
- **`streak_days`:** consecutive days with a session or an answered question, ending today. If the student hasn't studied yet today, the count ends yesterday.

#### `GET /mastery/heatmap`

Get mastery heatmap data: concepts × weeks.

**Query params:** `?subject_id=uuid&weeks=8&limit=100`

- `weeks` is 2–26.
- `limit` is 1–300.
- A subject that doesn't exist or belongs to someone else returns `404`.

**Response 200:**
```json
{
  "data": {
    "columns": ["2026-08-30", "2026-09-06", "2026-09-13", "2026-09-17"],
    "concepts": [
      {
        "id": "uuid",
        "name": "Quadratic Formula",
        "subject_id": "uuid",
        "subject_name": "Mathematics",
        "mastery_level": 0.68,
        "label": "proficient",
        "attempt_count": 9,
        "last_assessed_at": "2026-09-16T18:00:00Z",
        "next_review_at": "2026-09-19T00:00:00Z",
        "cells": [null, 0.31, 0.55, 0.68]
      }
    ],
    "total_concepts": 45,
    "truncated": false
  }
}
```

How it's built:

- **Columns** are week-end dates, oldest first. The last column is today.
- **Each cell** is the concept's mastery at the end of that week, taken from its mastery history. `null` means the concept hadn't been assessed yet.
- **The last cell** is the current stored mastery, which includes materialised decay.
- **Concept order:** the most recently studied concepts come first.

---

### 3.12 Misconceptions

#### `GET /misconceptions`

List detected misconceptions for the current user. Open ones (`active`, `recurring`) come first, then newest first.

**Query params:** `?status=active|recurring|resolved&concept_id=uuid&page=1&per_page=20` (paginated)

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "misconception": {
        "id": "uuid",
        "name": "Sign error in differentiation",
        "description": "Believes the derivative of x^n is -nx^(n-1)",
        "concept_id": "uuid",
        "concept_name": "Differentiation Rules"
      },
      "status": "active",
      "occurrence_count": 3,
      "detected_at": "2026-09-15T14:00:00Z",
      "resolved_at": null,
      "evidence": [
        {
          "attempt_id": "uuid",
          "description": "Student wrote -2x instead of 2x",
          "detected_at": "2026-09-15T14:00:00Z"
        }
      ]
    }
  ],
  "meta": {"pagination": {"total": 1, "page": 1, "per_page": 20, "total_pages": 1}}
}
```

`evidence` holds the 5 most recent entries, newest first.

---

### 3.13 AI Cost Tracking

#### `GET /ai/usage`

Get AI usage stats for the current user.

**Query params:** `?period=today|week|month`. The periods are today, the last 7 days and the last 30 days. Days are UTC, matching the daily budget.

**Response 200:**
```json
{
  "data": {
    "period": "today",
    "since": "2026-09-17",
    "total_cost_usd": 0.42,
    "today_cost_usd": 0.42,
    "daily_budget_usd": 5.00,
    "total_tokens": 15420,
    "total_interactions": 28,
    "failed_interactions": 1,
    "by_purpose": {
      "tutor_explanation": {"cost": 0.18, "count": 5, "tokens": 6100, "failed": 0},
      "generate_question": {"cost": 0.08, "count": 8, "tokens": 3900, "failed": 1}
    },
    "by_model": {
      "gemini-3.6-flash": {"cost": 0.30, "count": 9, "tokens": 9000, "failed": 0}
    },
    "daily": [{"date": "2026-09-17", "cost": 0.42, "count": 28}]
  }
}
```

The two breakdowns:

- **`by_purpose`** keys are the operation names recorded on each AI interaction, such as `generate_question`, `evaluate_answer`, `concept_extraction` and `tutor_explanation`. It is sorted by call count.
- **`by_model`** groups the same calls by model.

---

## 4. Pagination

All list endpoints support pagination:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |

---

## 5. Filtering & Sorting

List endpoints support filtering via query parameters. Common patterns:

| Pattern | Example | Description |
|---|---|---|
| Exact match | `?status=active` | Filter by exact value |
| ID filter | `?subject_id=uuid` | Filter by foreign key |
| Range | `?mastery_below=0.5` | Filter by numeric range |
| Search | `?search=quadratic` | Full-text search |
| Sort | `?sort=mastery_level&order=asc` | Sort by field |

---

## 6. Rate Limits

Rate limits are returned in response headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1695000000
```

---

## 7. Versioning

- API is versioned via URL path: `/api/v1/...`
- Breaking changes require a new version.
- Deprecation warnings are returned in headers: `Deprecation: true`.
