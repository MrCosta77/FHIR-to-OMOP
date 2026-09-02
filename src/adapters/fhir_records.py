"""Typed records crossing the FHIR extraction-to-SQL staging boundary."""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.fhir_coding import SourceCoding


@dataclass(frozen=True, slots=True)
class CodedFHIRPeriodRecord:
    """One coded FHIR event with an OMOP-compatible temporal interval."""

    event_id: int
    person_id: int
    coding: SourceCoding
    start_date: str
    start_datetime: str
    end_date: str
    end_datetime: str
    source_event_key: str

    def as_staging_row(self) -> tuple:
        """Flatten the typed record only at the positional SQL boundary."""
        return (
            self.event_id,
            self.person_id,
            self.coding.code,
            self.coding.source_value,
            self.start_date,
            self.start_datetime,
            self.end_date,
            self.end_datetime,
            self.coding.system_uri,
            self.coding.athena_vocabulary_id,
            self.coding.source_vocabulary_id,
            self.coding.version,
            self.source_event_key,
        )
