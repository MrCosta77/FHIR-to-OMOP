from src.mapping.loinc_reranking import (
    LOINC_RERANKER_VERSION,
    extract_loinc_axes,
    render_measurement_context,
    rerank_loinc_search,
    retrieve_mapping_candidates,
)


def test_explicit_loinc_axes_are_extracted_without_imputing_missing_specimen():
    axes = extract_loinc_axes(
        "Glucose [Mass/volume] in Urine by Test strip (legacy)"
    )
    assert axes["specimen"] == {"urine"}
    assert axes["property"] == {"mass_volume"}
    assert axes["method"] == {"test_strip"}
    assert extract_loinc_axes("Chloride [Moles/volume]")["specimen"] == set()
    assert extract_loinc_axes("Units: mg/dL")["property"] == {"mass_volume"}


def test_measurement_context_is_deterministic_and_contains_no_values():
    context = render_measurement_context(
        ["mg/dL", "mg/dL"], has_numeric=True, has_coded=False
    )
    assert context == "Units: mg/dL; Observed value kind: numeric"


def test_reranker_demotes_presence_candidate_for_quantitative_urine_source():
    search = {
        "ids": [["presence", "quantitative"]],
        "documents": [[
            "Glucose [Presence] in Urine by Test strip",
            "Glucose [Mass/volume] in Urine by Test strip",
        ]],
        "distances": [[0.05, 0.12]],
    }

    reranked, diagnostics = rerank_loinc_search(
        "Glucose [Mass/volume] in Urine by Test strip", search
    )

    assert LOINC_RERANKER_VERSION == "loinc-axes-v1"
    assert reranked["ids"][0] == ["quantitative", "presence"]
    assert "property:conflict" in diagnostics[1]["signals"]
    assert reranked["distances"][0] == [0.12, 0.05]


def test_reranker_preserves_embedding_order_when_source_axis_is_unknown():
    search = {
        "ids": [["generic", "blood"]],
        "documents": [[
            "Chloride [Moles/volume] in Specimen",
            "Chloride [Moles/volume] in Blood",
        ]],
        "distances": [[0.05, 0.10]],
    }

    reranked, _ = rerank_loinc_search("Chloride [Moles/volume]", search)

    assert reranked["ids"][0] == ["generic", "blood"]


def test_context_reranks_but_does_not_change_embedding_query():
    class Collection:
        metadata = {"distance_metric": "cosine"}

        def query(self, query_texts, n_results):
            assert query_texts == ["GLUCOSE IN URINE BY TEST STRIP"]
            assert n_results == 20
            return {
                "ids": [["presence", "quantitative"]],
                "documents": [[
                    "Glucose [Presence] in Urine by Test strip",
                    "Glucose [Mass/volume] in Urine by Test strip",
                ]],
                "distances": [[0.05, 0.12]],
            }

    search, _ = retrieve_mapping_candidates(
        Collection(),
        "GLUCOSE IN URINE BY TEST STRIP",
        "measurement",
        rerank_context="Units: mg/dL; Observed value kind: numeric",
    )

    assert search["ids"][0] == ["quantitative", "presence"]
