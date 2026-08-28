import json
from pathlib import Path

import pytest

from src.quality.release_metadata import ReleaseMetadataError, validate_release_metadata


ROOT = Path(__file__).resolve().parents[1]


def test_versioned_dependency_locks_and_changelog_are_consistent():
    result = validate_release_metadata(ROOT)

    assert result == {
        "version": "0.1.0",
        "python_direct_dependencies": 7,
        "r_version": "4.6.1",
        "r_packages": 71,
        "licence": "Apache-2.0",
        "release_ready": False,
    }


def test_release_gate_accepts_apache_2_metadata():
    assert validate_release_metadata(ROOT, release=True)["release_ready"] is True


def test_release_validator_rejects_missing_required_r_package(tmp_path):
    for relative in [
        "VERSION",
        "requirements.in",
        "requirements.lock",
        "CHANGELOG.md",
        "LICENSE",
        "NOTICE",
        ".github/workflows/quality.yml",
    ]:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())

    lock = json.loads((ROOT / "renv.lock").read_text(encoding="utf-8"))
    del lock["Packages"]["DataQualityDashboard"]
    (tmp_path / "renv.lock").write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ReleaseMetadataError, match="DataQualityDashboard"):
        validate_release_metadata(tmp_path)
