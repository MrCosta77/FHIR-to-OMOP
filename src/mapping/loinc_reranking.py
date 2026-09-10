"""Deterministic LOINC candidate reranking from explicit source-text axes."""

from __future__ import annotations

import re

LOINC_RERANKER_VERSION = "loinc-axes-v1"
LOINC_RETRIEVAL_POOL_SIZE = 20

_AXIS_PATTERNS = {
    "specimen": {
        "urine": (r"\burine\b",),
        "serum_or_plasma": (r"\bserum\b", r"\bplasma\b"),
        "arterial_blood": (r"\barterial blood\b",),
        "blood": (r"\bblood\b",),
        "respiratory": (r"\brespiratory\b", r"\bnasophary", r"\bsputum\b"),
    },
    "property": {
        "presence": (r"\bpresence\b", r"\bqualitative\b"),
        "mass_volume": (
            r"\bmass\s*/\s*volume\b", r"\b(?:mg|g|ug|µg)\s*/\s*(?:dl|l|ml)\b",
        ),
        "moles_volume": (
            r"\bmoles?\s*/\s*volume\b", r"\b(?:mmol|mol|umol|µmol)\s*/\s*l\b",
        ),
        "number_volume": (r"\bnumber\s*/\s*volume\b", r"\bcount\b"),
        "volume_fraction": (r"\bvolume fraction\b",),
        "enzymatic_activity": (r"\benzymatic activity\s*/\s*volume\b",),
    },
    "method": {
        "test_strip": (r"\btest strip\b",),
        "automated_count": (r"\bautomated count\b",),
        "nucleic_acid": (r"\bnaa\b", r"\bprobe detection\b", r"\brna\b", r"\bdna\b"),
        "rapid_immunoassay": (r"\brapid immunoassay\b",),
    },
}


def _normalize(value: str) -> str:
    value = value.casefold().replace("[", " ").replace("]", " ")
    value = re.sub(r"\(legacy\)", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_loinc_axes(value: str) -> dict[str, frozenset[str]]:
    """Extract only explicit LOINC-like axes; missing text remains unknown."""
    normalized = _normalize(value)
    result = {}
    for axis, values in _AXIS_PATTERNS.items():
        matches = {
            label
            for label, patterns in values.items()
            if any(re.search(pattern, normalized) for pattern in patterns)
        }
        if axis == "specimen" and "arterial_blood" in matches:
            matches.discard("blood")
        result[axis] = frozenset(matches)
    return result


def _distance_score(distance: float | None, metric: str) -> float:
    if distance is None:
        return 0.0
    if metric == "cosine":
        return max(0.0, min(1.0, 1.0 - float(distance)))
    return 1.0 / (1.0 + max(0.0, float(distance)))


def render_measurement_context(
    units: list[str] | tuple[str, ...], *, has_numeric: bool, has_coded: bool
) -> str:
    """Render non-identifying measurement context for retrieval and prompting."""
    clean_units = sorted({str(unit).strip() for unit in units if str(unit).strip()})
    value_kinds = []
    if has_numeric:
        value_kinds.append("numeric")
    if has_coded:
        value_kinds.append("coded-or-text")
    parts = []
    if clean_units:
        parts.append("Units: " + ", ".join(clean_units))
    if value_kinds:
        parts.append("Observed value kind: " + ", ".join(value_kinds))
    return "; ".join(parts)


def rerank_loinc_search(
    source_value: str, search: dict, *, metric: str = "cosine", top_k: int = 5
) -> tuple[dict, list[dict]]:
    """Rerank a Chroma result without changing its original similarity values."""
    ids = search.get("ids", [[]])[0]
    documents = search.get("documents", [[]])[0]
    distances = search.get("distances", [[]])[0]
    source_axes = extract_loinc_axes(source_value)
    ranked = []
    for index, concept_id in enumerate(ids):
        name = documents[index]
        distance = float(distances[index]) if index < len(distances) else None
        candidate_axes = extract_loinc_axes(name)
        score = _distance_score(distance, metric)
        signals = []
        for axis, source_values in source_axes.items():
            if not source_values:
                continue
            candidate_values = candidate_axes[axis]
            if source_values & candidate_values:
                score += 0.08
                signals.append(f"{axis}:match")
            elif candidate_values:
                score -= 0.35
                signals.append(f"{axis}:conflict")
        ranked.append({
            "original_rank": index + 1,
            "concept_id": str(concept_id),
            "concept_name": name,
            "distance": distance,
            "rerank_score": score,
            "signals": tuple(signals),
        })
    ranked.sort(
        key=lambda item: (-item["rerank_score"], item["original_rank"])
    )
    selected = ranked[:top_k]
    reranked = {
        "ids": [[item["concept_id"] for item in selected]],
        "documents": [[item["concept_name"] for item in selected]],
        "distances": [[item["distance"] for item in selected]],
    }
    return reranked, selected


def retrieve_mapping_candidates(
    collection,
    source_value: str,
    target_table: str,
    *,
    top_k: int = 5,
    rerank_context: str = "",
) -> tuple[dict, list[dict]]:
    """Retrieve candidates and apply LOINC reranking only to Measurement."""
    pool_size = LOINC_RETRIEVAL_POOL_SIZE if target_table == "measurement" else top_k
    search = collection.query(query_texts=[source_value], n_results=pool_size)
    if target_table != "measurement":
        return search, []
    rerank_input = source_value
    if rerank_context:
        rerank_input = f"{rerank_input}\n{rerank_context}"
    return rerank_loinc_search(
        rerank_input,
        search,
        metric=(collection.metadata or {}).get("distance_metric", "cosine"),
        top_k=top_k,
    )
