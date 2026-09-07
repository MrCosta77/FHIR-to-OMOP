"""Validate reproducibility and release metadata without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REQUIRED_R_PACKAGES = {
    "renv",
    "DataQualityDashboard",
    "DatabaseConnector",
    "readr",
    "rstudioapi",
    "shiny",
}


class ReleaseMetadataError(RuntimeError):
    """Raised when versioned release evidence is missing or inconsistent."""


def _read(path: Path) -> str:
    if not path.is_file():
        raise ReleaseMetadataError(f"Missing required release file: {path.name}")
    return path.read_text(encoding="utf-8")


def _direct_python_packages(text: str) -> set[str]:
    packages: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if not match:
            raise ReleaseMetadataError(f"Invalid requirements.in line: {raw_line}")
        packages.add(match.group(1).lower().replace("_", "-"))
    return packages


def _cff_scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if not match:
        raise ReleaseMetadataError(f"CITATION.cff is missing required field: {key}")
    return match.group(1).strip().strip('"').strip("'")


def _validate_citation(citation: str, version: str) -> None:
    if _cff_scalar(citation, "cff-version") != "1.2.0":
        raise ReleaseMetadataError("CITATION.cff must use CFF 1.2.0")
    if _cff_scalar(citation, "title") != "FHIR-to-OMOP: Clinical Mapping Framework":
        raise ReleaseMetadataError("CITATION.cff title does not identify this project")
    if _cff_scalar(citation, "license") != "Apache-2.0":
        raise ReleaseMetadataError("CITATION.cff license must be Apache-2.0")
    if _cff_scalar(citation, "version") != version:
        raise ReleaseMetadataError("CITATION.cff version does not match VERSION")
    try:
        date.fromisoformat(_cff_scalar(citation, "date-released"))
    except ValueError as exc:
        raise ReleaseMetadataError("CITATION.cff date-released is not an ISO date") from exc
    repository = _cff_scalar(citation, "repository-code")
    if repository != "https://github.com/MrCosta77/FHIR-to-OMOP":
        raise ReleaseMetadataError("CITATION.cff repository-code is not canonical")
    if not re.search(r"(?m)^\s*- family-names:\s*\S", citation):
        raise ReleaseMetadataError("CITATION.cff has no author family-names")
    if not re.search(r"(?m)^\s+given-names:\s*\S", citation):
        raise ReleaseMetadataError("CITATION.cff has no author given-names")


def _unreleased_notes(changelog: str) -> str:
    match = re.search(
        r"(?ms)^## \[Unreleased\]\s*(.*?)(?=^## \[|\Z)",
        changelog,
    )
    return match.group(1).strip() if match else ""


def validate_release_metadata(root: Path = ROOT, *, release: bool = False) -> dict:
    version = _read(root / "VERSION").strip()
    if not SEMVER.fullmatch(version):
        raise ReleaseMetadataError(f"VERSION is not stable SemVer: {version!r}")

    try:
        project_metadata = tomllib.loads(_read(root / "pyproject.toml"))
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseMetadataError(f"pyproject.toml is invalid TOML: {exc}") from exc
    package_version = str(project_metadata.get("project", {}).get("version", ""))
    if package_version != version:
        raise ReleaseMetadataError(
            "pyproject.toml project.version does not match VERSION: "
            f"{package_version!r} != {version!r}"
        )
    requires_python = str(project_metadata.get("project", {}).get("requires-python", ""))
    if requires_python != ">=3.12":
        raise ReleaseMetadataError(
            "pyproject.toml must declare the tested Python contract >=3.12"
        )

    requirements_in = _read(root / "requirements.in")
    requirements_lock = _read(root / "requirements.lock")
    declared_dependencies = set(
        project_metadata.get("project", {}).get("dependencies", ())
    )
    direct_requirements = {
        line.strip()
        for line in requirements_in.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing_declarations = sorted(declared_dependencies - direct_requirements)
    if missing_declarations:
        raise ReleaseMetadataError(
            "pyproject runtime dependencies differ from requirements.in: "
            + ", ".join(missing_declarations)
        )
    if "--generate-hashes" not in requirements_lock or "--hash=sha256:" not in requirements_lock:
        raise ReleaseMetadataError("requirements.lock is not a generated hashed lock")
    direct_python = _direct_python_packages(requirements_in)
    missing_python = sorted(
        package
        for package in direct_python
        if not re.search(rf"(?m)^{re.escape(package)}==[^\s\\]+", requirements_lock)
    )
    if missing_python:
        raise ReleaseMetadataError(
            "Direct Python dependencies missing exact lock entries: "
            + ", ".join(missing_python)
        )

    try:
        renv_lock = json.loads(_read(root / "renv.lock"))
    except json.JSONDecodeError as exc:
        raise ReleaseMetadataError(f"renv.lock is invalid JSON: {exc}") from exc
    r_version = str(renv_lock.get("R", {}).get("Version", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", r_version):
        raise ReleaseMetadataError("renv.lock does not pin a complete R version")
    r_packages = renv_lock.get("Packages", {})
    missing_r = sorted(REQUIRED_R_PACKAGES - set(r_packages))
    if missing_r:
        raise ReleaseMetadataError("Required R packages missing from renv.lock: " + ", ".join(missing_r))
    unversioned_r = sorted(
        package for package in REQUIRED_R_PACKAGES if not r_packages[package].get("Version")
    )
    if unversioned_r:
        raise ReleaseMetadataError("Unversioned R packages: " + ", ".join(unversioned_r))

    changelog = _read(root / "CHANGELOG.md")
    if f"## [{version}]" not in changelog:
        raise ReleaseMetadataError(f"CHANGELOG.md has no section for VERSION {version}")

    license = _read(root / "LICENSE")
    if "Apache License" not in license or "Version 2.0, January 2004" not in license:
        raise ReleaseMetadataError("LICENSE is not the canonical Apache-2.0 text")
    notice = _read(root / "NOTICE")
    if "FHIR-to-OMOP" not in notice or "Copyright" not in notice:
        raise ReleaseMetadataError("NOTICE has no project copyright attribution")

    citation = _read(root / "CITATION.cff")
    _validate_citation(citation, version)

    quality_workflow = _read(root / ".github" / "workflows" / "quality.yml")
    if "requirements.lock" not in quality_workflow or "--require-hashes" not in quality_workflow:
        raise ReleaseMetadataError("Quality CI does not install the hashed Python lock")
    if 'python-version: "3.12"' not in quality_workflow:
        raise ReleaseMetadataError("Quality CI does not test the declared Python 3.12 contract")

    ruff_match = re.search(r"(?m)^ruff==([^\s]+)$", requirements_in)
    if not ruff_match:
        raise ReleaseMetadataError("requirements.in does not pin Ruff")
    ruff_version = ruff_match.group(1)
    pre_commit = _read(root / ".pre-commit-config.yaml")
    if f"rev: v{ruff_version}" not in pre_commit:
        raise ReleaseMetadataError("pre-commit Ruff version does not match requirements.in")
    if "ruff-format" in pre_commit or "--fix" in pre_commit:
        raise ReleaseMetadataError(
            "pre-commit must match CI lint checks without unreviewed formatting or fixes"
        )

    environment_example = _read(root / ".env.example")
    if "CMF_OLLAMA_TIMEOUT_SECONDS" not in environment_example:
        raise ReleaseMetadataError(".env.example does not document the Ollama timeout")

    if release:
        if "Select and add the project license" in changelog:
            raise ReleaseMetadataError("CHANGELOG.md still marks the license as pending")
        if _unreleased_notes(changelog):
            raise ReleaseMetadataError(
                "CHANGELOG.md has Unreleased changes that must be assigned to a version"
            )

    return {
        "version": version,
        "python_direct_dependencies": len(direct_python),
        "r_version": r_version,
        "r_packages": len(r_packages),
        "license": "Apache-2.0",
        "release_ready": release,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="also require the license and absence of pending release blockers",
    )
    args = parser.parse_args()
    print(json.dumps(validate_release_metadata(release=args.release), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

