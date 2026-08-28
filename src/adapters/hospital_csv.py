"""Fail-closed adapter for versioned, row-oriented hospital CSV input."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from src.clinical_mapping_core import Candidate, MappingRequest
from src.omop.mapping_targets import TARGETS
from src.security.privacy import redact_direct_identifiers


SCHEMA_VERSION = "hospital-csv-v1"
REQUIRED_COLUMNS = {"schema_version", "record_id", "domain", "source_value"}
CONTEXT_COLUMNS = (
    "source_system",
    "source_code",
    "unit",
    "specimen",
    "route",
    "dose",
    "event_date",
)
ALLOWED_COLUMNS = REQUIRED_COLUMNS | set(CONTEXT_COLUMNS)
RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
MAX_SOURCE_LENGTH = 500
MAX_CONTEXT_LENGTH = 200

DOMAIN_ALIASES = {
    "condition": "condition_occurrence",
    "condition_occurrence": "condition_occurrence",
    "drug": "drug_exposure",
    "drug_exposure": "drug_exposure",
    "medication": "drug_exposure",
    "measurement": "measurement",
    "lab": "measurement",
    "laboratory": "measurement",
    "observation": "observation",
    "procedure": "procedure_occurrence",
    "procedure_occurrence": "procedure_occurrence",
    "device": "device_exposure",
    "device_exposure": "device_exposure",
}


class HospitalCSVError(ValueError):
    """Raised when hospital CSV input violates the versioned contract."""


@dataclass(frozen=True, slots=True)
class PreparedMappingRequest:
    """A redacted core request plus non-prompt row routing metadata."""

    record_id: str
    target_table: str
    request: MappingRequest
    redaction_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HospitalCSVRecord:
    """One validated CSV row before terminology retrieval."""

    record_id: str
    target_table: str
    target_domain: str
    target_vocabulary: str
    source_value: str
    context: tuple[tuple[str, str], ...]

    def prepare_mapping_request(
        self,
        candidates: Iterable[Candidate],
    ) -> PreparedMappingRequest:
        """Redact prompt-bound values and bind them to retrieved candidates."""
        source_value, categories = redact_direct_identifiers(self.source_value)
        redacted_context = []
        all_categories = list(categories)
        for key, value in self.context:
            redacted, field_categories = redact_direct_identifiers(value)
            redacted_context.append((key, redacted))
            all_categories.extend(field_categories)
        request = MappingRequest(
            source_value=source_value,
            target_domain=self.target_domain,
            target_vocabulary=self.target_vocabulary,
            candidates=tuple(candidates),
            context=tuple(redacted_context),
        )
        return PreparedMappingRequest(
            record_id=self.record_id,
            target_table=self.target_table,
            request=request,
            redaction_categories=tuple(sorted(set(all_categories))),
        )


def _clean_value(value: str | None, *, column: str, row_number: int) -> str:
    cleaned = (value or "").strip()
    if "\x00" in cleaned:
        raise HospitalCSVError(f"Row {row_number} column {column} contains a NUL byte")
    return cleaned


def _validate_length(value: str, maximum: int, *, column: str, row_number: int) -> None:
    if len(value) > maximum:
        raise HospitalCSVError(
            f"Row {row_number} column {column} exceeds {maximum} characters"
        )


def load_hospital_csv(path: str | Path) -> tuple[HospitalCSVRecord, ...]:
    """Load UTF-8 CSV/semicolon/TSV rows under the hospital-csv-v1 contract."""
    path = Path(path)
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise HospitalCSVError(f"Cannot open hospital CSV: {path}") from exc

    records = []
    seen_record_ids = set()
    with handle:
        sample = handle.read(4096)
        if not sample.strip():
            raise HospitalCSVError("Hospital CSV is empty")
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error as exc:
            raise HospitalCSVError("Hospital CSV delimiter could not be determined") from exc
        handle.seek(0)
        reader = csv.DictReader(handle, dialect=dialect)
        original_fields = reader.fieldnames or []
        fields = [field.strip() for field in original_fields]
        if len(fields) != len(set(fields)):
            raise HospitalCSVError("Hospital CSV contains duplicate column names")
        missing = sorted(REQUIRED_COLUMNS - set(fields))
        unknown = sorted(set(fields) - ALLOWED_COLUMNS)
        if missing:
            raise HospitalCSVError("Hospital CSV is missing columns: " + ", ".join(missing))
        if unknown:
            raise HospitalCSVError(
                "Hospital CSV contains non-allowlisted columns: " + ", ".join(unknown)
            )
        reader.fieldnames = fields

        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise HospitalCSVError(f"Row {row_number} contains extra fields")
            values = {
                column: _clean_value(row.get(column), column=column, row_number=row_number)
                for column in fields
            }
            if values["schema_version"] != SCHEMA_VERSION:
                raise HospitalCSVError(
                    f"Row {row_number} has unsupported schema_version: "
                    f"{values['schema_version'] or '<empty>'}"
                )
            record_id = values["record_id"]
            if not RECORD_ID_PATTERN.fullmatch(record_id):
                raise HospitalCSVError(f"Row {row_number} has invalid record_id")
            if record_id in seen_record_ids:
                raise HospitalCSVError(f"Duplicate record_id: {record_id}")
            seen_record_ids.add(record_id)

            domain_key = values["domain"].casefold().replace(" ", "_")
            target_table = DOMAIN_ALIASES.get(domain_key)
            if target_table is None:
                raise HospitalCSVError(
                    f"Row {row_number} has unsupported domain: {values['domain'] or '<empty>'}"
                )
            source_value = values["source_value"]
            if not source_value:
                raise HospitalCSVError(f"Row {row_number} has empty source_value")
            _validate_length(
                source_value,
                MAX_SOURCE_LENGTH,
                column="source_value",
                row_number=row_number,
            )

            event_date = values.get("event_date", "")
            if event_date:
                try:
                    date.fromisoformat(event_date)
                except ValueError as exc:
                    raise HospitalCSVError(
                        f"Row {row_number} event_date must be ISO YYYY-MM-DD"
                    ) from exc
            context = []
            for column in CONTEXT_COLUMNS:
                value = values.get(column, "")
                if value:
                    _validate_length(
                        value,
                        MAX_CONTEXT_LENGTH,
                        column=column,
                        row_number=row_number,
                    )
                    context.append((column, value))
            target = TARGETS[target_table]
            records.append(HospitalCSVRecord(
                record_id=record_id,
                target_table=target_table,
                target_domain=target["domain"],
                target_vocabulary=target["vocabulary"],
                source_value=source_value,
                context=tuple(context),
            ))
    if not records:
        raise HospitalCSVError("Hospital CSV contains no data rows")
    return tuple(records)


def summarize_hospital_csv(
    records: Iterable[HospitalCSVRecord],
) -> dict:
    """Return metadata-only validation counts without source or context values."""
    records = tuple(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_count": len(records),
        "by_target_table": dict(sorted(Counter(
            record.target_table for record in records
        ).items())),
        "context_fields_present": sorted({
            key for record in records for key, _value in record.context
        }),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate hospital-csv-v1 and print metadata-only counts."
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        records = load_hospital_csv(args.path)
    except HospitalCSVError as exc:
        parser.error(str(exc))
    print(json.dumps(summarize_hospital_csv(records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
