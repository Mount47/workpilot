# HA-02B Versioned Checkpoint Codec Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Persist a validated, versioned recovery checkpoint after every committed plan step and reconstruct all state required for the next step without pickle or full-Run replay.

**Architecture:** A restricted Codec Registry translates the six built-in tool outputs and Runtime state to canonical JSON. A Checkpoint Store writes and verifies files atomically inside the Run output directory. The Repository registers Checkpoint Metadata and completes ToolCall/PlanStep/Run sequence in one database transaction. An internal Restorer rebuilds execution state; lease takeover and public recovery entrypoints remain out of scope until HA-02C/D.

**Tech Stack:** Python 3.11+, Pydantic, SQLAlchemy 2.x, PostgreSQL 17, Alembic, pytest.

---

### Task 1: Define checkpoint records and restricted codecs

**Files:**
- Create: `src/workpilot/runtime/checkpoint.py`
- Modify: `src/workpilot/planning/scheduler.py`
- Test: `tests/test_checkpoint_codec.py`

**Step 1: Write failing codec tests**

Cover canonical byte stability, top-level schema validation, Run/sequence identity, all six built-in tool result shapes, a full payload round-trip, and rejection of unknown tools/arbitrary objects.

**Step 2: Run the tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_checkpoint_codec.py -v`

Expected: import/collection failure because checkpoint types do not exist.

**Step 3: Implement explicit v1 models and Codec Registry**

Add immutable metadata/value types, `CheckpointPayload`, `RestoredExecutionState`, canonical JSON helpers, and per-tool encode/decode functions. Add a controlled StepResultStore export/import boundary; do not expose or serialize arbitrary internal objects.

**Step 4: Run targeted tests**

Expected: all codec and StepResultStore restoration tests pass.

### Task 2: Implement atomic Checkpoint Store

**Files:**
- Create: `src/workpilot/runtime/checkpoint_store.py`
- Test: `tests/test_checkpoint_store.py`

**Step 1: Write failing filesystem protocol tests**

Test atomic final naming, same-directory temporary writes, deterministic digest/size, read-back verification, relative-path validation, missing/truncated/corrupt files, and Run/sequence mismatch.

**Step 2: Implement write/read verification**

Use UTF-8 canonical JSON, flush/fsync, `os.replace`, post-write read-back, SHA-256, and resolved containment checks. Return content-free `CheckpointFileRecord` metadata.

**Step 3: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_checkpoint_store.py -v`

Expected: PASS.

### Task 3: Add checkpoint persistence schema and contracts

**Files:**
- Modify: `src/workpilot/persistence/models.py`
- Modify: `src/workpilot/persistence/repository.py`
- Modify: `src/workpilot/persistence/database.py`
- Modify: `src/workpilot/persistence/sqlalchemy_repository.py`
- Modify: `src/workpilot/persistence/__init__.py`
- Create: `migrations/versions/20260722_0003_checkpoints.py`
- Modify: `tests/test_execution_repository.py`
- Modify: `tests/test_sqlalchemy_run_repository.py`
- Modify: `tests/test_migrations.py`

**Step 1: Write failing repository contract tests**

Require `CheckpointMetadataRecord`, latest/list queries, and `complete_tool_call_with_checkpoint`. Assert one atomic operation inserts metadata, completes ToolCall/PlanStep, attaches Step sequence, and advances Run sequence exactly once. Cover gaps, duplicate conflicts and rollback.

**Step 2: Write failing migration tests**

Require Alembic head `20260722_0003`, `checkpoints`, `runs.checkpoint_sequence`, and `plan_steps.checkpoint_sequence`. Verify downgrade to HA-02A preserves old Run and ledger data, then re-upgrade.

**Step 3: Implement InMemory and SQLAlchemy contracts**

Use the existing repository lock/session transaction. SQL implementation must lock the participating rows and reject sequence gaps. Never inspect checkpoint file content in the Repository.

**Step 4: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_execution_repository.py tests\test_sqlalchemy_run_repository.py tests\test_migrations.py -q`

Expected: InMemory tests pass; PostgreSQL tests skip unless the test URL is configured.

### Task 4: Commit checkpoints at the executor boundary

**Files:**
- Modify: `src/workpilot/runtime/observer.py`
- Modify: `src/workpilot/persistence/execution_observer.py`
- Modify: `src/workpilot/planning/executor.py`
- Modify: `src/workpilot/runtime/runner.py`
- Test: `tests/test_execution_observer.py`
- Test: `tests/test_smoke.py`

**Step 1: Write failing lifecycle-order tests**

Assert tool success order is execute -> success evaluation -> checkpoint file write/verify -> repository atomic completion -> dependent scheduling. Inject file and repository failures and assert the Step is not committed.

**Step 2: Add Checkpoint Coordinator**

Build immutable payloads from Runtime-owned state. Change observer completion to accept verified metadata and call the atomic repository operation. Preserve `NullExecutionObserver` behavior for explicit no-ledger development runs.

**Step 3: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_execution_observer.py tests\test_smoke.py tests\test_planning.py -q`

Expected: PASS and one checkpoint per completed built-in step when persistence is enabled.

### Task 5: Rebuild Runtime state from the latest committed checkpoint

**Files:**
- Modify: `src/workpilot/evidence/store.py`
- Modify: `src/workpilot/runtime/budget.py`
- Modify: `src/workpilot/memory/working.py`
- Modify: `src/workpilot/runtime/checkpoint.py`
- Modify: `src/workpilot/runtime/runner.py`
- Create: `tests/test_checkpoint_restore.py`

**Step 1: Write failing restoration tests**

For checkpoints after each built-in step, assert Plan state, StepResultStore outputs, Evidence IDs/content, ProjectSnapshot, rendered artifacts, verification results, revision counters and budget counters reconstruct exactly. Running/uncommitted steps must become pending.

**Step 2: Add narrow restore APIs**

Add validated constructors/import methods rather than mutating private dictionaries from outside their owners. Re-run Plan validation and reject incompatible Tool Registry versions.

**Step 3: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_checkpoint_restore.py tests\test_runtime_budget.py tests\test_planning.py -q`

Expected: PASS.

### Task 6: Add crash-boundary and real PostgreSQL acceptance

**Files:**
- Create: `tests/test_checkpoint_recovery_integration.py`
- Modify: `tests/test_migrations.py`
- Modify: `README.md`
- Modify: `docs/02-项目开发计划/07-存储与高可用.md`
- Modify: `docs/03-实现细节/13-高可用运行.md`
- Modify: `docs/06-项目状态/01-当前实现状态.md`
- Modify: `docs/06-项目状态/03-变更记录.md`

**Step 1: Add fault-injection acceptance tests**

Crash before rename, after rename/before DB commit, after DB commit, and after each of the six steps. Assert recovery always chooses the latest database-committed checkpoint and corrupt committed files fail closed.

**Step 2: Run PostgreSQL migration and transaction acceptance**

Start the existing local PostgreSQL test harness, upgrade HA-02A -> HA-02B with pre-existing data, run concurrent sequence/conflict tests, downgrade and re-upgrade.

**Step 3: Run full verification**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q src tests
npm run build
git diff --check
```

Expected: all tests/builds pass; only the known third-party TestClient warning may remain.

**Step 4: Update status without overstating HA**

Document HA-02B as internal checkpoint durability and reconstruction complete. Keep lease takeover, startup scanning, cancellation/queue, encryption and multi-instance claims explicitly incomplete.

