import pytest

from src.utils.assets import PACKAGE_DATA_ROOT, PROJECT_ROOT, runtime_asset

RUNTIME_ASSETS = (
    ("config", "privacy_policy.json"),
    ("config", "profiles", "development.json"),
    ("config", "profiles", "benchmark.json"),
    ("config", "profiles", "hospital.json"),
    ("resources", "omop_cdm_v5_4", "OMOP_CDMv5.4_Field_Level.csv"),
    ("benchmarks", "dirty_hospital", "cases.jsonl"),
    ("benchmarks", "dirty_hospital", "phase5_protocol.json"),
    ("benchmarks", "dirty_hospital", "phase6_development_protocol.json"),
    ("quality", "dqd_policy.json"),
)


@pytest.mark.parametrize("parts", RUNTIME_ASSETS)
def test_packaged_runtime_asset_matches_checkout_source(parts):
    checkout = PROJECT_ROOT.joinpath(*parts)
    packaged = PACKAGE_DATA_ROOT.joinpath(*parts)

    assert checkout.read_bytes() == packaged.read_bytes()
    assert runtime_asset(*parts) == checkout


def test_runtime_asset_fails_closed_for_unknown_asset():
    with pytest.raises(FileNotFoundError, match="Required runtime asset"):
        runtime_asset("does-not-exist.json")
