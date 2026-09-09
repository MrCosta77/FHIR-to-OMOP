"""Versioned, non-mapping normalization for terminology retrieval."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from src.utils.assets import runtime_asset

DEFAULT_LIS_ALIASES = runtime_asset("config", "lis_aliases.json")


@dataclass(frozen=True, slots=True)
class RetrievalNormalization:
    original_text: str
    retrieval_text: str
    alias_id: str | None
    lexicon_version: str | None
    lexicon_sha256: str | None

    @property
    def alias_applied(self) -> bool:
        return self.alias_id is not None


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def load_lis_aliases(path: Path = DEFAULT_LIS_ALIASES) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    required = {
        "schema_version", "lexicon_version", "scope", "target_table", "aliases",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("LIS alias lexicon does not match the versioned schema.")
    if payload["schema_version"] != "1.0.0":
        raise ValueError("Unsupported LIS alias schema version.")
    if payload["target_table"] != "measurement":
        raise ValueError("LIS aliases must be scoped to measurement retrieval.")
    if not isinstance(payload["aliases"], list):
        raise ValueError("LIS aliases must be a list.")
    seen_aliases = set()
    seen_ids = set()
    index = {}
    for entry in payload["aliases"]:
        if not isinstance(entry, dict) or set(entry) != {
            "alias_id", "alias", "retrieval_expansion",
        }:
            raise ValueError("Invalid LIS alias entry.")
        if not all(
            isinstance(entry[field], str) and entry[field].strip()
            for field in entry
        ):
            raise ValueError("LIS alias fields must be non-empty text.")
        alias_key = _key(entry["alias"])
        if alias_key in seen_aliases or entry["alias_id"] in seen_ids:
            raise ValueError("LIS alias keys and IDs must be unique.")
        seen_aliases.add(alias_key)
        seen_ids.add(entry["alias_id"])
        index[alias_key] = entry
    return {
        **payload,
        "index": index,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def normalize_retrieval_text(
    source_value: str,
    target_table: str,
    *,
    lexicon: dict | None = None,
) -> RetrievalNormalization:
    """Expand exact governed aliases for retrieval without assigning a concept."""
    source_value = str(source_value)
    if target_table != "measurement":
        return RetrievalNormalization(source_value, source_value, None, None, None)
    lexicon = lexicon or load_lis_aliases()
    entry = lexicon["index"].get(_key(source_value))
    if entry is None:
        return RetrievalNormalization(
            source_value,
            source_value,
            None,
            lexicon["lexicon_version"],
            lexicon["sha256"],
        )
    return RetrievalNormalization(
        original_text=source_value,
        retrieval_text=entry["retrieval_expansion"],
        alias_id=entry["alias_id"],
        lexicon_version=lexicon["lexicon_version"],
        lexicon_sha256=lexicon["sha256"],
    )
