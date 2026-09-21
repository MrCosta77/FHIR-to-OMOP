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

from src.adapters.fhir_coding import select_source_coding_or_text  # noqa: E402
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


def _fhir_source_namespace(full_url: str, resource_type: str) -> str:
    """Return the source namespace that scopes a FHIR logical resource ID."""
    value = str(full_url).strip()
    parsed = urlsplit(value)
    if parsed.scheme.casefold() == "urn":
        # ``urn:uuid:<id>`` and similar URNs share the namespace before the
        # final identifier. The complete fullUrl remains independently unique.
        return value.rsplit(":", 1)[0].casefold()

    marker = f"/{resource_type}/"
    path = parsed.path.rstrip("/")
    if marker.casefold() in path.casefold():
        marker_index = path.casefold().rfind(marker.casefold())
        base_path = path[:marker_index]
    else:
        base_path = path.rsplit("/", 1)[0] if "/" in path else ""
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{base_path}"


def validate_bundle(
    path: Path,
    *,
    require_patient: bool = True,
    seen_identities: set[tuple[str, str, str]] | None = None,
    seen_full_urls: set[str] | None = None,
) -> Counter:
    """Validate one bundle, optionally against directory-wide identity state."""
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(bundle.get("resourceType") == "Bundle", f"{path}: resourceType must be Bundle")
    entries = bundle.get("entry")
    _require(isinstance(entries, list) and entries, f"{path}: Bundle.entry is empty")

    resources = [entry.get("resource", {}) for entry in entries]
    full_urls = seen_full_urls if seen_full_urls is not None else set()
    resource_namespaces: dict[int, str] = {}
    for entry, resource in zip(entries, resources, strict=False):
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
        resource_namespaces[id(resource)] = _fhir_source_namespace(
            full_url, resource_type
        )
    patients = {resource.get("id") for resource in resources if resource.get("resourceType") == "Patient"}
    patients.discard(None)
    if require_patient:
        _require(bool(patients), f"{path}: bundle has no Patient")
    encounters = {
        resource.get("id") for resource in resources
        if resource.get("resourceType") == "Encounter"
    }
    encounters.discard(None)

    identities = seen_identities if seen_identities is not None else set()
    counts: Counter = Counter()
    for resource in resources:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        _require(resource_type and resource_id, f"{path}: every resource requires resourceType and id")
        namespace = resource_namespaces.get(id(resource), "")
        identity = (namespace, resource_type, resource_id)
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
            source_coding = select_source_coding_or_text(resource.get("code"))
            _require(
                source_coding is not None,
                f"{path}: {resource_type}/{resource_id} has no usable concept",
            )

        if resource_type == "Observation" and "valueQuantity" in resource:
            quantity = resource["valueQuantity"]
            _require(isinstance(quantity.get("value"), (int, float)), f"{path}: numeric Observation has no numeric value")
            _require(bool(quantity.get("unit") or quantity.get("code")), f"{path}: numeric Observation has no source unit")

    return counts


def validate_directory(directory: Path) -> Counter:
    paths = sorted(Path(directory).glob("*.json"))
    _require(bool(paths), f"No FHIR JSON bundles found in {directory}")
    total: Counter = Counter()
    seen_identities: set[tuple[str, str, str]] = set()
    seen_full_urls: set[str] = set()
    for path in paths:
        is_auxiliary = path.name.startswith(AUXILIARY_BUNDLE_PREFIXES)
        total.update(validate_bundle(
            path,
            require_patient=not is_auxiliary,
            seen_identities=seen_identities,
            seen_full_urls=seen_full_urls,
        ))
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
