from src.quality import preflight


def test_preflight_reports_all_missing_file_inputs(tmp_path):
    failures = preflight.collect_failures(
        fhir_dir=tmp_path / "missing-fhir",
        vocab_dir=tmp_path / "missing-vocab",
        require_ollama=False,
    )
    assert len(failures) == 2
    assert "FHIR directory is missing" in failures[0]
    assert all(name in failures[1] for name in preflight.ATHENA_FILES)


def test_preflight_accepts_complete_local_file_contract(tmp_path):
    fhir = tmp_path / "fhir"
    vocab = tmp_path / "vocab"
    fhir.mkdir()
    vocab.mkdir()
    (fhir / "bundle.json").write_text("{}", encoding="utf-8")
    for name in preflight.ATHENA_FILES:
        (vocab / name).write_text("fixture", encoding="utf-8")

    assert preflight.collect_failures(
        fhir_dir=fhir,
        vocab_dir=vocab,
        require_ollama=False,
    ) == []


def test_rscript_can_be_discovered_outside_path(tmp_path, monkeypatch):
    executable = tmp_path / "Rscript.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    assert preflight.find_rscript([executable]) == executable
