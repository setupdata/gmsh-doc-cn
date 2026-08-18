"""Deterministic terminology-candidate extraction."""

from __future__ import annotations

import csv
import hashlib
import re
import tomllib
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from .catalog import TranslationUnit


TERM_QUOTAS: dict[str, int] = {
    "api_symbol": 30,
    "script_command": 25,
    "option_field_plugin": 35,
    "file_format": 20,
    "mesh_geometry_solver": 25,
    "build_workflow_faq": 15,
}
_DECISION_DEFAULTS: dict[str, tuple[str, str]] = {
    "api_symbol": (
        "所有代码、API 调用和正文标识语境均保留英文。",
        "Gmsh API 标识符；使用时必须保持原拼写。",
    ),
    "script_command": (
        "脚本源码、语法示例、命令名和标识符中保留英文。",
        "Gmsh 脚本命令；中文译法只用于说明其含义。",
    ),
    "option_field_plugin": (
        "所有配置、代码、命令和正文标识语境均保留英文。",
        "Gmsh 选项、网格尺寸字段或插件标识符；必须保持原拼写。",
    ),
    "file_format": (
        "规范字段、节名、文件标记和代码中保留英文；普通技术叙述使用中文译法。",
        "Gmsh 文件格式术语。",
    ),
    "mesh_geometry_solver": (
        "API、脚本、选项和专有名称中的标识符保留英文；普通技术叙述使用中文译法。",
        "网格、几何或数值计算领域术语。",
    ),
    "build_workflow_faq": (
        "命令、路径、工具名、软件名和代码标识符保留原文；普通说明使用中文译法。",
        "软件构建、安装或使用说明术语。",
    ),
}
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "does",
    "for", "from", "has", "have", "if", "in", "into", "is", "it", "may", "not",
    "of", "on", "or", "other", "set", "that", "the", "their", "then", "this",
    "to", "use", "used", "using", "value", "when", "which", "will", "with", "you",
    "your", "gmsh", "code", "url", "ref", "pxref", "value", "gitlab-prefix",
    "python", "julia", "fortran", "language", "specific", "definition", "examples",
    "input", "output", "return", "default", "saved",
}
_HEX64_RE = re.compile(r"^[0-9a-f]{64}(?:#\d+)?$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+/#-]*")
_CODE_INNER_RE = re.compile(r"@(code|command|file|option)\{([^{}]+)\}")


def _canonical(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def _term_class(unit: TranslationUnit) -> str:
    if unit.subcategory == "api":
        return "api_symbol"
    if unit.subcategory == "scripting":
        return "script_command"
    if unit.subcategory in {"options", "fields", "plugins"}:
        return "option_field_plugin"
    if unit.subcategory == "formats":
        return "file_format"
    if unit.subcategory in {"compile", "faq"}:
        return "build_workflow_faq"
    return "mesh_geometry_solver"


def _candidate_terms(unit: TranslationUnit, group: str) -> set[str]:
    terms: set[str] = set()
    semantic = unit.semantic_key.removesuffix("#2")
    if group == "api_symbol":
        return {semantic} if semantic.startswith("gmsh/") else set()
    if group == "option_field_plugin":
        return (
            {semantic}
            if semantic and not _HEX64_RE.fullmatch(semantic) and " " not in semantic
            else set()
        )
    if (
        semantic
        and not _HEX64_RE.fullmatch(semantic)
        and len(semantic) <= 80
        and len(semantic.split()) <= 4
    ):
        terms.add(semantic)
    for _, inner in _CODE_INNER_RE.findall(unit.msgid):
        if (
            1 < len(inner) <= 80
            and " " not in inner.strip()
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+/#:-]*", inner.strip())
        ):
            terms.add(inner.strip())
    if group == "script_command":
        return terms
    words = [
        word
        for word in _WORD_RE.findall(unit.msgid)
        if len(word) > 2 and word.casefold() not in _STOPWORDS
    ]
    terms.update(words)
    terms.update(f"{left} {right}" for left, right in zip(words, words[1:]))
    return terms


def load_term_seeds(path: Path) -> dict[str, list[str]]:
    """Load the reviewed, ordered English term inventory."""

    with Path(path).open("rb") as stream:
        document = tomllib.load(stream)
    raw = document.get("terms")
    if not isinstance(raw, dict):
        raise ValueError("terminology seed file must contain a [terms] table")
    result: dict[str, list[str]] = {}
    for group, values in raw.items():
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"term seed group must be an array of strings: {group}")
        result[str(group)] = list(values)
    return result


