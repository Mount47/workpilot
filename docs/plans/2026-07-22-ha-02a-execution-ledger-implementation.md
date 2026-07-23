# HA-02A Durable Execution Ledger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify API/Runtime Run identity and persist idempotent Run creation, PlanStep state, ToolCall state, and Artifact Metadata as the control-plane foundation for later checkpoint recovery.

**Architecture:** Extend the existing PostgreSQL Repository with normalized execution-ledger records and atomic operations. Runtime emits lifecycle events through a small observer protocol; a repository-backed observer persists them without coupling planning code to SQLAlchemy. This phase deliberately does not resume checkpoints or claim leases, and documentation must continue to say so.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI, SQLAlchemy 2.x, PostgreSQL 17, Alembic, pytest.

---

### Task 1: Unify API and Runtime Run identity

**Files:**
- Modify: `src/workpilot/runtime/runner.py`
- Modify: `src/workpilot/api/app.py`
- Test: `tests/test_smoke.py`
- Test: `tests/test_api.py`

**Step 1: Write the failing Runtime identity test**

Add a test that constructs `Runtime(..., run_id="run_external")`, executes the Stub path, then asserts:

```python
assert result.run_id == "run_external"
assert json.loads((output / "trace.json").read_text(encoding="utf-8"))[0]["run_id"] == "run_external"
assert json.loads((output / "run_context.json").read_text(encoding="utf-8"))["run_id"] == "run_external"
```

**Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests\test_smoke.py -k external_run_id -v`

Expected: FAIL because `Runtime.__init__` does not accept `run_id`.

**Step 3: Implement externally supplied identity**

Add a keyword-only `run_id: str | None = None`; generate an ID only when absent. Use the resolved identity for `Run`, `MissionContract`, `EvidenceStore`, `TraceJournal`, snapshot IDs and every artifact. In API `_execute`, pass `record.run_id`.

**Step 4: Add the API correlation assertion**

After an API Run completes, assert `trace.json`, `run_context.json`, and persisted Run all carry the response `run_id`.

**Step 5: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_smoke.py tests\test_api.py -q`

Expected: PASS; PostgreSQL-only API test may skip without its URL.

**Step 6: Commit checkpoint**

```bash
git add src/workpilot/runtime/runner.py src/workpilot/api/app.py tests/test_smoke.py tests/test_api.py
git commit -m "fix(runtime): unify persisted run identity"
```

### Task 2: Define idempotency and execution-ledger contracts

**Files:**
- Modify: `src/workpilot/persistence/models.py`
- Modify: `src/workpilot/persistence/repository.py`
- Modify: `src/workpilot/persistence/__init__.py`
- Test: `tests/test_run_repository.py`
- Create: `tests/test_execution_repository.py`

**Step 1: Write failing idempotent-create contract tests**

Cover:

```python
first = repository.create_or_get(record, key_hash="k", request_fingerprint="f")
same = repository.create_or_get(other_record, key_hash="k", request_fingerprint="f")
assert first.created is True
assert same.created is False
assert same.record.run_id == first.record.run_id
```

Also assert the same key with a different fingerprint raises `IdempotencyConflictError` and the raw key is never stored.

**Step 2: Write failing ledger contract tests**

Define immutable `PlanStepRecord`, `ToolCallRecord`, and `ArtifactMetadataRecord`. Test:

- one plan batch creates all steps;
- duplicate plan/step writes are deterministic;
- starting a ToolCall atomically marks the Step running;
- completing/failing a ToolCall atomically updates both records;
- duplicate `(run_id, step_id, execution_no)` is rejected;
- Artifact Metadata uses relative paths and versions;
- missing Run/Step operations fail with stable exceptions.

**Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_run_repository.py tests\test_execution_repository.py -v`

Expected: collection/import failures for the new contracts.

**Step 4: Implement contracts and InMemory adapter**

Add:

```python
@dataclass(frozen=True, slots=True)
class CreateRunResult:
    record: RunRecord
    created: bool

class ExecutionRepository(Protocol):
    def create_plan_steps(self, run_id: str, records: list[PlanStepRecord]) -> None: ...
    def start_tool_call(self, step: PlanStepRecord, call: ToolCallRecord) -> None: ...
    def complete_tool_call(self, call_id: str, *, output_summary: dict, evidence_ids: list[str]) -> None: ...
    def fail_tool_call(self, call_id: str, *, error_type: str) -> None: ...
    def record_artifact(self, record: ArtifactMetadataRecord) -> None: ...
