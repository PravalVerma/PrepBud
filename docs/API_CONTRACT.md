# API Contract — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
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

---

### 3.7 Learning Goals

#### `GET /goals`

List user's goals.

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

**Response 201:** Created goal with auto-generated study plan.

#### `PATCH /goals/{goal_id}`

Update a goal.

#### `DELETE /goals/{goal_id}`

Delete a goal.

---

### 3.8 Learning Sessions

#### `POST /sessions`

Start a new learning session.

**Request:**
```json
{
  "session_type": "mixed",
  "learning_goal_id": "uuid",
  "time_budget_minutes": 30,
  "preferences": {
    "max_concepts": 3
  }
}
```

**Response 201:**
```json
{
  "data": {
    "session_id": "uuid",
    "status": "initialising",
    "websocket_url": "/api/v1/sessions/uuid/ws",
    "objective": {
      "target_concepts": [
        {"id": "uuid", "name": "Quadratic Formula", "action": "teach"}
      ],
      "session_type": "mixed",
      "estimated_duration_minutes": 25
    }
  }
}
```

#### `GET /sessions`

List past sessions.

**Query params:** `?status=completed&page=1&per_page=10`

#### `GET /sessions/{session_id}`

Get session details including events and summary.

#### `POST /sessions/{session_id}/end`

End an active session.

---

### 3.9 Session WebSocket

#### `WS /sessions/{session_id}/ws`

Real-time bidirectional communication for learning sessions.

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

---

### 3.10 Study Plans

#### `GET /study-plan`

Get the user's active study plan.

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "learning_goal_id": "uuid",
    "status": "active",
    "generated_at": "2026-09-17T10:00:00Z",
    "items": [
      {
        "id": "uuid",
        "concept_id": "uuid",
        "concept_name": "Quadratic Formula",
        "scheduled_date": "2026-09-18",
        "priority": 0.9,
        "status": "pending",
        "mastery_level": 0.45
      }
    ],
    "stats": {
      "total_items": 15,
      "completed": 3,
      "overdue": 1,
      "upcoming_today": 2
    }
  }
}
```

#### `POST /study-plan/regenerate`

Force regeneration of the study plan.

#### `PATCH /study-plan/items/{item_id}`

Update a review item (e.g., mark as skipped, reschedule).

---

### 3.11 Mastery Dashboard

#### `GET /mastery/overview`

Get mastery overview across all subjects.

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
      }
    ],
    "overall_stats": {
      "total_concepts": 90,
      "total_mastered": 24,
      "avg_mastery": 0.55,
      "streak_days": 5,
      "total_study_minutes": 320
    },
    "recent_activity": [
      {
        "date": "2026-09-17",
        "sessions": 2,
        "minutes": 45,
        "concepts_practiced": 5
      }
    ]
  }
}
```

#### `GET /mastery/heatmap`

Get mastery heatmap data (concepts × mastery levels).

**Query params:** `?subject_id=uuid`

---

### 3.12 Misconceptions

#### `GET /misconceptions`

List detected misconceptions for the current user.

**Query params:** `?status=active&concept_id=uuid`

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
        "concept_name": "Differentiation Rules"
      },
      "status": "active",
      "occurrence_count": 3,
      "detected_at": "2026-09-15T14:00:00Z",
      "evidence": [
        {
          "attempt_id": "uuid",
          "description": "Student wrote -2x instead of 2x",
          "detected_at": "2026-09-15T14:00:00Z"
        }
      ]
    }
  ]
}
```

---

### 3.13 AI Cost Tracking

#### `GET /ai/usage`

Get AI usage stats for the current user.

**Query params:** `?period=today|week|month`

**Response 200:**
```json
{
  "data": {
    "period": "today",
    "total_cost_usd": 0.42,
    "daily_budget_usd": 5.00,
    "total_tokens": 15420,
    "total_interactions": 28,
    "by_purpose": {
      "tutor_explanation": {"cost": 0.18, "count": 5},
      "question_generation": {"cost": 0.08, "count": 8},
      "answer_evaluation": {"cost": 0.06, "count": 8},
      "concept_extraction": {"cost": 0.10, "count": 1}
    }
  }
}
```

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