def load_term_decisions(path: Path) -> dict[str, object]:
    with Path(path).open("rb") as stream:
        document = tomllib.load(stream)
    state = document.get("review_state")
    identity = document.get("identity_groups", [])
    translations = document.get("translations", {})
    if state != "two-independent-reviews-accepted":
        raise ValueError("terminology decisions have not passed two independent reviews")
    if not isinstance(identity, list) or not all(isinstance(item, str) for item in identity):
        raise ValueError("identity_groups must be an array of strings")
    if not isinstance(translations, dict):
        raise ValueError("translations must be a table")
    parsed: dict[str, dict[str, str]] = {}
    for group, table in translations.items():
        if not isinstance(table, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in table.items()
        ):
            raise ValueError(f"translation group must be a string table: {group}")
        parsed[str(group)] = dict(table)
    return {
        "review_state": state,
        "identity_groups": set(identity),
        "translations": parsed,
    }


def apply_term_decisions(
    records: Iterable[Mapping[str, object]], decisions: Mapping[str, object]
) -> list[dict[str, object]]:
    """Apply only a complete set of independently reviewed terminology decisions."""

    if decisions.get("review_state") != "two-independent-reviews-accepted":
        raise ValueError("terminology decisions have not passed two independent reviews")
    identity = set(decisions.get("identity_groups", set()))
    translations = decisions.get("translations", {})
    if not isinstance(translations, Mapping):
        raise ValueError("invalid terminology translations")
    result: list[dict[str, object]] = []
    for original in records:
        record = dict(original)
        group = str(record["candidate_class"])
        term = str(record["term_en"])
        group_translations = translations.get(group, {})
        if group in identity:
            translated = term
        elif isinstance(group_translations, Mapping) and isinstance(
            group_translations.get(term), str
        ):
            translated = str(group_translations[term])
        else:
            raise ValueError(f"missing terminology decision for {group}: {term}")
        context, description = _DECISION_DEFAULTS[group]
        record.update(
            {
                "zh_cn": translated,
                "keep_english_context": context,
                "description": description,
                "status": "approved",
                "ai_review": "two-independent-reviews-accepted",
                "human_approval": "not-required-by-term-policy",
                "effective_version": "4.15.2",
            }
        )
        result.append(record)
    return result


def _searchable_text(unit: TranslationUnit) -> str:
    text = re.sub(r"@[A-Za-z][A-Za-z0-9-]*\{([^{}]*)\}", r"\1", unit.msgid)
    return re.sub(r"@[A-Za-z][A-Za-z0-9-]*", " ", text)


def _has_term_evidence(unit: TranslationUnit, term: str, group: str) -> bool:
    canonical = _canonical(term)
    semantic = _canonical(unit.semantic_key.removesuffix("#2"))
    if canonical == semantic:
        return True
    if canonical in {_canonical(value) for value in _candidate_terms(unit, group)}:
        return True
    searchable = _canonical(_searchable_text(unit))
    pattern = rf"(?<![\w]){re.escape(canonical)}(?![\w])"
    return re.search(pattern, searchable, flags=re.IGNORECASE) is not None


