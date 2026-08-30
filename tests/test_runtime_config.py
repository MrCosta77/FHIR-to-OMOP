import json

import pytest

from src.utils.config import SettingsError, load_settings


def test_development_profile_resolves_portable_paths(tmp_path):
    profiles = tmp_path / "config" / "profiles"
    profiles.mkdir(parents=True)
    source = load_settings().project_root / "config" / "profiles" / "development.json"
    (profiles / "development.json").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    settings = load_settings({}, project_root=tmp_path, profile_root=profiles)
    assert settings.profile == "development"
    assert settings.db_path == (tmp_path / "data" / "omop_clinical.duckdb").resolve()
    assert settings.fhir_dir == (tmp_path / "synthea" / "output" / "fhir").resolve()
    assert settings.reports_dir == (tmp_path / "data" / "run_reports").resolve()


def test_environment_overrides_profile_without_loading_dotenv(tmp_path):
    settings = load_settings({
        "CMF_DB_PATH": str(tmp_path / "isolated.duckdb"),
        "CMF_MODEL_NAME": "local-test-model",
        "CMF_SIMILARITY_THRESHOLD": "0.75",
    })
    assert settings.db_path == (tmp_path / "isolated.duckdb").resolve()
    assert settings.model_name == "local-test-model"
    assert settings.similarity_threshold == 0.75


@pytest.mark.parametrize("value", ["-0.1", "1.1", "not-a-number"])
def test_invalid_threshold_fails_closed(value):
    with pytest.raises(SettingsError, match="SIMILARITY_THRESHOLD"):
        load_settings({"CMF_SIMILARITY_THRESHOLD": value})


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf", "not-a-number"])
def test_invalid_ollama_timeout_fails_closed(value):
    with pytest.raises(SettingsError, match="finite positive number"):
        load_settings({"CMF_OLLAMA_TIMEOUT_SECONDS": value})


def test_unknown_profile_fails_closed():
    with pytest.raises(SettingsError, match="Unsupported CMF_PROFILE"):
        load_settings({"CMF_PROFILE": "typo"})


def test_external_llm_endpoint_fails_closed():
    with pytest.raises(SettingsError, match="External LLM endpoint"):
        load_settings({"CMF_OLLAMA_URL": "https://external.example/api/generate"})


def test_hospital_profile_requires_explicit_phi_approval():
    with pytest.raises(SettingsError, match="CMF_PHI_ENABLED"):
        load_settings({"CMF_PROFILE": "hospital"})


def test_hospital_profile_accepts_complete_phi_activation():
    settings = load_settings({
        "CMF_PROFILE": "hospital",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
    })
    assert settings.profile == "hospital"
    assert settings.data_classification == "PHI"
    assert settings.similarity_threshold == 1.0
    assert settings.include_dqd is True


@pytest.mark.parametrize("override", [
    {"CMF_DATA_CLASSIFICATION": "SYNTHETIC"},
    {"CMF_SIMILARITY_THRESHOLD": "0.9"},
    {"CMF_SIMULATE_LIS_NOISE": "true"},
    {"CMF_REQUIRE_INTEGRATION": "false"},
    {"CMF_INCLUDE_DQD": "false"},
])
def test_hospital_profile_cannot_downgrade_safety(override):
    environment = {
        "CMF_PROFILE": "hospital",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
        **override,
    }
    with pytest.raises(SettingsError):
        load_settings(environment)


def test_invalid_boolean_fails_closed():
    with pytest.raises(SettingsError, match="must be true or false"):
        load_settings({"CMF_REQUIRE_INTEGRATION": "sometimes"})


def test_profile_rejects_unknown_keys(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    source = load_settings().project_root / "config" / "profiles" / "development.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    document["defaults"]["ollama_typo"] = "unsafe"
    (profiles / "development.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SettingsError, match="unknown"):
        load_settings({}, project_root=tmp_path, profile_root=profiles)
