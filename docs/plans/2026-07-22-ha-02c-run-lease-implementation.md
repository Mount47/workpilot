# HA-02C Run Lease and Fencing Implementation Plan

**Status:** Completed and accepted on 2026-07-22. Full PostgreSQL suite: 304 passed; no-database suite: 298 passed, 6 skipped; Python compileall and React production build passed.

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Guarantee that at most one valid Worker can mutate a Run by adding PostgreSQL-backed leases, monotonic fencing tokens, Heartbeat loss detection, and expired-Run takeover semantics.

**Architecture:** The `runs` row is the lease authority. Claim and renew use database time and row locks; every execution-ledger write verifies owner, execution attempt, and expiry in the same transaction. A small Heartbeat thread detects loss early, while Repository fencing remains authoritative. Checkpoint reconstruction and public recovery entrypoints remain HA-02D scope.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.x, PostgreSQL 17, Alembic, FastAPI, pytest.

---

### Task 1: Define lease models and InMemory contract

**Files:**
- Modify: `src/workpilot/persistence/models.py`
- Modify: `src/workpilot/persistence/repository.py`
- Modify: `src/workpilot/persistence/__init__.py`
- Create: `tests/test_run_lease.py`

**Step 1: Write failing lease contract tests**

Cover pending claim, active conflict, renewal, expiry takeover, monotonic `execution_attempt`, terminal rejection, wrong owner, stale attempt, expired renewal, and atomic terminal release. Use an injected UTC fake clock.

**Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_run_lease.py -v`

Expected: import failures for `RunLease`, `LeaseConflictError`, and repository lease methods.

**Step 3: Implement immutable records and InMemory state machine**

Add `lease_owner`, `lease_expires_at`, and `execution_attempt` to `RunRecord`; define content-minimized `RunLease`. Add `claim_run`, `renew_lease`, `assert_lease`, and `commit_terminal` to the Repository contract. InMemory uses one lock and injected clock.

**Step 4: Run contract tests**

Expected: all deterministic fake-clock tests pass.

### Task 2: Add migration 0004 and PostgreSQL lease operations

**Files:**
- Modify: `src/workpilot/persistence/database.py`
- Modify: `src/workpilot/persistence/sqlalchemy_repository.py`
- Create: `migrations/versions/20260722_0004_run_leases.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_sqlalchemy_run_repository.py`

**Step 1: Write failing migration and SQL adapter tests**

Require head `20260722_0004`, three Run columns and `(state, lease_expires_at)` index. Verify HA-02B data survives upgrade/downgrade. SQL adapter tests cover claim/renew/takeover and terminal commit.

**Step 2: Implement database-time row-lock operations**

Read `SELECT now()` inside each lease transaction, lock Run with `FOR UPDATE`, and apply the same state machine as InMemory. Never use local Worker time to decide PostgreSQL expiry.

**Step 3: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_migrations.py tests\test_sqlalchemy_run_repository.py -q`

Expected: SQLite adapter tests pass and PostgreSQL-only tests skip without a URL.

### Task 3: Fence every execution write

**Files:**
- Modify: `src/workpilot/persistence/repository.py`
- Modify: `src/workpilot/persistence/sqlalchemy_repository.py`
- Modify: `src/workpilot/persistence/execution_observer.py`
- Modify: `tests/test_execution_repository.py`
- Modify: `tests/test_execution_observer.py`
- Modify: `tests/test_artifact_metadata.py`

**Step 1: Write failing stale-worker tests**

After Worker B takes over an expired lease, assert Worker A cannot create/update PlanSteps, start/complete/fail ToolCalls, commit Checkpoints, record Artifact Metadata, or update terminal Run state. Assert unclaimed development Runs retain existing behavior.

**Step 2: Add optional lease arguments and centralized validation**

InMemory validates under its lock. SQL operations lock Run first, obtain database time, validate the fence, then touch child rows. RepositoryExecutionObserver always carries the claimed `RunLease`.

**Step 3: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_execution_repository.py tests\test_execution_observer.py tests\test_artifact_metadata.py -q`

Expected: PASS; all claimed-Run writes require the current fence.

### Task 4: Add Heartbeat and Runtime ownership guard

**Files:**
- Create: `src/workpilot/runtime/lease.py`
- Modify: `src/workpilot/runtime/observer.py`
- Modify: `src/workpilot/runtime/runner.py`
- Create: `tests/test_lease_heartbeat.py`
- Modify: `tests/test_planning.py`

**Step 1: Write failing Heartbeat tests**

Use fake wait/clock hooks to verify configuration, renew cadence, stop, Repository outage, lost lease propagation, and no raw owner token in events.

**Step 2: Implement `LeaseHeartbeat` and execution guard**

Use a daemon thread plus Event. Runtime calls `assert_execution_allowed` before starting a PlanStep, after tool return, and before checkpoint commit. NullObserver remains a no-op.

**Step 3: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_lease_heartbeat.py tests\test_planning.py -q`

Expected: PASS.

### Task 5: Integrate API Worker claim and terminal release

**Files:**
- Modify: `src/workpilot/api/app.py`
- Modify: `src/workpilot/config.py`
- Modify: `.env.example`
- Modify: `tests/test_api.py`
- Modify: `tests/test_config.py`

**Step 1: Write failing API lifecycle tests**

Assert background execution claims before Runtime construction, starts/stops Heartbeat, passes the lease to Observer, commits terminal state with the fence, and never lets a stale Worker overwrite a takeover result.

**Step 2: Add validated configuration**

Add `WORKPILOT_LEASE_TTL_SECONDS=30` and `WORKPILOT_HEARTBEAT_SECONDS=10`; reject heartbeat greater than one third of TTL.

**Step 3: Integrate claim/heartbeat/terminal commit**

Generate owner token with `secrets.token_urlsafe`, never log it, and hash only for safe Trace metadata. Do not add automatic recovery scanning yet.

**Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_config.py -q`

Expected: PASS.

### Task 6: Dual-Worker PostgreSQL acceptance and documentation

**Files:**
- Modify: `tests/test_sqlalchemy_run_repository.py`
- Create: `tests/test_run_lease_integration.py`
- Modify: `README.md`
- Modify: `docs/02-项目开发计划/07-存储与高可用.md`
- Modify: `docs/03-实现细节/13-高可用运行.md`
- Modify: `docs/06-项目状态/01-当前实现状态.md`
- Modify: `docs/06-项目状态/03-变更记录.md`

**Step 1: Add real PostgreSQL race tests**

Two Workers concurrently claim one pending Run: exactly one succeeds. Advance past expiry, let Worker B take over, then assert every Worker A write fails and Worker B can commit the next Checkpoint and terminal state.

**Step 2: Run migration acceptance**

Upgrade 0003 -> 0004 with pre-existing Checkpoint data, downgrade to 0003, verify data, then re-upgrade.

**Step 3: Run full verification**

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q src tests
npm --prefix web run build
git diff --check
```

Expected: all tests and builds pass; only the known third-party TestClient warning may remain.

**Step 4: Update status without overstating availability**

Mark HA-02C lease/fencing complete only after real PostgreSQL dual-Worker acceptance. Keep startup recovery scanning, API/CLI resume, Provider call deduplication, queueing, cancellation, auth, encryption and production metrics incomplete.
