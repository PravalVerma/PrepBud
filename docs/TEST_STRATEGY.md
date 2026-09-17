# Test Strategy — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Testing Philosophy

1. **Test the contract, not the implementation.** Tests should verify behaviour, not internal structure.
2. **Pyramid over diamond.** Many unit tests, moderate integration tests, few E2E tests.
3. **AI is tested differently.** LLM outputs are non-deterministic; test structure, constraints, and guardrails — not exact content.
4. **Every bug gets a test.** When a bug is found, write a regression test before fixing it.
5. **Tests are documentation.** Test names describe expected behaviour in plain language.

---

## 2. Test Pyramid

```
        ┌───────────┐
        │   E2E     │  ~10 tests
        │  (Browser)│  Critical user journeys
        ├───────────┤
        │Integration│  ~50–100 tests
        │  (API +   │  Endpoint contracts, DB queries, 
        │   DB)     │  AI call structure, WebSocket flows
        ├───────────┤
        │   Unit    │  ~300+ tests
        │           │  Domain logic, algorithms, utilities,
        │           │  mastery updates, scheduling, validation
        └───────────┘
```

---

## 3. Testing Layers

### 3.1 Unit Tests

**Scope:** Pure functions, domain logic, algorithms, data transformations.

**Framework:** `pytest` (backend), `vitest` (frontend).

**What to test:**

| Component | Tests |
|---|---|
| Mastery update (BKT) | Correct → mastery increases; incorrect → mastery decreases; partial credit handled; edge cases (mastery at 0, at 1) |
| SM-2 scheduler | Quality < 3 resets; intervals grow correctly; ease factor bounded; next review date computed |
| Mastery decay | Decay rate matches formula; no decay below 0; high ease = slow decay |
| Difficulty calibration | Difficulty increases with mastery; respects bounds; session mode adjustments |
| Concept selection | Overdue reviews prioritised; prerequisites checked; deduplication works |
| Study plan generation | Topological sort respects prerequisites; correct distribution across days; mastered concepts excluded |
| Frustration detection | Triggers after N consecutive failures; response time spike detection |
| Input validation | Pydantic model validation; edge cases; required fields |
| Session state machine | Valid transitions accepted; invalid transitions rejected |

**Example:**
```python
class TestMasteryUpdate:
    def test_correct_answer_increases_mastery(self):
        result = update_mastery(MasteryUpdateInput(
            current_mastery=0.5,
            is_correct=True,
            score=1.0,
            question_difficulty=0.5,
        ))
        assert result > 0.5
    
    def test_incorrect_answer_decreases_mastery(self):
        result = update_mastery(MasteryUpdateInput(
            current_mastery=0.5,
            is_correct=False,
            score=0.0,
            question_difficulty=0.5,
        ))
        assert result < 0.5
    
    def test_mastery_stays_in_bounds(self):
        result = update_mastery(MasteryUpdateInput(
            current_mastery=0.99,
            is_correct=True,
            score=1.0,
            question_difficulty=0.9,
        ))
        assert 0.0 <= result <= 1.0
    
    def test_harder_questions_give_more_signal(self):
        easy = update_mastery(MasteryUpdateInput(
            current_mastery=0.5, is_correct=True, score=1.0, question_difficulty=0.2,
        ))
        hard = update_mastery(MasteryUpdateInput(
            current_mastery=0.5, is_correct=True, score=1.0, question_difficulty=0.8,
        ))
        assert hard > easy
```

### 3.2 Integration Tests

**Scope:** API endpoints, database operations, external service interactions.

**Framework:** `pytest` + `httpx` (async client) + `testcontainers` (PostgreSQL, Redis).

**Strategy:**
- Use a real PostgreSQL instance (via testcontainers or test database).
- Use a real Redis instance (via testcontainers).
- Mock external services: LLM provider, S3, Qdrant.
- Test full request → response cycle including auth, validation, DB operations.

**What to test:**

| Component | Tests |
|---|---|
| Auth middleware | Valid JWT accepted; expired JWT rejected; missing JWT returns 401; user auto-created on first login |
| CRUD endpoints | Create, read, update, delete for all entities; user isolation (can't access other users' data) |
| Document upload | Presigned URL generation; upload confirmation triggers processing; status transitions |
| Session lifecycle | Create session; WebSocket connection; message exchange; session end |
| Mastery endpoints | Dashboard data aggregation; concept mastery history |
| Study plan | Generation; item status updates; regeneration |
| Rate limiting | Rate limits enforced; headers returned |
| Error handling | 404 for missing resources; 400 for invalid input; 409 for conflicts |

