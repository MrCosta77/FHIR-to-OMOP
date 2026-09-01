import json

import duckdb

import main


def test_runtime_paths_honor_isolation_environment(tmp_path):
    environment = {
        "CMF_DB_PATH": str(tmp_path / "published.duckdb"),
        "CMF_FHIR_DIR": str(tmp_path / "fhir"),
        "CMF_RUNS_DIR": str(tmp_path / "runs"),
        "CMF_MANIFESTS_DIR": str(tmp_path / "manifests"),
        "CMF_REPORTS_DIR": str(tmp_path / "reports"),
        "CMF_DQD_RESULTS_DIR": str(tmp_path / "dqd"),
    }
    paths = main.resolve_runtime_paths(environment)
    assert paths == {
        "published_db": (tmp_path / "published.duckdb").resolve(),
        "fhir_dir": (tmp_path / "fhir").resolve(),
        "runs_dir": (tmp_path / "runs").resolve(),
        "manifests_dir": (tmp_path / "manifests").resolve(),
        "reports_dir": (tmp_path / "reports").resolve(),
        "dqd_results_dir": (tmp_path / "dqd").resolve(),
    }


def test_staging_copy_isolated_from_published_database(monkeypatch, tmp_path):
    published = tmp_path / "published.duckdb"
    runs = tmp_path / "runs"
    with duckdb.connect(str(published)) as con:
        con.execute("CREATE TABLE marker(value INTEGER)")
        con.execute("INSERT INTO marker VALUES (1)")

    monkeypatch.setattr(main, "PUBLISHED_DB", published)
    monkeypatch.setattr(main, "RUNS_DIR", runs)
    staging = main.prepare_staging_database("RUN-test")
    with duckdb.connect(str(staging)) as con:
        con.execute("UPDATE marker SET value = 2")

    with duckdb.connect(str(published), read_only=True) as con:
        assert con.execute("SELECT value FROM marker").fetchone()[0] == 1


def test_manifest_write_is_atomic_and_valid_json(tmp_path):
    path = tmp_path / "manifest.json"
    main.write_manifest(path, {"run_id": "RUN-test", "status": "RUNNING"})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run_id": "RUN-test", "status": "RUNNING"
    }
    assert not path.with_suffix(".tmp").exists()


def test_manifest_write_retries_transient_windows_lock(monkeypatch, tmp_path):
    real_replace = main.os.replace
    attempts = []

    def flaky_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise PermissionError("transient OneDrive lock")
        return real_replace(source, destination)

    monkeypatch.setattr(main.os, "replace", flaky_replace)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    path = tmp_path / "manifest.json"
    main.write_manifest(path, {"status": "RUNNING"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "RUNNING"}
    assert len(attempts) == 3


def test_missing_step_fails_closed(tmp_path):
    try:
        main.run_step({"name": "missing", "script": "does-not-exist.py"}, {})
    except FileNotFoundError as exc:
        assert "Required pipeline step" in str(exc)
    else:
        raise AssertionError("Missing mandatory step was silently skipped")


def test_dirty_worktree_is_fingerprinted_without_persisting_diff(monkeypatch):
    class Result:
        stdout = " M src/etl/drug.py\n?? local-note.txt\n"

    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: Result())
    provenance = main.git_worktree_provenance()
    assert provenance["git_dirty"] is True
    assert len(provenance["git_status_sha256"]) == 64
    assert "src/etl/drug.py" not in str(provenance)


def test_pytest_step_emits_run_scoped_junit(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command

    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.run_step(
        {"name": "quality", "script": "tests", "is_pytest": True},
        {"CMF_RUN_ID": "RUN-test"},
    )
    assert f"--junitxml={tmp_path / 'RUN-test.pytest.xml'}" in captured["command"]


def test_run_manifest_is_persisted_in_database(tmp_path):
    database = tmp_path / "run.duckdb"
    manifest = {
        "run_id": "RUN-test",
        "status": "RUNNING",
        "started_at": "2026-08-26T10:00:00+00:00",
        "git_commit": "abc123",
        "inputs": [{"path": "input.json", "sha256": "deadbeef"}],
        "configuration": {"threshold": "0.90"},
        "steps": [],
    }
    main.persist_run(database, manifest)
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("""
            SELECT run_id, status, git_commit FROM etl_run
        """).fetchone() == ("RUN-test", "RUNNING", "abc123")
