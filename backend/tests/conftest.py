"""Root test configuration.

Unit tests need nothing external. Integration tests (tests/integration) use the
fixtures in tests/integration/conftest.py, which start PostgreSQL + Redis via
testcontainers — or reuse servers given by TEST_DATABASE_URL / TEST_REDIS_URL.
"""

from __future__ import annotations

import os

# Make sure an imported `app.main` never picks up a developer's real secrets.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
