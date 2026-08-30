"""Single, validated runtime configuration for every project entry point."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.security.privacy import validate_privacy_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_PROFILES = {"development", "benchmark", "hospital"}
PROFILE_SCHEMA_VERSION = "1.0.0"
PROFILE_KEYS = {
    "db_path", "fhir_dir", "vocab_dir", "chroma_path", "runs_dir",
    "manifests_dir", "reports_dir", "dqd_results_dir", "ollama_url",
    "ollama_timeout", "model_name", "similarity_threshold", "data_classification",
    "simulate_lis_noise", "require_integration", "include_dqd",
}
ENVIRONMENT_KEYS = {
    "db_path": "CMF_DB_PATH",
    "fhir_dir": "CMF_FHIR_DIR",
    "vocab_dir": "CMF_VOCAB_DIR",
    "chroma_path": "CMF_CHROMA_PATH",
    "runs_dir": "CMF_RUNS_DIR",
    "manifests_dir": "CMF_MANIFESTS_DIR",
    "reports_dir": "CMF_REPORTS_DIR",
    "dqd_results_dir": "CMF_DQD_RESULTS_DIR",
    "ollama_url": "CMF_OLLAMA_URL",
    "ollama_timeout": "CMF_OLLAMA_TIMEOUT_SECONDS",
    "model_name": "CMF_MODEL_NAME",
    "similarity_threshold": "CMF_SIMILARITY_THRESHOLD",
    "data_classification": "CMF_DATA_CLASSIFICATION",
    "simulate_lis_noise": "CMF_SIMULATE_LIS_NOISE",
    "require_integration": "CMF_REQUIRE_INTEGRATION",
    "include_dqd": "CMF_INCLUDE_DQD",
}
PATH_KEYS = {
    "db_path", "fhir_dir", "vocab_dir", "chroma_path", "runs_dir",
    "manifests_dir", "reports_dir", "dqd_results_dir",
}


class SettingsError(ValueError):
    """Raised when runtime configuration is missing, invalid or unsafe."""


@dataclass(frozen=True)
class RuntimeSettings:
    profile: str
    project_root: Path
    db_path: Path
    fhir_dir: Path
    vocab_dir: Path
    chroma_path: Path
    runs_dir: Path
    manifests_dir: Path
    reports_dir: Path
    dqd_results_dir: Path
    ollama_url: str
    ollama_timeout: float
    model_name: str
    similarity_threshold: float
    data_classification: str
    simulate_lis_noise: bool
    require_integration: bool
    include_dqd: bool

    def manifest(self) -> dict:
        """Return non-secret settings suitable for immutable run provenance."""
        return {
            "profile": self.profile,
            "db_path": str(self.db_path),
            "fhir_dir": str(self.fhir_dir),
            "vocab_dir": str(self.vocab_dir),
            "chroma_path": str(self.chroma_path),
            "runs_dir": str(self.runs_dir),
            "manifests_dir": str(self.manifests_dir),
            "reports_dir": str(self.reports_dir),
            "dqd_results_dir": str(self.dqd_results_dir),
            "ollama_url": self.ollama_url,
            "ollama_timeout": self.ollama_timeout,
            "model_name": self.model_name,
            "similarity_threshold": self.similarity_threshold,
            "data_classification": self.data_classification,
            "simulate_lis_noise": self.simulate_lis_noise,
            "require_integration": self.require_integration,
            "include_dqd": self.include_dqd,
        }


def _parse_boolean(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise SettingsError(f"{name} must be true or false.")


def _resolve_path(value, project_root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _load_profile(profile: str, profile_root: Path) -> dict:
    if profile not in SUPPORTED_PROFILES:
        raise SettingsError(
            f"Unsupported CMF_PROFILE {profile!r}; expected one of "
            f"{sorted(SUPPORTED_PROFILES)}."
        )
    path = profile_root / f"{profile}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError(f"Configuration profile is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(f"Configuration profile is invalid JSON: {path}") from exc
    if document.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise SettingsError(f"Unsupported configuration schema in {path}.")
    if document.get("profile") != profile:
        raise SettingsError(f"Configuration profile identity mismatch in {path}.")
    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        raise SettingsError(f"Configuration profile defaults must be an object: {path}")
    unknown = set(defaults) - PROFILE_KEYS
    missing = PROFILE_KEYS - set(defaults)
    if unknown or missing:
        raise SettingsError(
            f"Configuration profile keys are invalid; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}."
        )
    return defaults.copy()


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    profile_root: Path | None = None,
) -> RuntimeSettings:
    """Load one versioned profile, apply explicit environment overrides and validate."""
    env = os.environ if environ is None else environ
    root = Path(project_root).resolve()
    profiles = Path(profile_root or root / "config" / "profiles")
    profile = env.get("CMF_PROFILE", "development").strip().casefold()
    values = _load_profile(profile, profiles)

    for key, environment_key in ENVIRONMENT_KEYS.items():
        if environment_key in env:
            values[key] = env[environment_key]
    if "CMF_SIMULATE_LIS_NOISE" not in env and "SIMULATE_LIS_NOISE" in env:
        values["simulate_lis_noise"] = env["SIMULATE_LIS_NOISE"]

    parsed_paths = {key: _resolve_path(values[key], root) for key in PATH_KEYS}
    try:
        threshold = float(values["similarity_threshold"])
    except (TypeError, ValueError) as exc:
        raise SettingsError("CMF_SIMILARITY_THRESHOLD must be numeric.") from exc
    if not 0.0 <= threshold <= 1.0:
        raise SettingsError("CMF_SIMILARITY_THRESHOLD must be between 0 and 1.")

    try:
        ollama_timeout = float(values.get("ollama_timeout", 120.0))
        if not math.isfinite(ollama_timeout) or ollama_timeout <= 0:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise SettingsError("CMF_OLLAMA_TIMEOUT_SECONDS must be a finite positive number.") from exc

    model_name = str(values["model_name"]).strip()
    ollama_url = str(values["ollama_url"]).strip()
    classification = str(values["data_classification"]).strip().upper()
    simulate_lis_noise = _parse_boolean(
        values["simulate_lis_noise"], "CMF_SIMULATE_LIS_NOISE"
    )
    require_integration = _parse_boolean(
        values["require_integration"], "CMF_REQUIRE_INTEGRATION"
    )
    include_dqd = _parse_boolean(values["include_dqd"], "CMF_INCLUDE_DQD")
    if not model_name:
        raise SettingsError("CMF_MODEL_NAME must not be empty.")
    if profile == "hospital":
        if classification != "PHI":
            raise SettingsError("The hospital profile cannot downgrade PHI classification.")
        if threshold != 1.0:
            raise SettingsError(
                "The hospital profile requires threshold 1.0 until clinical authorization."
            )
        if simulate_lis_noise:
            raise SettingsError("LIS noise simulation is forbidden in the hospital profile.")
        if not require_integration:
            raise SettingsError("The hospital profile requires the complete integration gate.")
        if not include_dqd:
            raise SettingsError("The hospital profile requires the OHDSI DQD gate.")
    privacy_environment = dict(env)
    privacy_environment["CMF_DATA_CLASSIFICATION"] = classification
    privacy_environment["CMF_OLLAMA_URL"] = ollama_url
    try:
        privacy = validate_privacy_runtime(ollama_url, privacy_environment)
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc

    return RuntimeSettings(
        profile=profile,
        project_root=root,
        **parsed_paths,
        ollama_url=ollama_url,
        ollama_timeout=ollama_timeout,
        model_name=model_name,
        similarity_threshold=threshold,
        data_classification=privacy["classification"],
        simulate_lis_noise=simulate_lis_noise,
        require_integration=require_integration,
        include_dqd=include_dqd,
    )


SETTINGS = load_settings()

# Compatibility exports for existing ETL modules. New code should use SETTINGS.
DB_PATH = str(SETTINGS.db_path)
FHIR_DIR = str(SETTINGS.fhir_dir)
VOCAB_DIR = str(SETTINGS.vocab_dir)
CHROMA_PATH = str(SETTINGS.chroma_path)
RUNS_DIR = str(SETTINGS.runs_dir)
MANIFESTS_DIR = str(SETTINGS.manifests_dir)
REPORTS_DIR = str(SETTINGS.reports_dir)
DQD_RESULTS_DIR = str(SETTINGS.dqd_results_dir)
OLLAMA_URL = SETTINGS.ollama_url
OLLAMA_TIMEOUT = SETTINGS.ollama_timeout
MODEL_NAME = SETTINGS.model_name
SIMILARITY_THRESHOLD = SETTINGS.similarity_threshold
SIMULATE_LIS_NOISE = SETTINGS.simulate_lis_noise
REQUIRE_INTEGRATION = SETTINGS.require_integration
INCLUDE_DQD = SETTINGS.include_dqd
PROFILE = SETTINGS.profile


def main():
    print(json.dumps(SETTINGS.manifest(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
