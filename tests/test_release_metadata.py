import json
import re
from pathlib import Path

import pytest

from src.quality.release_metadata import ReleaseMetadataError, validate_release_metadata

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = [
    "VERSION",
    "pyproject.toml",
    "requirements.in",
    "requirements.lock",
    "renv.lock",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "CITATION.cff",
    ".github/workflows/quality.yml",
    ".pre-commit-config.yaml",
    ".env.example",
]


def _copy_release_files(destination_root):
    for relative in RELEASE_FILES:
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())


def _clear_unreleased_notes(root):
    path = root / "CHANGELOG.md"
    changelog = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(
            r"(?ms)(^## \[Unreleased\]\s*).*?(?=^## \[)",
            r"\1",
            changelog,
            count=1,
        ),
        encoding="utf-8",
    )


def test_versioned_dependency_locks_and_changelog_are_consistent():
    result = validate_release_metadata(ROOT)

    assert result == {
        "version": "0.3.0",
        "python_direct_dependencies": 8,
        "r_version": "4.6.1",
        "r_packages": 71,
        "license": "Apache-2.0",
        "release_ready": False,
    }


def test_release_gate_accepts_versioned_changes(tmp_path):
    _copy_release_files(tmp_path)
    _clear_unreleased_notes(tmp_path)

    assert validate_release_metadata(tmp_path, release=True)["release_ready"] is True


def test_release_gate_rejects_unversioned_changes(tmp_path):
    _copy_release_files(tmp_path)
    _clear_unreleased_notes(tmp_path)
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        changelog.replace("## [Unreleased]", "## [Unreleased]\n\n- Pending change", 1),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="Unreleased changes"):
        validate_release_metadata(tmp_path, release=True)


def test_release_validator_rejects_missing_required_r_package(tmp_path):
    _copy_release_files(tmp_path)

    lock = json.loads((ROOT / "renv.lock").read_text(encoding="utf-8"))
    del lock["Packages"]["DataQualityDashboard"]
    (tmp_path / "renv.lock").write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ReleaseMetadataError, match="DataQualityDashboard"):
        validate_release_metadata(tmp_path)


def test_release_validator_rejects_package_version_mismatch(tmp_path):
    _copy_release_files(tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        pyproject.replace('version = "0.3.0"', 'version = "9.9.9"'),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="does not match VERSION"):
        validate_release_metadata(tmp_path)


def test_release_validator_rejects_citation_version_mismatch(tmp_path):
    _copy_release_files(tmp_path)
    citation = (tmp_path / "CITATION.cff").read_text(encoding="utf-8")
    (tmp_path / "CITATION.cff").write_text(
        citation.replace("version: 0.3.0", "version: 9.9.9"),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="CITATION.cff version"):
        validate_release_metadata(tmp_path)


def test_release_validator_rejects_python_contract_drift(tmp_path):
    _copy_release_files(tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        pyproject.replace('requires-python = ">=3.12"', 'requires-python = ">=3.11"'),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="Python contract"):
        validate_release_metadata(tmp_path)


def test_release_validator_rejects_runtime_dependency_drift(tmp_path):
    _copy_release_files(tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        pyproject.replace("streamlit==1.62.0", "streamlit>=1.30.0,<2"),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="requirements.in"):
        validate_release_metadata(tmp_path)


def test_release_validator_rejects_precommit_ruff_drift(tmp_path):
    _copy_release_files(tmp_path)
    pre_commit = (tmp_path / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    (tmp_path / ".pre-commit-config.yaml").write_text(
        pre_commit.replace("rev: v0.16.5", "rev: v0.3.0"),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseMetadataError, match="Ruff version"):
        validate_release_metadata(tmp_path)
