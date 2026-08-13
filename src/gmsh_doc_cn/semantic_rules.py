"""Per-unit hashes for the semantic rules and approved terms that actually apply."""

from __future__ import annotations

import csv
import hashlib
import json
import tomllib
from collections.abc import Iterable
from pathlib import Path

from .catalog import TranslationUnit


_SEMANTIC_TERM_FIELDS = (
    "term_id",
    "term_en",
    "zh_cn",
    "keep_english_context",
    "description",
    "status",
    "effective_version",
)


def semantic_rule_hashes(
    units: Iterable[TranslationUnit], rules_path: Path, glossary_path: Path
) -> dict[str, str]:
    """Hash global rules plus only the approved glossary rows used by each unit."""

    unit_list = list(units)
    by_id = {unit.unit_id: unit for unit in unit_list}
    if len(by_id) != len(unit_list):
        raise ValueError("duplicate unit_id while computing semantic rule hashes")
    with Path(rules_path).open("rb") as stream:
        rules = tomllib.load(stream)

    terms_by_unit: dict[str, list[dict[str, str]]] = {unit_id: [] for unit_id in by_id}
    with Path(glossary_path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("status") != "approved":
                continue
            unit_ids = [value.strip() for value in row.get("source_unit_ids", "").split("|")]
            unit_ids = [value for value in unit_ids if value]
            unknown = set(unit_ids) - set(by_id)
            if unknown:
                raise ValueError(
                    f"approved glossary term references unknown unit_id: {sorted(unknown)[0]}"
                )
            semantic_term = {field: row.get(field, "") for field in _SEMANTIC_TERM_FIELDS}
            for unit_id in unit_ids:
                terms_by_unit[unit_id].append(semantic_term)

    hashes: dict[str, str] = {}
    for unit_id in sorted(by_id, key=lambda value: value.encode("utf-8")):
        terms = sorted(
            terms_by_unit[unit_id],
            key=lambda row: (row["term_id"].encode("utf-8"), row["term_en"].encode("utf-8")),
        )
        canonical = json.dumps(
            {
                "algorithm": "semantic-rules-v1",
                "rules": rules,
                "terms": terms,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        hashes[unit_id] = hashlib.sha256(canonical).hexdigest()
    return hashes
