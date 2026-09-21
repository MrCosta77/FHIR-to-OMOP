from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dqd_viewer_uses_configured_directory_and_explicit_run_selection():
    source = (PROJECT_ROOT / "src/analytics/view_dqd_dashboard.R").read_text(
        encoding="utf-8"
    )
    assert 'Sys.getenv(\n  "CMF_DQD_RESULTS_DIR"' in source
    assert 'Sys.getenv("CMF_DQD_REPORT"' in source
    assert "which.max" not in source
    assert "Multiple DQD reports found" in source


def test_dqd_resume_is_bound_to_database_thresholds_and_checkset():
    source = (PROJECT_ROOT / "src/analytics/run_dqd_tests.R").read_text(
        encoding="utf-8"
    )
    assert "database_md5" in source
    assert "thresholds_md5" in source
    assert "dqd_version" in source
    assert ".cmf-shard-manifest.json" in source
    assert "shard contract does not match current inputs" in source
