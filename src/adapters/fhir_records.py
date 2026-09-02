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


@dataclass(frozen=True, slots=True)
class FHIRMeasurementRecord:
    """Typed numeric or coded FHIR Observation candidate for MEASUREMENT."""

    event_id: int
    person_id: int
    coding: SourceCoding
    value_as_number: float | None
    unit: str | None
    unit_system: str | None
    unit_code: str | None
    canonical_unit_code: str | None
    event_date: str
    event_datetime: str
    source_event_key: str
    component_path: str | None = None
    value_coding: SourceCoding | None = None

    def as_staging_row(self) -> tuple:
        value = self.value_coding
        return (
            self.event_id,
            self.person_id,
            self.coding.code,
            self.coding.source_value,
            self.value_as_number,
            self.unit,
            self.unit_system,
            self.unit_code,
            self.canonical_unit_code,
            self.event_date,
            self.event_datetime,
            self.coding.system_uri,
            self.coding.athena_vocabulary_id,
            self.coding.source_vocabulary_id,
            self.coding.version,
            self.source_event_key,
            self.component_path,
            value.system_uri if value else None,
            value.athena_vocabulary_id if value else None,
            value.source_vocabulary_id if value else None,
            value.code if value else None,
            value.source_value if value else None,
            value.version if value else None,
        )


@dataclass(frozen=True, slots=True)
class FHIRObservationRecord:
    """Typed FHIR candidate routed to the OMOP OBSERVATION domain."""

    event_id: int
    person_id: int
    coding: SourceCoding
    event_date: str
    event_datetime: str
    source_event_key: str
    component_path: str | None = None
    value_as_number: float | None = None
    value_as_string: str | None = None
    unit: str | None = None
    unit_system: str | None = None
    unit_code: str | None = None
    canonical_unit_code: str | None = None
    value_coding: SourceCoding | None = None

    def as_staging_row(self) -> tuple:
        value = self.value_coding
        return (
            self.event_id,
            self.person_id,
            self.coding.code,
            self.coding.source_value,
            self.event_date,
            self.event_datetime,
            self.value_as_number,
            self.value_as_string,
            self.unit,
            self.unit_system,
            self.unit_code,
            self.canonical_unit_code,
            self.coding.system_uri,
            self.coding.athena_vocabulary_id,
            self.coding.source_vocabulary_id,
            self.coding.version,
            self.source_event_key,
            self.component_path,
            value.system_uri if value else None,
            value.athena_vocabulary_id if value else None,
            value.source_vocabulary_id if value else None,
            value.code if value else None,
            value.source_value if value else None,
            value.version if value else None,
        )


@dataclass(frozen=True, slots=True)
class FHIRPersonRecord:
    """Typed Patient demographics at the OMOP PERSON staging boundary."""

    person_id: int
    gender_concept_id: int
    year_of_birth: int
    month_of_birth: int | None
    day_of_birth: int | None
    birth_datetime: str | None
    race_concept_id: int
    ethnicity_concept_id: int
    person_source_value: str
    gender_source_value: str

    def as_staging_row(self) -> tuple:
        return (
            self.person_id,
            self.gender_concept_id,
            self.year_of_birth,
            self.month_of_birth,
            self.day_of_birth,
            self.birth_datetime,
            self.race_concept_id,
            self.ethnicity_concept_id,
            self.person_source_value,
            self.gender_source_value,
        )


@dataclass(frozen=True, slots=True)
class FHIRVisitRecord:
    """Typed Encounter at the OMOP VISIT_OCCURRENCE staging boundary."""

    visit_occurrence_id: int
    person_id: int
    visit_concept_id: int
    start_date: str
    start_datetime: str
    end_date: str
    end_datetime: str
    visit_type_concept_id: int
    provider_id: int | None
    care_site_id: int | None
    visit_source_value: str
    visit_source_concept_id: int

    def as_staging_row(self) -> tuple:
        return (
            self.visit_occurrence_id,
            self.person_id,
            self.visit_concept_id,
            self.start_date,
            self.start_datetime,
            self.end_date,
            self.end_datetime,
            self.visit_type_concept_id,
            self.provider_id,
            self.care_site_id,
            self.visit_source_value,
            self.visit_source_concept_id,
        )
