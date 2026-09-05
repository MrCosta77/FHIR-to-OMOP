import csv
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.utils.assets import runtime_asset

SPEC_PATH = runtime_asset(
    "resources", "omop_cdm_v5_4", "OMOP_CDMv5.4_Field_Level.csv"
)
CDM_VERSION = "5.4"
CDM_RELEASE = "v5.4.3"
CDM_SOURCE_COMMIT = "746a15e0fb36a95ba6cc0993737f1273bbad92f2"
SPEC_SHA256 = "2b763c7a2aeb309372c1564350939551531318e2078fd4443e03b2741e79b77c"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class FieldSpec:
    table: str
    name: str
    required: bool
    datatype: str
    primary_key: bool
    foreign_key: bool
    fk_table: str | None
    fk_field: str | None
    fk_domain: str | None


def _as_bool(value):
    return str(value).strip().upper() == "TRUE"


def _optional(value):
    value = str(value or "").strip()
    return None if not value or value.upper() == "NA" else value


def quote_identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe OMOP identifier: {value!r}")
    return f'"{value}"'


def verify_specification(path=SPEC_PATH):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != SPEC_SHA256:
        raise ValueError(
            "The vendored OMOP CDM 5.4 field specification does not match "
            f"the pinned OHDSI {CDM_RELEASE} release."
        )
    return digest


@lru_cache(maxsize=1)
def load_table_specs():
    verify_specification()
    tables = OrderedDict()
    with SPEC_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            table = row["cdmTableName"].strip().lower()
            name = row["cdmFieldName"].strip().lower()
            # The OHDSI SQLite-oriented metadata quotes the reserved word
            # "offset". Identifiers are quoted uniformly when DDL is emitted.
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            quote_identifier(table)
            quote_identifier(name)
            tables.setdefault(table, []).append(FieldSpec(
                table=table,
                name=name,
                required=_as_bool(row["isRequired"]),
                datatype=row["cdmDatatype"].strip(),
                primary_key=_as_bool(row["isPrimaryKey"]),
                foreign_key=_as_bool(row["isForeignKey"]),
                fk_table=_optional(row["fkTableName"]),
                fk_field=_optional(row["fkFieldName"]),
                fk_domain=_optional(row["fkDomain"]),
            ))
    if len(tables) != 39 or sum(map(len, tables.values())) != 432:
        raise ValueError("Unexpected OMOP CDM 5.4 specification dimensions.")
    return tables


def duckdb_type(datatype):
    normalized = datatype.strip().lower()
    if normalized == "integer":
        # OMOP's logical integer identifiers can exceed signed 32-bit range.
        return "BIGINT"
    if normalized == "float":
        return "DOUBLE"
    if normalized == "date":
        return "DATE"
    if normalized == "datetime":
        return "TIMESTAMP"
    match = re.fullmatch(r"varchar\((max|\d+)\)", normalized)
    if match:
        return "VARCHAR" if match.group(1) == "max" else f"VARCHAR({match.group(1)})"
    raise ValueError(f"Unsupported OMOP datatype for DuckDB: {datatype!r}")


def create_table_sql(table, physical_name=None, if_not_exists=False):
    specs = load_table_specs()
    if table not in specs:
        raise ValueError(f"Unknown OMOP CDM table: {table}")
    physical_name = physical_name or table
    table_sql = quote_identifier(physical_name)
    fields = specs[table]
    definitions = [
        f"{quote_identifier(field.name)} {duckdb_type(field.datatype)}"
        + (" NOT NULL" if field.required else "")
        for field in fields
    ]
    primary_key = [field.name for field in fields if field.primary_key]
    if primary_key:
        definitions.append(
            "PRIMARY KEY ("
            + ", ".join(quote_identifier(name) for name in primary_key)
            + ")"
        )
    qualifier = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {qualifier}{table_sql} (\n  " + ",\n  ".join(definitions) + "\n)"


def ensure_empty_cdm_tables(con, exclude=()):
    excluded = set(exclude)
    for table in load_table_specs():
        if table not in excluded:
            con.execute(create_table_sql(table, if_not_exists=True))


def ensure_table_columns(con, table):
    """Add only missing OMOP fields; existing data and types are preserved."""
    existing = {
        row[0]
        for row in con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchall()
    }
    if not existing:
        con.execute(create_table_sql(table))
        return
    for field in load_table_specs()[table]:
        if field.name not in existing:
            con.execute(
                f"ALTER TABLE {quote_identifier(table)} ADD COLUMN "
                f"{quote_identifier(field.name)} {duckdb_type(field.datatype)}"
            )


def ensure_complete_cdm_schema(con):
    """Create missing tables/fields without deleting or replacing any data."""
    ensure_empty_cdm_tables(con)
    for table in load_table_specs():
        ensure_table_columns(con, table)


def record_schema_manifest(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS cdm_schema_manifest (
            cdm_version VARCHAR PRIMARY KEY,
            source_release VARCHAR NOT NULL,
            source_commit VARCHAR NOT NULL,
            specification_sha256 VARCHAR NOT NULL,
            installed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        INSERT INTO cdm_schema_manifest (
            cdm_version, source_release, source_commit,
            specification_sha256
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT (cdm_version) DO UPDATE SET
            source_release = EXCLUDED.source_release,
            source_commit = EXCLUDED.source_commit,
            specification_sha256 = EXCLUDED.specification_sha256,
            installed_at = now()
    """, [CDM_VERSION, CDM_RELEASE, CDM_SOURCE_COMMIT, SPEC_SHA256])


def expected_columns(table):
    return [field.name for field in load_table_specs()[table]]


def validate_table_schema(con, table, physical_name=None):
    physical_name = physical_name or table
    actual = con.execute(
        f"PRAGMA table_info({quote_identifier(physical_name)})"
    ).fetchall()
    if not actual:
        raise ValueError(f"OMOP table {physical_name} does not exist.")
    fields = load_table_specs()[table]
    actual_names = [row[1] for row in actual]
    expected_names = [field.name for field in fields]
    if actual_names != expected_names:
        raise ValueError(
            f"{physical_name} does not match OMOP CDM {CDM_VERSION} columns. "
            f"Expected {expected_names}; received {actual_names}."
        )
    for row, field in zip(actual, fields, strict=False):
        actual_type = row[2].upper().split("(", 1)[0]
        expected_type = duckdb_type(field.datatype).upper().split("(", 1)[0]
        if actual_type != expected_type:
            raise ValueError(
                f"{physical_name}.{field.name} has type {row[2]}, "
                f"expected {duckdb_type(field.datatype)}."
            )
        if bool(row[3]) != field.required:
            raise ValueError(
                f"{physical_name}.{field.name} has an invalid NOT NULL contract."
            )
        if bool(row[5]) != field.primary_key:
            raise ValueError(
                f"{physical_name}.{field.name} has an invalid primary-key contract."
            )
    return True
