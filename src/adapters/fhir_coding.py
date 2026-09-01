"""FHIR Coding identity and event-level terminology provenance.

FHIR codes are identified by their system URI and code together.  This module
keeps that identity independent from OMOP vocabulary routing so an unsupported
local system can never be silently interpreted as SNOMED, LOINC, or RxNorm.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

SNOMED_URI = "http://snomed.info/sct"
LOINC_URI = "http://loinc.org"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
UCUM_URI = "http://unitsofmeasure.org"

SYSTEM_TO_ATHENA_VOCABULARY = {
    SNOMED_URI: "SNOMED",
    LOINC_URI: "LOINC",
    RXNORM_URI: "RxNorm",
    UCUM_URI: "UCUM",
}


def normalize_system_uri(value: str | None) -> str:
    """Normalize harmless URI variation without guessing a missing system."""
    return str(value or "").strip().rstrip("/")


@dataclass(frozen=True, slots=True)
class SourceCoding:
    """The lossless identity of one FHIR Coding selected for an event."""

    system_uri: str
    code: str
    display: str
    version: str | None = None

    def __post_init__(self) -> None:
        system_uri = normalize_system_uri(self.system_uri)
        code = str(self.code or "").strip()
        display = str(self.display or "").strip()
        version = str(self.version or "").strip() or None
        if not system_uri or not code:
            raise ValueError("FHIR SourceCoding requires both system and code")
        object.__setattr__(self, "system_uri", system_uri)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "display", display)
        object.__setattr__(self, "version", version)

    @property
    def athena_vocabulary_id(self) -> str | None:
        """Return an Athena vocabulary only for an explicitly recognized URI."""
        return SYSTEM_TO_ATHENA_VOCABULARY.get(self.system_uri)

    @property
    def source_vocabulary_id(self) -> str:
        """Return a stable, OMOP-sized source vocabulary identity."""
        if self.athena_vocabulary_id:
            return self.athena_vocabulary_id
        digest = hashlib.sha256(self.system_uri.encode("utf-8")).hexdigest()[:12]
        return f"FHIR_{digest}"

    @property
    def source_value(self) -> str:
        """Prefer the human-readable display without replacing code identity."""
        return self.display or self.code


def coding_from_mapping(value: Mapping | None) -> SourceCoding | None:
    """Parse one FHIR Coding; incomplete codings are not processable identities."""
    if not isinstance(value, Mapping):
        return None
    if not value.get("system") or not value.get("code"):
        return None
    return SourceCoding(
        system_uri=value["system"],
        code=value["code"],
        display=value.get("display") or "",
        version=value.get("version"),
    )


def select_source_coding(
    codings: Iterable[Mapping] | None,
    *,
    preferred_systems: Sequence[str] = (),
) -> SourceCoding | None:
    """Select deterministically while retaining unsupported local systems.

    Preferred systems are considered in caller-provided order.  If none match,
    the first complete Coding is returned with no inferred Athena vocabulary.
    """
    parsed = [
        coding
        for coding in (coding_from_mapping(value) for value in (codings or ()))
        if coding is not None
    ]
    if not parsed:
        return None
    for preferred in map(normalize_system_uri, preferred_systems):
        for coding in parsed:
            if coding.system_uri == preferred:
                return coding
    return parsed[0]


def iter_observation_elements(
    resource: Mapping,
) -> Iterator[tuple[str | None, Mapping, Mapping]]:
    """Yield the top-level Observation value and every component value.

    The component path is part of event identity; it prevents a panel's
    individual results from collapsing onto the parent Observation ID.
    """
    yield None, resource.get("code", {}), resource
    for index, component in enumerate(resource.get("component", ()) or ()):
        if isinstance(component, Mapping):
            yield f"component[{index}]", component.get("code", {}), component


def ensure_fhir_source_coding_table(con) -> None:
    """Create the non-OMOP sidecar that preserves event-level Coding identity."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS fhir_event_source_coding (
            target_table VARCHAR NOT NULL,
            target_id BIGINT NOT NULL,
            source_event_key VARCHAR NOT NULL,
            component_path VARCHAR,
            source_system_uri VARCHAR NOT NULL,
            source_vocabulary_id VARCHAR NOT NULL,
            source_code VARCHAR NOT NULL,
            source_display VARCHAR,
            source_version VARCHAR,
            source_adapter VARCHAR,
            run_id VARCHAR,
            recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (target_table, target_id)
        )
    """)
    columns = {
        row[0]
        for row in con.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'main'
              AND table_name = 'fhir_event_source_coding'
        """).fetchall()
    }
    if "source_adapter" not in columns:
        con.execute(
            "ALTER TABLE fhir_event_source_coding "
            "ADD COLUMN source_adapter VARCHAR"
        )


def replace_fhir_source_codings(
    con,
    target_table: str,
    rows: Iterable[tuple],
    *,
    source_adapter: str | None = None,
) -> None:
    """Replace sidecar rows without erasing other cross-domain adapters."""
    ensure_fhir_source_coding_table(con)
    if source_adapter is None:
        con.execute(
            "DELETE FROM fhir_event_source_coding WHERE target_table = ?",
            [target_table],
        )
    else:
        con.execute(
            "DELETE FROM fhir_event_source_coding "
            "WHERE target_table = ? "
            "AND (source_adapter = ? OR source_adapter IS NULL)",
            [target_table, source_adapter],
        )
    materialized = list(rows)
    if materialized:
        scoped = [(*row, source_adapter) for row in materialized]
        con.executemany("""
            INSERT INTO fhir_event_source_coding (
                target_table, target_id, source_event_key, component_path,
                source_system_uri, source_vocabulary_id, source_code,
                source_display, source_version, run_id, source_adapter
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, scoped)
