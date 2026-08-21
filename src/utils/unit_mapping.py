"""Normalization rules for FHIR UCUM units before OMOP vocabulary lookup."""

UCUM_SYSTEM = "http://unitsofmeasure.org"

# Only explicit, reviewed equivalences belong here. OMOP/UCUM matching remains
# case-sensitive after normalization.
UCUM_ALIASES = {
    "U/L": "[U]/L",
    "kU/L": "10*3.[U]/L",
    "m[IU]/L": "10*-3.[iU]/L",
    "mL/min/{1.73_m2}": "mL/min/(173.10*-2.m2)",
    "ng/dl": "ng/dL",
}


def canonical_ucum_code(system, code):
    """Return a reviewed OMOP UCUM code, or None for non-UCUM/missing input."""
    if system != UCUM_SYSTEM or code is None:
        return None
    code = str(code).strip()
    if not code:
        return None
    return UCUM_ALIASES.get(code, code)