**Example:**
```python
class TestConceptEndpoints:
    async def test_list_concepts_returns_only_user_data(self, client, user_a, user_b):
        # User A creates a concept
        resp = await client.post("/api/v1/concepts", json={...}, headers=auth(user_a))
        assert resp.status_code == 201
        concept_id = resp.json()["data"]["id"]
        
        # User A can see it
        resp = await client.get("/api/v1/concepts", headers=auth(user_a))
        assert any(c["id"] == concept_id for c in resp.json()["data"])
        
        # User B cannot see it
        resp = await client.get("/api/v1/concepts", headers=auth(user_b))
        assert not any(c["id"] == concept_id for c in resp.json()["data"])
    
    async def test_get_concept_404_for_other_user(self, client, user_a, user_b):
        resp = await client.post("/api/v1/concepts", json={...}, headers=auth(user_a))
        concept_id = resp.json()["data"]["id"]
        
        resp = await client.get(f"/api/v1/concepts/{concept_id}", headers=auth(user_b))
        assert resp.status_code == 404  # Not 403 (don't leak existence)
```

### 3.3 AI Integration Tests

**Scope:** AI call structure, prompt assembly, output parsing, guardrails.

**Strategy:**
- **Mock LLM responses** for deterministic tests (structure validation, parsing).
- **Live LLM tests** (optional, CI-excluded) for prompt quality regression.
- **Golden output tests** for critical prompts (concept extraction, question generation).

**What to test:**

| Component | Tests |
|---|---|
| Prompt assembly | Correct template variables filled; context within token budget; student profile included |
| Output parsing | Valid JSON parsed; malformed JSON handled; missing fields defaulted |
| Question generation | Output has required fields; difficulty within range; concept_id matches input |
| Answer evaluation | Correctness flag is boolean; score ∈ [0,1]; misconceptions is a list |
| Cost tracking | AIInteraction logged for every call; tokens and cost recorded |
| Error handling | LLM timeout handled; rate limit handled; invalid response retried |
| Safety guardrails | Content safety check runs; flagged content blocked |

**Example (mock-based):**
```python
class TestQuestionGeneration:
    async def test_generated_question_has_required_fields(self, mock_llm):
        mock_llm.return_value = LLMResponse(
            content='{"questions": [{"content": "What is 2+2?", "type": "short_answer", ...}]}',
            ...
        )
        result = await generate_questions(concept_id="uuid", difficulty=0.5, count=1)
        
        assert len(result.questions) == 1
        q = result.questions[0]
        assert q.content is not None
        assert q.type in VALID_QUESTION_TYPES
        assert 0.0 <= q.difficulty <= 1.0
        assert q.correct_answer is not None
```

### 3.4 End-to-End Tests

**Scope:** Critical user journeys through the full stack.

**Framework:** Playwright (browser automation).

**What to test:**

| Journey | Steps |
|---|---|
| **Onboarding** | Sign up → set profile → upload document → see processing complete |
| **First session** | Start session → receive explanation → answer question → see mastery update → session ends |
| **Review session** | Have overdue concepts → start review → answer review questions → see updated schedule |
| **Study plan** | Create goal → see generated plan → complete items → plan updates |
| **Document upload** | Upload PDF → see processing status → see extracted concepts |

**Example:**
```typescript
test('student can complete a learning session', async ({ page }) => {
  await login(page, testStudent);
  await page.goto('/dashboard');
  
  // Start session
  await page.click('[data-testid="start-session"]');
  await expect(page.locator('[data-testid="session-active"]')).toBeVisible();
  
  // Receive explanation
  await expect(page.locator('[data-testid="explanation"]')).toBeVisible({ timeout: 10000 });
  
  // Acknowledge understanding
  await page.click('[data-testid="understood-btn"]');
  
  // Answer a question
  await expect(page.locator('[data-testid="question"]')).toBeVisible();
  await page.fill('[data-testid="answer-input"]', '2x');
  await page.click('[data-testid="submit-answer"]');
  
  // See evaluation
  await expect(page.locator('[data-testid="evaluation"]')).toBeVisible();
  
  // End session
  await page.click('[data-testid="end-session"]');
  await expect(page.locator('[data-testid="session-summary"]')).toBeVisible();
});
```

---

## 4. Test Infrastructure

### 4.1 Test Database

- **Approach:** Testcontainers (PostgreSQL) for isolated test databases.
- **Migrations:** Run Alembic migrations before each test suite.
- **Cleanup:** Transaction rollback after each test (or truncate tables).

### 4.2 Test Fixtures

