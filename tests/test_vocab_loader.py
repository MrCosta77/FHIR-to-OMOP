import duckdb
from datetime import date

from src.utils.setup_vocab import _load_next_table


def test_vocab_loader_applies_official_types(tmp_path):
    source = tmp_path / "CONCEPT.csv"
    source.write_text(
        "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\t"
        "standard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason\n"
        "123\tFixture concept\tCondition\tSNOMED\tClinical Finding\tS\tABC\t"
        "20000101\t20991231\t\n",
        encoding="utf-8",
    )
    with duckdb.connect(":memory:") as con:
        assert _load_next_table(con, "concept", source) == 1
        row = con.execute(
            "SELECT concept_id, valid_start_date, invalid_reason FROM next_concept"
        ).fetchone()
        types = {
            name: datatype
            for _, name, datatype, *_ in con.execute(
                "PRAGMA table_info('next_concept')"
            ).fetchall()
        }
    assert row == (123, date(2000, 1, 1), None)
    assert types["concept_id"] == "BIGINT"
    assert types["valid_start_date"] == "DATE"