```

Use locks in the InMemory adapter and return defensive immutable records.

**Step 5: Run contract tests**

Expected: all InMemory contract tests pass.

**Step 6: Commit checkpoint**

```bash
git add src/workpilot/persistence tests/test_run_repository.py tests/test_execution_repository.py
git commit -m "feat(persistence): define durable execution ledger"
```

### Task 3: Add the second Alembic migration and SQLAlchemy rows

**Files:**
- Modify: `src/workpilot/persistence/database.py`
- Create: `migrations/versions/20260722_0002_execution_ledger.py`
- Modify: `tests/test_migrations.py`

**Step 1: Write failing migration-chain assertions**

Assert the single Alembic head is `20260722_0002`, its parent is `20260722_0001`, and after upgrade these tables exist:

```python
{"runs", "plan_steps", "tool_calls", "artifact_metadata"}
```

Assert downgrade to `20260722_0001` removes only the three ledger tables/columns and preserves `runs` data shape from HA-01.

**Step 2: Run migration tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_migrations.py -v`

Expected: FAIL because revision `20260722_0002` does not exist.

**Step 3: Implement rows and migration**

Extend `runs` with nullable `idempotency_key_hash` and `request_fingerprint`, plus a unique partial-capable index on the hash. Create normalized tables with foreign keys to `runs`, composite uniqueness for logical ToolCalls, JSON columns only for safe summaries, UTC timestamps, and lookup indexes on state/run ID.

Do not add lease/checkpoint columns in HA-02A; they belong to HA-02B/C.

**Step 4: Verify upgrade/downgrade/upgrade on SQLite**

Run: `.venv\Scripts\python.exe -m pytest tests\test_migrations.py -v`

Expected: PASS.

**Step 5: Generate PostgreSQL offline DDL**

Run: `.venv\Scripts\alembic.exe upgrade head --sql`

Expected: transactional PostgreSQL DDL with no database URL in output.

**Step 6: Commit checkpoint**

```bash
git add src/workpilot/persistence/database.py migrations tests/test_migrations.py
git commit -m "feat(database): add execution ledger schema"
```

### Task 4: Implement SQLAlchemy idempotency and ledger transactions

**Files:**
- Modify: `src/workpilot/persistence/sqlalchemy_repository.py`
- Modify: `tests/test_sqlalchemy_run_repository.py`
- Modify: `tests/test_execution_repository.py`

**Step 1: Add adapter contract fixtures**

Run the same ledger contract suite against SQLite/SQLAlchemy and conditionally against `WORKPILOT_TEST_DATABASE_URL`.

**Step 2: Add concurrency tests**

Two sessions creating the same idempotency hash and fingerprint must return the same Run. A mismatched fingerprint must raise `IdempotencyConflictError`. Completion must update ToolCall and PlanStep in one transaction; inject a failure before commit and assert neither advances.

**Step 3: Run tests to verify failure**

Expected: methods are missing.

**Step 4: Implement SQLAlchemy operations**

Map IntegrityError by violated semantic, never expose SQL or URLs. Use one short transaction for plan creation, call start, call completion/failure, and Artifact Metadata insert. Validate that relative Artifact paths contain no traversal before persistence.

**Step 5: Run adapter tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_sqlalchemy_run_repository.py tests\test_execution_repository.py -q`

Expected: PASS; real PostgreSQL cases skip when not configured.

**Step 6: Commit checkpoint**

```bash
git add src/workpilot/persistence/sqlalchemy_repository.py tests
git commit -m "feat(persistence): implement SQL execution ledger"
```

### Task 5: Add API Idempotency-Key semantics

**Files:**
- Modify: `src/workpilot/api/routes.py`
- Modify: `src/workpilot/api/models.py`
- Modify: `tests/test_api.py`

**Step 1: Write failing HTTP tests**

Cover:

- repeated request with the same `Idempotency-Key` and body returns the same Run ID;
- exactly one background execution starts;
- same key with different goal/workspace/provider returns HTTP 409;
- missing key keeps create-new behavior;
- oversized/blank keys return 422/400;
- response and logs never contain the raw key.

**Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api.py -k idempotency -v`

Expected: FAIL because the Header is ignored.

**Step 3: Implement canonical fingerprints**

Hash the raw Header immediately with SHA-256. Build `request_fingerprint` from canonical JSON of all execution-affecting request fields. Call `create_or_get`; start a background thread only when `created` is true. Return the existing status otherwise.

**Step 4: Run API tests**

Expected: all API tests pass.

**Step 5: Commit checkpoint**

```bash
git add src/workpilot/api tests/test_api.py
git commit -m "feat(api): make run creation idempotent"
```