def select_term_candidates(
    units: Iterable[TranslationUnit],
    *,
    quotas: Mapping[str, int] = TERM_QUOTAS,
    preferred_terms: Mapping[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    """Select about 150 English candidates; this function never approves them."""

    unit_list = list(units)
    sources: dict[str, dict[str, set[str]]] = {
        group: defaultdict(set) for group in quotas
    }
    surfaces: dict[tuple[str, str], str] = {}
    source_files: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_nodes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for unit in unit_list:
        group = _term_class(unit)
        if group not in sources:
            continue
        for term in _candidate_terms(unit, group):
            canonical = _canonical(term)
            if not canonical or canonical in _STOPWORDS or len(canonical) > 100:
                continue
            sources[group][canonical].add(unit.unit_id)
            key = (group, canonical)
            surfaces.setdefault(key, term)
            source_files[key].add(unit.relative_file)
            source_nodes[key].add(unit.node)

    used: set[tuple[str, str]] = set()
    selected: list[tuple[str, str]] = []
    for group, quota in quotas.items():
        if preferred_terms is not None:
            declared = preferred_terms.get(group)
            if declared is None:
                raise ValueError(f"missing curated term group: {group}")
            if len(declared) != quota:
                raise ValueError(
                    f"curated term quota mismatch for {group}: need {quota}, have {len(declared)}"
                )
            ranked = []
            for term in declared:
                canonical = _canonical(term)
                matching = [
                    unit
                    for unit in unit_list
                    if _term_class(unit) == group
                    and _has_term_evidence(unit, term, group)
                ]
                if not matching:
                    raise ValueError(
                        f"curated term has no source evidence in {group}: {term}"
                    )
                key = (group, canonical)
                surfaces[key] = term
                for unit in matching:
                    sources[group][canonical].add(unit.unit_id)
                    source_files[key].add(unit.relative_file)
                    source_nodes[key].add(unit.node)
                ranked.append(canonical)
        else:
            ranked = sorted(
                sources[group],
                key=lambda canonical: (
                    -len(sources[group][canonical]),
                    canonical.encode("utf-8"),
                ),
            )
        available = [canonical for canonical in ranked if (group, canonical) not in used]
        if len(available) < quota:
            raise ValueError(
                f"term quota shortfall for {group}: need {quota}, have {len(available)}"
            )
        for canonical in available[:quota]:
            used.add((group, canonical))
            selected.append((group, canonical))

    records: list[dict[str, object]] = []
    for number, (group, canonical) in enumerate(selected, start=1):
        key = (group, canonical)
        term_id = f"term-v1-{number:03d}"
        records.append(
            {
                "term_id": term_id,
                "stable_id": (
                    "term-v1-"
                    + hashlib.sha256(f"{group}\0{canonical}".encode("utf-8")).hexdigest()
                ),
                "term_en": surfaces[key],
                "canonical_form": canonical,
                "candidate_class": group,
                "source_unit_ids": sorted(sources[group][canonical]),
                "source_files": sorted(source_files[key]),
                "source_nodes": sorted(source_nodes[key]),
                "distinct_unit_count": len(sources[group][canonical]),
                "extraction_rule": (
                    "curated-evidence-v1"
                    if preferred_terms is not None
                    else "structured-and-ngram-v1"
                ),
                "zh_cn": "",
                "keep_english_context": "",
                "description": "",
                "status": "candidate_pending",
                "ai_review": "not_evaluated",
                "human_approval": "pending",
                "effective_version": "",
            }
        )
    return records


def merge_term_decisions(
    records: Iterable[Mapping[str, object]], existing_csv: Path | None
) -> list[dict[str, object]]:
    """Carry review decisions forward only for the same stable English term."""

    current = [dict(record) for record in records]
    if existing_csv is None or not Path(existing_csv).is_file():
        return current
    with Path(existing_csv).open("r", encoding="utf-8-sig", newline="") as stream:
        existing = {row["stable_id"]: row for row in csv.DictReader(stream)}
    decision_fields = (
        "zh_cn",
        "keep_english_context",
        "description",
        "status",
        "ai_review",
        "human_approval",
        "effective_version",
    )
    for record in current:
        previous = existing.get(str(record["stable_id"]))
        if not previous:
            continue
        if (
            previous.get("canonical_form") != record["canonical_form"]
            or previous.get("candidate_class") != record["candidate_class"]
            or previous.get("term_en") != record["term_en"]
        ):
            continue
        for field in decision_fields:
            record[field] = previous.get(field, "")
    return current


def write_terms_csv(records: Iterable[Mapping[str, object]], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "term_id",
        "stable_id",
        "term_en",
        "canonical_form",
        "candidate_class",
        "zh_cn",
        "keep_english_context",
        "description",
        "status",
        "ai_review",
        "human_approval",
        "effective_version",
        "distinct_unit_count",
        "source_unit_ids",
        "source_files",
        "source_nodes",
        "extraction_rule",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for record in records:
            row = dict(record)
            for field in ("source_unit_ids", "source_files", "source_nodes"):
                row[field] = json_join(row.get(field, []))
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def json_join(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " | ".join(str(item) for item in value)
    return str(value)
