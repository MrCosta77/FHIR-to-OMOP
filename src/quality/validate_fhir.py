"""Structural acceptance checks for FHIR R4 transaction/batch bundles."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.helpers import normalise_fhir_reference  # noqa: E402

CLINICAL_TYPES = {
    "Condition", "Encounter", "MedicationRequest", "Observation", "Procedure"
}
IDENTITY_SCOPED_TYPES = CLINICAL_TYPES | {"Patient"}
AUXILIARY_BUNDLE_PREFIXES = ("hospitalInformation", "practitionerInformation")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_absolute_fhir_uri(value: object) -> bool:
    parsed = urlsplit(str(value or "").strip())
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme.casefold() == "urn"))


def validate_bundle(path: Path, *, require_patient: bool = True) -> Counter:
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(bundle.get("resourceType") == "Bundle", f"{path}: resourceType must be Bundle")
    entries = bundle.get("entry")
    _require(isinstance(entries, list) and entries, f"{path}: Bundle.entry is empty")

    resources = [entry.get("resource", {}) for entry in entries]
    full_urls: set[str] = set()
    for entry, resource in zip(entries, resources):
        resource_type = resource.get("resourceType")
        if resource_type not in IDENTITY_SCOPED_TYPES:
            continue
        full_url = str(entry.get("fullUrl", "")).strip()
        _require(
            _is_absolute_fhir_uri(full_url),
            f"{path}: {resource_type} requires an absolute fullUrl namespace",
        )
        _require(full_url not in full_urls, f"{path}: duplicate fullUrl {full_url}")
        full_urls.add(full_url)
    patients = {resource.get("id") for resource in resources if resource.get("resourceType") == "Patient"}
    patients.discard(None)
    if require_patient:
        _require(bool(patients), f"{path}: bundle has no Patient")
    encounters = {
        resource.get("id") for resource in resources
        if resource.get("resourceType") == "Encounter"
    }
    encounters.discard(None)

    identities: set[tuple[str, str]] = set()
    counts: Counter = Counter()
    for resource in resources:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        _require(resource_type and resource_id, f"{path}: every resource requires resourceType and id")
        identity = (resource_type, resource_id)
        _require(identity not in identities, f"{path}: duplicate resource {resource_type}/{resource_id}")
        identities.add(identity)
        counts[resource_type] += 1

        if resource_type in CLINICAL_TYPES:
            reference = resource.get("subject", {}).get("reference", "")
            subject_id = reference.rsplit("/", 1)[-1].replace("urn:uuid:", "")
            _require(subject_id in patients, f"{path}: unresolved subject reference {reference!r}")

        encounter_reference = resource.get("encounter", {}).get("reference")
        if encounter_reference:
            encounter_id = normalise_fhir_reference(encounter_reference)
            _require(
                encounter_id in encounters,
                f"{path}: unresolved Encounter reference {encounter_reference!r}",
            )

        if resource_type in {"Condition", "Observation", "Procedure"}:
            codings = resource.get("code", {}).get("coding", [])
            _require(any(c.get("code") and c.get("system") for c in codings), f"{path}: {resource_type}/{resource_id} has no coded concept")

        if resource_type == "Observation" and "valueQuantity" in resource:
            quantity = resource["valueQuantity"]
            _require(isinstance(quantity.get("value"), (int, float)), f"{path}: numeric Observation has no numeric value")
            _require(bool(quantity.get("unit") or quantity.get("code")), f"{path}: numeric Observation has no source unit")

    return counts


def validate_directory(directory: Path) -> Counter:
    paths = sorted(Path(directory).glob("*.json"))
    _require(bool(paths), f"No FHIR JSON bundles found in {directory}")
    total: Counter = Counter()
    for path in paths:
        is_auxiliary = path.name.startswith(AUXILIARY_BUNDLE_PREFIXES)
        total.update(validate_bundle(path, require_patient=not is_auxiliary))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="FHIR JSON bundle or directory")
    args = parser.parse_args()
    counts = validate_directory(args.path) if args.path.is_dir() else validate_bundle(args.path)
    print("FHIR input acceptance passed.")
    for resource_type, count in sorted(counts.items()):
        print(f" - {resource_type}: {count}")


if __name__ == "__main__":
    main()