### Task 6: Persist Runtime PlanStep and ToolCall lifecycle

**Files:**
- Create: `src/workpilot/runtime/observer.py`
- Create: `src/workpilot/persistence/execution_observer.py`
- Modify: `src/workpilot/runtime/runner.py`
- Modify: `src/workpilot/api/app.py`
- Create: `tests/test_execution_observer.py`
- Modify: `tests/test_smoke.py`

**Step 1: Write failing observer tests**

Define a recording observer and assert event order:

```text
plan_registered
step_started/tool_call_started
step_completed/tool_call_completed
...
artifact_recorded
```

Assert `tool_call_id` and key hash are deterministic from Run/Step/logical execution/tool version/canonical inputs; Trace and ledger agree on attempts.

**Step 2: Define a no-op-safe observer protocol**

Runtime accepts an optional observer with explicit methods. The default is a Null observer so CLI and unit tests do not require persistence. Observer exceptions are infrastructure failures and fail the Run; they must not be swallowed or converted into success.

**Step 3: Implement RepositoryExecutionObserver**

Translate validated Plan and safe ToolResult summaries to persistence records. Do not pass raw ToolResult output or Provider content to the Repository.

**Step 4: Wire Runtime lifecycle**

Register all PlanSteps only after Plan validation. Around `_execute_plan_step_result`, persist start and atomic completion/failure. Use the same attempt values already written to Trace.

**Step 5: Wire API persistent mode**

When the injected repository also satisfies `ExecutionRepository`, pass a repository observer to Runtime. InMemory development mode implements the same protocol.

**Step 6: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_execution_observer.py tests\test_smoke.py tests\test_api.py -q`

Expected: PASS.

**Step 7: Commit checkpoint**

```bash
git add src/workpilot/runtime src/workpilot/persistence src/workpilot/api tests
git commit -m "feat(runtime): persist plan and tool execution ledger"
```

### Task 7: Persist Artifact Metadata without claiming atomic checkpoints

**Files:**
- Modify: `src/workpilot/artifacts/writer.py`
- Modify: `src/workpilot/runtime/runner.py`
- Modify: `src/workpilot/persistence/execution_observer.py`
- Create: `tests/test_artifact_metadata.py`

**Step 1: Write failing metadata tests**

Assert every successful Writer call records relative path, SHA-256, byte size, media type and monotonically increasing version. Assert path traversal and files outside the Run output directory fail closed.

**Step 2: Add a post-write callback**

`ArtifactWriter` invokes the callback only after a complete write. Use a same-directory temporary file plus atomic replace so readers never see partial content. This phase records metadata after the replace; database/file crash consistency remains HA-02B scope.

**Step 3: Register metadata through the observer**

Compute metadata from bytes actually on disk. Do not hash an in-memory representation that could differ from the file.

**Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_artifact_metadata.py tests\test_smoke.py -q`

Expected: PASS.

**Step 5: Commit checkpoint**

```bash
git add src/workpilot/artifacts src/workpilot/runtime src/workpilot/persistence tests/test_artifact_metadata.py
git commit -m "feat(artifacts): persist versioned artifact metadata"
```

### Task 8: Real PostgreSQL acceptance and documentation

**Files:**
- Modify: `tests/test_sqlalchemy_run_repository.py`
- Modify: `docs/03-实现细节/11-持久化存储.md`
- Modify: `docs/03-实现细节/13-高可用运行.md`
- Modify: `docs/06-项目状态/01-当前实现状态.md`
- Modify: `docs/06-项目状态/03-变更记录.md`
- Modify: `docs/02-项目开发计划/09-下一阶段改进路线图.md`

**Step 1: Run isolated PostgreSQL 17 acceptance**

On a disposable database:

1. upgrade `20260722_0001 -> 20260722_0002` with an existing Run present;
2. verify Run data survives;
3. exercise concurrent idempotent creation;
4. execute one API Run and query PlanStep/ToolCall/Artifact rows;
5. downgrade to `20260722_0001`, then upgrade again.

**Step 2: Run all verification**

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q src tests migrations
Set-Location web; npm run build
git diff --check
```

Expected: all tests pass; only explicitly conditional PostgreSQL cases skip without a database URL.

**Step 3: Update documentation truthfully**

Record HA-02A as a durable execution ledger, not checkpoint recovery. Keep HA-02B/C/D, auth, queue, cancellation and multi-instance recovery incomplete.

**Step 4: Commit checkpoint**

```bash
git add docs tests
git commit -m "docs: record HA-02A execution ledger acceptance"
```
