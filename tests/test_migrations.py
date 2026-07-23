"""Static migration-chain checks that do not require a live database."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def test_migration_chain_has_single_run_lease_head() -> None:
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260722_0004"]
    revision = scripts.get_revision("20260722_0004")
    assert revision is not None
    assert revision.down_revision == "20260722_0003"
    assert "Add fenced Run leases" in revision.doc


def test_migration_upgrades_and_downgrades_clean_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = Path(__file__).parents[1]
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("WORKPILOT_DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {
            "alembic_version",
            "runs",
            "plan_steps",
            "tool_calls",
            "artifact_metadata",
            "checkpoints",
        } <= set(inspector.get_table_names())
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        assert {
            "idempotency_key_hash",
            "request_fingerprint",
            "checkpoint_sequence",
            "lease_owner",
            "lease_expires_at",
            "execution_attempt",
        } <= run_columns
        step_columns = {
            column["name"] for column in inspector.get_columns("plan_steps")
        }
        assert "checkpoint_sequence" in step_columns
    finally:
        engine.dispose()

    command.downgrade(config, "20260722_0003")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "runs" in inspector.get_table_names()
        assert "checkpoints" in inspector.get_table_names()
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        assert not {
            "lease_owner",
            "lease_expires_at",
            "execution_attempt",
        } & run_columns
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    command.downgrade(config, "20260722_0002")
    command.downgrade(config, "20260722_0001")
    command.downgrade(config, "base")


def test_checkpoint_migration_preserves_execution_ledger_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = Path(__file__).parents[1]
    database_path = tmp_path / "migration-existing.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("WORKPILOT_DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "20260722_0002")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO runs (
                        run_id, goal, workspace, provider, output_dir, state,
                        failure_reason, idempotency_key_hash, request_fingerprint,
                        created_at, updated_at, version
                    ) VALUES (
                        'run_existing', 'goal', 'workspace', 'stub', 'runs/existing',
                        'running', NULL, NULL, NULL,
                        '2026-07-22 00:00:00', '2026-07-22 00:00:00', 1
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO plan_steps (
                        run_id, step_id, plan_id, objective, tool, tool_version,
                        dependencies, expected_output, success_rule_ids, input_hash,
                        status, attempts, last_error_type, success_evaluation,
                        created_at, updated_at, version
                    ) VALUES (
                        'run_existing', 'scan_workspace', 'plan_existing', 'scan',
                        'workspace.scan', '1.0', '[]', 'files',
                        '["tool_result.completed"]', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        'pending', 0, NULL, NULL,
                        '2026-07-22 00:00:00', '2026-07-22 00:00:00', 1
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            run = connection.execute(
                text(
                    "SELECT state, checkpoint_sequence, lease_owner, "
                    "lease_expires_at, execution_attempt FROM runs "
                    "WHERE run_id = 'run_existing'"
                )
            ).one()
            step = connection.execute(
                text(
                    "SELECT status, checkpoint_sequence FROM plan_steps "
                    "WHERE run_id = 'run_existing' AND step_id = 'scan_workspace'"
                )
            ).one()
        assert tuple(run) == ("running", 0, None, None, 0)
        assert tuple(step) == ("pending", None)
    finally:
        engine.dispose()

    command.downgrade(config, "20260722_0002")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT state FROM runs WHERE run_id = 'run_existing'")
            ).scalar_one() == "running"
            assert connection.execute(
                text(
                    "SELECT status FROM plan_steps "
                    "WHERE run_id = 'run_existing' AND step_id = 'scan_workspace'"
                )
            ).scalar_one() == "pending"
    finally:
        engine.dispose()

    command.downgrade(config, "base")
