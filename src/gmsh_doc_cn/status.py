"""Deterministic translation-state reduction."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .catalog import TranslationUnit


def candidate_hash(value: str) -> str:
    normalised = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def reduce_status(
    units: Iterable[TranslationUnit],
    catalog: Mapping[str, str],
    reviews: Sequence[Mapping[str, object]],
    deterministic_checks: Mapping[str, bool],
    *,
    flags: Mapping[str, set[str]] | None = None,
    catalog_msgids: Mapping[str, str] | None = None,
    current_rules_hash: str | None = None,
    current_rules_hashes: Mapping[str, str] | None = None,
    current_glossary_hash: str | None = None,
    obsolete_count: int = 0,
) -> dict[str, object]:
    """Reduce PO content and append-only review events to one state per unit."""

    flags = flags or {}
    unit_list = list(units)
    known_contexts = {unit.msgctxt for unit in unit_list}
    unknown_catalog_contexts = set(catalog) - known_contexts
    if unknown_catalog_contexts:
        raise ValueError(
            f"catalog references unknown msgctxt: {sorted(unknown_catalog_contexts)[0]}"
        )
    unknown_checks = set(deterministic_checks) - set(by_id := {unit.unit_id: unit for unit in unit_list})
    if unknown_checks:
        raise ValueError(
            f"deterministic check references unknown unit_id: {sorted(unknown_checks)[0]}"
        )
    for unit_id, result in deterministic_checks.items():
        if not isinstance(result, bool):
            raise ValueError(f"deterministic check result must be boolean: {unit_id}")
    if catalog_msgids is not None:
        missing_msgids = known_contexts - set(catalog_msgids)
        unknown_msgids = set(catalog_msgids) - known_contexts
        if missing_msgids:
            raise ValueError(f"PO is missing current msgctxt: {sorted(missing_msgids)[0]}")
        if unknown_msgids:
            raise ValueError(f"PO contains unknown active msgctxt: {sorted(unknown_msgids)[0]}")
        by_context = {unit.msgctxt: unit for unit in unit_list}
        for context, msgid in catalog_msgids.items():
            if msgid != by_context[context].msgid:
                raise ValueError(f"PO msgid does not match current source: {context}")
    unknown_flag_contexts = set(flags) - known_contexts
    if unknown_flag_contexts:
        raise ValueError(f"gettext flags reference unknown msgctxt: {sorted(unknown_flag_contexts)[0]}")
    for context, values in flags.items():
        unsupported = set(values) - {"fuzzy", "obsolete"}
        if unsupported:
            raise ValueError(
                f"unsupported gettext flag for {context}: {sorted(unsupported)[0]}"
            )
        if "obsolete" in values:
            raise ValueError(f"obsolete PO entry cannot map to an active unit: {context}")
    if len(by_id) != len(unit_list):
        raise ValueError("duplicate unit_id")
    if current_rules_hashes is not None:
        unknown_rule_units = set(current_rules_hashes) - set(by_id)
        missing_rule_units = set(by_id) - set(current_rules_hashes)
        if unknown_rule_units:
            raise ValueError(
                f"semantic rules reference unknown unit_id: {sorted(unknown_rule_units)[0]}"
            )
        if missing_rule_units:
            raise ValueError(
                f"semantic rules are missing unit_id: {sorted(missing_rule_units)[0]}"
            )

    event_ids: set[str] = set()
    run_roles: dict[str, str] = {}
    conclusions: dict[tuple[object, ...], str] = {}
    by_unit: dict[str, list[Mapping[str, object]]] = {}
    for record in reviews:
        event_id = str(record.get("event_id", ""))
        if not event_id or event_id in event_ids:
            raise ValueError(f"duplicate or missing review event_id: {event_id}")
        event_ids.add(event_id)
        unit_id = str(record.get("unit_id", ""))
        if unit_id not in by_id:
            raise ValueError(f"review references unknown unit_id: {unit_id}")
        role = str(record.get("role", ""))
        result = str(record.get("result", ""))
        if role not in {"translation", "language_review", "technical_review", "revision"}:
            raise ValueError(f"invalid review role: {role}")
        if result not in {"accept", "revise", "unresolved"}:
            raise ValueError(f"invalid review result: {result}")
        run_id = str(record.get("run_id", ""))
        if run_id:
            previous_role = run_roles.get(run_id)
            if previous_role is not None and previous_role != role:
                raise ValueError(f"review run_id is shared across roles: {run_id}")
            run_roles[run_id] = role
        key = (
            unit_id,
            record.get("source_hash"),
            record.get("candidate_hash"),
            role,
            int(record.get("round", 0)),
        )
        previous = conclusions.get(key)
        if previous is not None:
            description = "conflicting" if previous != result else "duplicate"
            raise ValueError(f"{description} review conclusions for {unit_id} {role}")
        conclusions[key] = result
        by_unit.setdefault(unit_id, []).append(record)

    rows: list[dict[str, object]] = []
    for unit in unit_list:
        translation = catalog.get(unit.msgctxt, "")
        current_candidate_hash = candidate_hash(translation) if translation else None
        unit_reviews = by_unit.get(unit.unit_id, [])
        semantic_rules_hash = (
            current_rules_hashes[unit.unit_id]
            if current_rules_hashes is not None
            else current_rules_hash
        )
        matching_input_reviews = [
            record
            for record in unit_reviews
            if str(record.get("source_hash")) == unit.source_hash
            and str(record.get("context_hash")) == unit.context_hash
            and str(record.get("protected_hash")) == unit.protected_hash
            and (
                semantic_rules_hash is None
                or str(record.get("rules_hash")) == semantic_rules_hash
            )
            and (
                current_rules_hashes is not None
                or current_glossary_hash is None
                or str(record.get("glossary_hash")) == current_glossary_hash
            )
        ]
        if not translation:
            state = "untranslated"
        elif "fuzzy" in flags.get(unit.msgctxt, set()):
            state = "stale"
        elif unit_reviews and not matching_input_reviews:
            state = "stale"
        else:
            current = [
                record
                for record in matching_input_reviews
                if str(record.get("candidate_hash")) == current_candidate_hash
            ]
            if any(
                str(record.get("result")) in {"revise", "unresolved"}
                for record in current
            ):
                state = "unresolved"
            else:
                initial_translation_accepted = any(
                    record.get("role") == "translation" and record.get("result") == "accept"
                    for record in matching_input_reviews
                )
                current_generation_accepted = any(
                    record.get("role") in {"translation", "revision"}
                    and record.get("result") == "accept"
                    for record in current
                )
                language_accepted = any(
                    record.get("role") == "language_review" and record.get("result") == "accept"
                    for record in current
                )
                technical_accepted = any(
                    record.get("role") == "technical_review" and record.get("result") == "accept"
                    for record in current
                )
                if not language_accepted:
                    state = "draft"
                elif not technical_accepted:
                    state = "language-reviewed"
                elif (
                    not initial_translation_accepted
                    or not current_generation_accepted
                    or not deterministic_checks.get(unit.unit_id, False)
                ):
                    state = "technical-reviewed"
                else:
                    state = "formal"
        rows.append(
            {
                "unit_id": unit.unit_id,
                "msgctxt": unit.msgctxt,
                "source_hash": unit.source_hash,
                "candidate_hash": current_candidate_hash,
                "semantic_rules_hash": semantic_rules_hash,
                "state": state,
            }
        )

    counts = Counter(str(row["state"]) for row in rows)
    return {
        "schema_version": 1,
        "algorithm": "translation-status-reducer-v1",
        "input_hashes": {
            "translation_rules": current_rules_hash,
            "glossary": current_glossary_hash,
        },
        "obsolete_count": obsolete_count,
        "unit_count": len(rows),
        "counts": dict(sorted(counts.items())),
        "units": rows,
    }


def write_status(report: Mapping[str, object], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
