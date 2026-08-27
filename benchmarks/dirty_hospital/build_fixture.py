"""Build the deterministic dirty-hospital benchmark fixture and manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
CURATION_STATUS = "PROVISIONAL_TECHNICAL"
RELEASE_DATE = "2026-08-26"
SYSTEMS = {
    "SNOMED": "http://snomed.info/sct",
    "LOINC": "http://loinc.org",
    "RxNorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
}

# The concepts below are Standard, valid concepts verified against the official
# Athena vocabulary loaded by the project. Clinical review is intentionally a
# separate gate; the fixture must not be described as clinically validated yet.
FAMILIES = [
    # Condition (3 development, 2 held-out)
    ("condition_hypertension", "development", "Condition", "SNOMED", "59621000", 320128, "Essential hypertension", "HTA essencial confirmada", "DX-HTA", "hipertensão arterial essencial", "pressão alta? sem diagnóstico confirmado", {}),
    ("condition_asthma", "development", "Condition", "SNOMED", "195967001", 317009, "Asthma", "asma brônquica ativa", "DX-ASMA", "antecedentes de asma", "pieira ocasional, causa por esclarecer", {}),
    ("condition_diabetes2", "development", "Condition", "SNOMED", "44054006", 201826, "Type 2 diabetes mellitus", "DM tipo 2", "DX-DM2", "diabetes mellitus tipo II", "açúcar alto numa medição isolada", {}),
    ("condition_mi", "held_out", "Condition", "SNOMED", "22298006", 4329847, "Myocardial infarction", "enfarte agudo do miocárdio", "DX-EAM", "infarto do miocárdio", "dor torácica; excluir enfarte", {}),
    ("condition_appendicitis", "held_out", "Condition", "SNOMED", "74400008", 440448, "Appendicitis", "apendicite confirmada", "DX-APEND", "inflamação do apêndice", "dor abdominal a esclarecer", {}),
    # Measurement
    ("measurement_heart_rate", "development", "Measurement", "LOINC", "8867-4", 3027018, "Heart rate", "FC 78 bpm", "VIT-FC", "frequência cardíaca 78 bat/min", "sinais vitais alterados", {"value": 78, "unit_text": "bpm", "unit_code": "/min"}),
    ("measurement_sbp", "development", "Measurement", "LOINC", "8480-6", 3004249, "Systolic blood pressure", "TA sistólica 128 mmHg", "VIT-TAS", "pressão arterial sistólica 128", "pressão 128/sem segundo valor", {"value": 128, "unit_text": "mmHg", "unit_code": "mm[Hg]"}),
    ("measurement_dbp", "development", "Measurement", "LOINC", "8462-4", 3012888, "Diastolic blood pressure", "TA diastólica 76 mmHg", "VIT-TAD", "pressão arterial diastólica 76", "pressão 76 isolada, componente desconhecido", {"value": 76, "unit_text": "mmHg", "unit_code": "mm[Hg]"}),
    ("measurement_glucose", "held_out", "Measurement", "LOINC", "2345-7", 3004501, "Glucose [Mass/volume] in Serum or Plasma", "glicemia sérica 104 mg/dL", "LAB-GLU-S", "glucose no soro/plasma 104", "glicose 104, espécime e método ausentes", {"value": 104, "unit_text": "mg/dL", "unit_code": "mg/dL", "specimen": "Serum or plasma"}),
    ("measurement_hemoglobin", "held_out", "Measurement", "LOINC", "718-7", 3000963, "Hemoglobin [Mass/volume] in Blood", "Hb sangue 13,6 g/dL", "LAB-HGB", "hemoglobina no sangue 13.6", "Hb registada sem espécime nem unidade", {"value": 13.6, "unit_text": "g/dL", "unit_code": "g/dL", "specimen": "Blood"}),
    # Procedure
    ("procedure_colonoscopy", "development", "Procedure", "SNOMED", "73761001", 4249893, "Colonoscopy", "colonoscopia realizada", "PROC-COLON", "endoscopia do cólon completa", "exame intestinal por especificar", {"status": "completed"}),
    ("procedure_appendectomy", "development", "Procedure", "SNOMED", "80146002", 4198190, "Appendectomy", "apendicectomia efetuada", "PROC-APX", "remoção cirúrgica do apêndice", "cirurgia abdominal sem procedimento descrito", {"status": "completed"}),
    ("procedure_cholecystectomy", "development", "Procedure", "SNOMED", "38102005", 4242997, "Cholecystectomy", "colecistectomia realizada", "PROC-COLE", "remoção cirúrgica da vesícula", "cirurgia biliar planeada, técnica incerta", {"status": "completed"}),
    ("procedure_liver_transplant", "held_out", "Procedure", "SNOMED", "18027006", 4076862, "Transplantation of liver", "transplante hepático realizado", "PROC-TH", "transplantação do fígado", "avaliação para possível transplante", {"status": "completed"}),
    ("procedure_breech_delivery", "held_out", "Procedure", "SNOMED", "177157003", 4073422, "Spontaneous breech delivery", "parto pélvico espontâneo", "PROC-PPE", "parto espontâneo em apresentação pélvica", "apresentação pélvica; via do parto desconhecida", {"status": "completed"}),
    # Observation
    ("observation_smoking", "development", "Observation", "LOINC", "72166-2", 43054909, "Tobacco smoking status", "estado tabágico: ex-fumador", "OBS-TAB", "situação perante o tabaco", "hábitos registados sem indicar quais", {"value_text": "Former smoker"}),
    ("observation_pregnancy", "development", "Observation", "LOINC", "82810-3", 42528957, "Pregnancy status", "estado de gravidez: não grávida", "OBS-GRAV", "situação atual de gravidez", "história obstétrica sem estado atual", {"value_text": "Not pregnant"}),
    ("observation_tobacco_history", "development", "Observation", "LOINC", "11367-0", 3012697, "History of Tobacco use", "história de consumo de tabaco", "OBS-HIST-TAB", "antecedentes de uso de tabaco", "nota social incompleta", {"value_text": "Past use documented"}),
    ("observation_orientation", "held_out", "Observation", "LOINC", "76690-7", 46235214, "Sexual orientation", "orientação sexual registada", "OBS-ORIENT", "registo de orientação sexual", "informação demográfica sensível não especificada", {"value_text": "Patient-provided"}),
    ("observation_transport", "held_out", "Observation", "LOINC", "93030-5", 37020730, "Has lack of transportation kept you from medical appointments, meetings, work, or from getting things needed for daily living", "barreira de transporte: sim", "SDOH-TRANS", "falta de transporte impediu consultas ou tarefas essenciais", "dificuldade social não caracterizada", {"value_text": "Yes"}),
    # Drug
    ("drug_lisinopril", "development", "Drug", "RxNorm", "314076", 19080128, "lisinopril 10 MG Oral Tablet", "lisinopril 10 mg comprimido oral", "MED-LIS10", "lisinopril comp 10mg PO", "lisinopril sem dose nem apresentação", {"dose": "10 mg", "route": "oral", "dose_form": "tablet"}),
    ("drug_metformin", "development", "Drug", "RxNorm", "860975", 40163924, "24 HR metformin hydrochloride 500 MG Extended Release Oral Tablet", "metformina XR 500 mg 24h comprimido", "MED-METXR500", "metformin 500mg libertação prolongada oral", "metformina sem dose nem formulação", {"dose": "500 mg", "route": "oral", "dose_form": "extended-release tablet"}),
    ("drug_amlodipine", "development", "Drug", "RxNorm", "197361", 1332419, "amlodipine 5 MG Oral Tablet", "amlodipina 5 mg comprimido oral", "MED-AML5", "amlodipine tab 5mg PO", "amlodipina sem dose nem apresentação", {"dose": "5 mg", "route": "oral", "dose_form": "tablet"}),
    ("drug_atorvastatin", "held_out", "Drug", "RxNorm", "617314", 1545998, "atorvastatin 10 MG Oral Tablet [Lipitor]", "Lipitor 10 mg comprimido oral", "MED-LIP10", "atorvastatina 10mg oral marca Lipitor", "Lipitor sem dose nem apresentação", {"dose": "10 mg", "route": "oral", "dose_form": "tablet", "brand": "Lipitor"}),
    ("drug_prednisone", "held_out", "Drug", "RxNorm", "312615", 1551170, "prednisone 20 MG Oral Tablet", "prednisona 20 mg comprimido oral", "MED-PRED20", "prednisone tab 20mg PO", "prednisona sem dose nem apresentação", {"dose": "20 mg", "route": "oral", "dose_form": "tablet"}),
]


def _case(family: tuple, variant: str, index: int) -> dict:
    (family_id, split, domain, vocabulary, code, concept_id, concept_name,
     dirty_text, local_code, local_text, abstain_text, context) = family
    if variant == "coded_exact":
        source = {"format": "FHIR", "system": SYSTEMS[vocabulary], "code": code, "text": concept_name}
        decision, expected_id, rationale = "MAP", concept_id, "Exact valid standard vocabulary code."
        tags = ["coded", "clean"]
    elif variant == "dirty_text":
        source = {"format": "TEXT", "system": None, "code": None, "text": dirty_text}
        decision, expected_id, rationale = "MAP", concept_id, "Concept is explicit despite abbreviation, language, or formatting noise."
        tags = ["uncoded", "dirty_text", "pt"]
    elif variant == "local_code":
        source = {"format": "CSV", "system": "urn:hospital:local", "code": local_code, "text": local_text}
        decision, expected_id, rationale = "MAP", concept_id, "Local code is accompanied by concept-specific clinical text."
        tags = ["local_code", "dirty_text", "mixed_language"]
    else:
        source = {"format": "TEXT", "system": None, "code": None, "text": abstain_text}
        decision, expected_id, rationale = "ABSTAIN", None, "Evidence is insufficient, ambiguous, uncertain, or less specific than the target concept."
        tags = ["uncoded", "ambiguous", "abstain_required"]
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": f"DH-{index:03d}",
        "family_id": family_id,
        "split": split,
        "domain": domain,
        "variant": variant,
        "source": source,
        "context": context,
        "expected": {
            "decision": decision,
            "concept_id": expected_id,
            "concept_name": concept_name if expected_id else None,
            "domain": domain if expected_id else None,
        },
        "difficulty_tags": tags,
        "curation": {
            "status": CURATION_STATUS,
            "reviewer": "project-technical-curation",
            "rationale": rationale,
        },
    }


def build_cases() -> list[dict]:
    variants = ("coded_exact", "dirty_text", "local_code", "ambiguous")
    return [_case(family, variant, index)
            for index, (family, variant) in enumerate(
                ((family, variant) for family in FAMILIES for variant in variants), start=1)]


def write_fixture(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    fixture_path = output_dir / "cases.jsonl"
    payload = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases)
    fixture_path.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    manifest = {
        "benchmark": "dirty-hospital-to-omop",
        "schema_version": SCHEMA_VERSION,
        "release_date": RELEASE_DATE,
        "curation_status": CURATION_STATUS,
        "clinical_validation_required": True,
        "case_count": len(cases),
        "split_counts": {split: sum(c["split"] == split for c in cases) for split in ("development", "held_out")},
        "domain_counts": {domain: sum(c["domain"] == domain for c in cases) for domain in ("Condition", "Measurement", "Procedure", "Observation", "Drug")},
        "decision_counts": {decision: sum(c["expected"]["decision"] == decision for c in cases) for decision in ("MAP", "ABSTAIN")},
        "fixture": fixture_path.name,
        "fixture_sha256": digest,
        "leakage_policy": "No family_id or expected concept_id may occur in both splits.",
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return fixture_path, manifest_path


if __name__ == "__main__":
    fixture, manifest = write_fixture(Path(__file__).resolve().parent)
    print(f"Wrote {fixture} and {manifest}")
