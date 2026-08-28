"""Validate reproducibility and release metadata without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
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


def validate_release_metadata(root: Path = ROOT, *, release: bool = False) -> dict:
    version = _read(root / "VERSION").strip()
    if not SEMVER.fullmatch(version):
        raise ReleaseMetadataError(f"VERSION is not stable SemVer: {version!r}")

    requirements_in = _read(root / "requirements.in")
    requirements_lock = _read(root / "requirements.lock")
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

    licence = _read(root / "LICENSE")
    if "Apache License" not in licence or "Version 2.0, January 2004" not in licence:
        raise ReleaseMetadataError("LICENSE is not the canonical Apache-2.0 text")
    notice = _read(root / "NOTICE")
    if "FHIR-to-OMOP" not in notice or "Copyright" not in notice:
        raise ReleaseMetadataError("NOTICE has no project copyright attribution")

    quality_workflow = _read(root / ".github" / "workflows" / "quality.yml")
    if "requirements.lock" not in quality_workflow or "--require-hashes" not in quality_workflow:
        raise ReleaseMetadataError("Quality CI does not install the hashed Python lock")

    if release:
        if "Select and add the project licence" in changelog:
            raise ReleaseMetadataError("CHANGELOG.md still marks the licence as pending")

    return {
        "version": version,
        "python_direct_dependencies": len(direct_python),
        "r_version": r_version,
        "r_packages": len(r_packages),
        "licence": "Apache-2.0",
        "release_ready": release,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="also require the licence and absence of pending release blockers",
    )
    args = parser.parse_args()
    print(json.dumps(validate_release_metadata(release=args.release), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
