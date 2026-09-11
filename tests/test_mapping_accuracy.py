import duckdb

from src.analytics.evaluate_mapping_accuracy import mapping_accuracy_metrics


def test_quarantined_measurements_remain_in_accuracy_denominator():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT, true_concept_id INTEGER
            );
            CREATE TABLE measurement (
                measurement_id BIGINT, measurement_concept_id INTEGER
            );
            INSERT INTO lis_noise_ground_truth VALUES (1, 100), (2, 200);
            INSERT INTO measurement VALUES (1, 100);
        """)

        metrics = mapping_accuracy_metrics(con)

    assert metrics == {
        "total_corrupted": 2,
        "total_mapped": 1,
        "correct_matches": 1,
        "coverage": 50.0,
        "precision": 100.0,
        "recall": 50.0,
    }