```python
# conftest.py
@pytest.fixture
async def db_session():
    """Provide a transactional test database session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def test_user(db_session):
    """Create a test user with profile."""
    user = User(auth_id="test-auth-id", email="test@example.com")
    db_session.add(user)
    await db_session.flush()
    profile = StudentProfile(user_id=user.id, grade_level="10th grade")
    db_session.add(profile)
    await db_session.flush()
    return user

@pytest.fixture
def mock_llm():
    """Mock LLM client for deterministic tests."""
    with patch('backend.ai.llm_client.get_client') as mock:
        yield mock
```

### 4.3 CI Pipeline

```yaml
# .github/workflows/test.yml
test:
  steps:
    - name: Unit tests
      run: pytest tests/unit -v --cov=backend --cov-report=xml
    
    - name: Integration tests
      run: pytest tests/integration -v
      services:
        postgres: { image: postgres:16 }
        redis: { image: redis:7 }
    
    - name: Frontend unit tests
      run: cd frontend && npm test
    
    - name: E2E tests (on merge to main only)
      if: github.ref == 'refs/heads/main'
      run: npx playwright test
```

---

## 5. Test Data Strategy

### 5.1 Factories

Use factory functions (not fixtures-as-data) for test data:

```python
def make_concept(
    user_id: str = "test-user",
    name: str = "Test Concept",
    difficulty: float = 0.5,
    **kwargs,
) -> Concept:
    return Concept(
        id=str(uuid4()),
        user_id=user_id,
        name=name,
        difficulty_estimate=difficulty,
        **kwargs,
    )

def make_mastery(
    user_id: str = "test-user",
    concept_id: str = "test-concept",
    level: float = 0.5,
    **kwargs,
) -> StudentConceptMastery:
    return StudentConceptMastery(
        id=str(uuid4()),
        user_id=user_id,
        concept_id=concept_id,
        mastery_level=level,
        **kwargs,
    )
```

### 5.2 Seed Data

For E2E and demo purposes, maintain a seed script:
- 1 test user with complete profile
- 2 subjects, 2 courses each
- 10 concepts per course with prerequisite relationships
- 5 uploaded documents (processing already complete)
- Mastery data at various levels
- 1 active study plan with review items

---

## 6. Coverage Targets

| Layer | Target | Measurement |
|---|---|---|
| Unit tests | ≥ 90% line coverage | `pytest --cov` |
| Integration tests | ≥ 80% of API endpoints | Endpoint count |
| E2E tests | 100% of critical journeys (5) | Journey count |
| AI tests | 100% of prompt templates | Template count |
| Overall | ≥ 85% line coverage (excluding AI output content) | Combined |

---

## 7. AI-Specific Testing Challenges

### 7.1 Non-Determinism

LLM outputs vary between calls. Testing strategies:

| Strategy | When | How |
|---|---|---|
| **Mock responses** | Structure/parsing tests | Fixed JSON responses from mocks |
| **Schema validation** | Output format tests | Validate against Pydantic models |
| **Constraint checking** | Guardrail tests | Assert output doesn't contain forbidden content |
| **Golden tests** | Prompt regression | Compare structure (not content) against golden outputs |
| **Human-in-loop** | Prompt quality | Periodic manual review of sampled outputs |
| **Statistical tests** | Quality regression | Run N times, assert success rate > threshold |

### 7.2 Prompt Regression Testing

When a prompt template changes:
1. Run the prompt against 5 test inputs with a fixed seed (if supported).
2. Compare output **structure** against golden samples.
3. Flag if output format deviates (missing fields, wrong types).
4. Manual review for quality assessment.

---

## 8. Performance Testing

### 8.1 Benchmarks

| Scenario | Target | Tool |
|---|---|---|
| API response time (CRUD) | < 100ms p95 | `locust` or `k6` |
| Session start | < 2s (including first AI call) | Custom benchmark |
| Question evaluation | < 2s | Custom benchmark |
| Document processing (50-page PDF) | < 3 minutes | Celery monitoring |
| Concurrent sessions | 10 simultaneous | `k6` WebSocket |

### 8.2 Load Testing

Deferred to post-MVP, but the architecture supports horizontal scaling:
- Stateless API → multiple instances behind a load balancer.
- Redis → shared session state.
- Celery → multiple workers.

---

## 9. Security Testing

| Test | Approach |
|---|---|
| Auth bypass | Attempt requests without JWT; verify 401 |
| Cross-user access | Attempt to access another user's resources; verify 404 |
| SQL injection | Fuzz input fields; verify parameterised queries |
| File upload abuse | Upload non-whitelisted MIME types; verify rejection |
| Rate limiting | Exceed rate limits; verify 429 |
| Prompt injection | Include instruction-like text in student answers; verify system prompt not overridden |
