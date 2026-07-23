# HA-01 PostgreSQL Run Repository Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist API Run identity and lifecycle through a repository backed by PostgreSQL so history survives API restarts.

**Architecture:** Introduce a small synchronous `RunRepository` protocol with immutable records. Keep an in-memory implementation for contract tests and explicit development fallback, and implement the persistent adapter with SQLAlchemy 2.x, psycopg 3, and Alembic. FastAPI routes and background execution depend only on the protocol.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI, SQLAlchemy 2.x, psycopg 3, Alembic, pytest.

---

### Task 1: Define repository contract and in-memory implementation

**Files:**
- Create: `src/workpilot/persistence/__init__.py`
- Create: `src/workpilot/persistence/models.py`
- Create: `src/workpilot/persistence/repository.py`
- Create: `tests/test_run_repository.py`

**Step 1:** Write failing tests for create/get/list/update, newest-first ordering, duplicate IDs, and missing records.

**Step 2:** Run `pytest tests/test_run_repository.py -v`; expect import failure.

**Step 3:** Add immutable `RunRecord`, `RunRepository` protocol, stable repository exceptions, and locked `InMemoryRunRepository`.

**Step 4:** Run `pytest tests/test_run_repository.py -v`; expect all contract tests to pass.

### Task 2: Add SQLAlchemy PostgreSQL adapter

**Files:**
- Create: `src/workpilot/persistence/database.py`
- Create: `src/workpilot/persistence/sqlalchemy_repository.py`
- Modify: `pyproject.toml`
- Test: `tests/test_sqlalchemy_run_repository.py`

**Step 1:** Write repository contract tests against a SQLAlchemy test engine and a conditional real PostgreSQL test using `WORKPILOT_TEST_DATABASE_URL`.

**Step 2:** Run the test and verify it fails because the adapter is absent.

**Step 3:** Implement declarative `RunRow`, engine/session factory, mapping, transactions, duplicate/missing error translation, and `close()`.

**Step 4:** Run repository tests; expect SQLite-compatible contract tests to pass and real PostgreSQL test to skip unless configured.

### Task 3: Add explicit Alembic migration

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/20260722_0001_create_runs.py`
- Test: `tests/test_migrations.py`

**Step 1:** Write a test that loads Alembic configuration and verifies a single head revision containing the `runs` table upgrade.

**Step 2:** Run the test and verify it fails because migration files are absent.

**Step 3:** Add Alembic environment that requires `WORKPILOT_DATABASE_URL` without logging it, plus reversible first migration.

**Step 4:** Run migration tests; with a real test database, also run upgrade/downgrade/upgrade.

### Task 4: Replace FastAPI registry with repository

**Files:**
- Modify: `src/workpilot/api/app.py`
- Modify: `src/workpilot/api/routes.py`
- Modify: `src/workpilot/api/__init__.py`
- Modify: `tests/test_api.py`

**Step 1:** Add a failing test that creates a Run, waits for terminal state, rebuilds the app with the same repository, and queries the Run/history/artifact.

**Step 2:** Run `pytest tests/test_api.py -v`; verify the restart test fails with the current in-memory registry.

**Step 3:** Inject `RunRepository` into `create_app`; create and update records through it; resolve reports, artifacts, and sources from persisted `output_dir`; translate unavailable repository operations to HTTP 503.

**Step 4:** Run API and repository tests; expect existing HTTP contract and restart test to pass.

### Task 5: Wire configuration and CLI

**Files:**
- Modify: `src/workpilot/config.py`
- Modify: `src/workpilot/cli.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Step 1:** Write failing tests for database URL configuration, persistent repository construction, and explicit in-memory development fallback.

**Step 2:** Run targeted CLI tests and verify failure.

**Step 3:** Add `workpilot_database_url` and `--in-memory-runs`; refuse silent fallback when a configured database cannot connect. Database credentials are accepted only from environment/`.env`, not command-line arguments. Add migration usage documentation.

**Step 4:** Run targeted tests and CLI help checks.

### Task 6: Verify and synchronize documentation

**Files:**
- Modify: `docs/03-实现细节/11-持久化存储.md`
- Modify: `docs/03-实现细节/13-高可用运行.md`
- Modify: `docs/02-项目开发计划/07-存储与高可用.md`
- Modify: `docs/06-项目状态/01-当前实现状态.md`
- Modify: `docs/05-追加式设计/设计记录/20260722-PostgreSQL-Repository与Run持久化.md`

**Step 1:** Run repository, migration, API, CLI, full pytest, and frontend build checks.

**Step 2:** Run `git diff --check` and the relative Markdown link checker.

**Step 3:** Record actual implementation, test evidence, limitations, and HA-02 follow-ups. Do not mark checkpoint recovery, idempotency, or multi-tenant isolation complete.
