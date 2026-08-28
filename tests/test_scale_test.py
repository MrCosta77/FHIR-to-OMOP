from pathlib import Path

import pytest

from src.benchmark.run_scale_test import synthea_command


def test_scale_command_is_deterministic_and_uses_isolated_output(tmp_path):
    output = tmp_path / "isolated scale output"
    command = synthea_command(output, 250, 6062026)
    assert command[1:3] == ["/d", "/c"]
    assert Path(command[3]).name == "run_synthea.bat"
    assert command[4:10] == [
        "-s", "6062026", "-p", "250",
        f"--exporter.baseDirectory={output.resolve().as_posix()}",
        "--exporter.fhir.export=true",
    ]
    assert "--exporter.csv.export=false" in command


def test_scale_command_rejects_nonpositive_population(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        synthea_command(Path(tmp_path), 0, 1)


def test_fhir_directory_can_be_overridden_for_isolated_scale_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CMF_FHIR_DIR", str(tmp_path / "fhir"))
    from importlib import reload
    from src.utils import config

    try:
        assert Path(reload(config).FHIR_DIR) == tmp_path / "fhir"
    finally:
        monkeypatch.delenv("CMF_FHIR_DIR")
        reload(config)
