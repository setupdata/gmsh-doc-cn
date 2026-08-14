"""Generate deterministic translation-pipeline baselines from a pinned source tree."""

from __future__ import annotations

import hashlib
import json
import tempfile
import tomllib
from pathlib import Path
from typing import Mapping

from .benchmark import (
    select_benchmark,
    select_smoke_benchmark,
    validate_smoke_candidates,
    write_jsonl,
    write_report,
)
from .benchmark_evidence import (
    ROLE_PROMPT_FILES,
    bind_reference_translations,
    read_jsonl,
    reviewed_qualification_report,
)
from .catalog import (
    TranslationUnit,
    PoEntry,
    apply_catalog,
    extract_tree,
    read_po_catalog,
    write_po,
)
from .pilot import select_pilot_units, validate_pilot_files, validate_pilot_manifest
from .review import read_review_batches, review_evidence_sha256
from .semantic_rules import semantic_rule_hashes
from .structure import compare_structures, scan_structure
from .terminology import (
    apply_term_decisions,
    load_term_decisions,
    load_term_seeds,
    merge_term_decisions,
    select_term_candidates,
    write_terms_csv,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(values: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def generate_baseline(
    source_root: Path,
    output_root: Path,
    *,
    upstream_manifest: Path,
    pilot_manifest: Path,
    existing_po: Path | None = None,
    terminology_seeds: Path | None = None,
    terminology_decisions: Path | None = None,
    existing_terms: Path | None = None,
    smoke_candidates: Path | None = None,
    benchmark_initial_candidates: Path | None = None,
    benchmark_final_candidates: Path | None = None,
    benchmark_review_directory: Path | None = None,
    benchmark_review_manifest: Path | None = None,
    benchmark_approval: Path | None = None,
) -> dict[str, object]:
    """Generate POT, PO normalization, pilot evidence, terms and benchmark manifests."""

    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    extraction = extract_tree(source_root)
    units = extraction.units
    translations: Mapping[str, str] = {}
    po_flags: Mapping[str, set[str]] = {}
    previous_msgids: Mapping[str, str] = {}
    obsolete_entries: list[PoEntry] = []
    if existing_po is not None and Path(existing_po).is_file():
        parsed_po = read_po_catalog(existing_po)
        translations = parsed_po.translations
        po_flags = parsed_po.flags
        previous_msgids = parsed_po.msgids
        obsolete_entries.extend(parsed_po.obsolete_entries)
    current_contexts = {unit.msgctxt for unit in units}
    active_translations = {
        context: value for context, value in translations.items() if context in current_contexts
    }
    for context in sorted(set(translations) - current_contexts, key=lambda value: value.encode("utf-8")):
        obsolete_entries.append(
            PoEntry(
                context,
                previous_msgids.get(context, ""),
                translations[context],
                frozenset(po_flags.get(context, set())),
            )
        )
    obsolete_by_context = {entry.msgctxt: entry for entry in obsolete_entries}
    obsolete_context_count = len(obsolete_by_context)
    active_flags = {context: values for context, values in po_flags.items() if context in current_contexts}

    pot = output_root / "po" / "gmsh.pot"
    po = output_root / "po" / "zh_CN.po"
    write_po(units, pot)
    write_po(
        units,
        po,
        language="zh_CN",
        translations=active_translations,
        flags=active_flags,
        obsolete_entries=obsolete_by_context.values(),
    )

    with Path(pilot_manifest).open("rb") as stream:
        pilot_config = tomllib.load(stream)["pilot"]
    pilot_units = select_pilot_units(units, pilot_config)
    validate_pilot_manifest(pilot_units, pilot_config)
    protected: list[dict[str, object]] = []
    for unit in pilot_units:
        for index, value in enumerate(unit.protected_values):
            protected.append(
                {
                    "kind": "protected-token",
                    "relative_file": unit.relative_file,
                    "node": unit.node,
                    "structure_path": unit.structure_path,
                    "unit_id": unit.unit_id,
                    "index": index,
                    "value": value,
                }
            )
    _write_jsonl(
        protected,
        output_root / "tests" / "fixtures" / "pilot" / "protected.jsonl",
    )

    structure = scan_structure(source_root)
    validate_pilot_files(
        source_root,
        pilot_config,
        include_files=[
            record.relative_file.rsplit("/", 1)[0] + "/" + record.value
            for record in structure.records
            if record.kind == "include" and record.relative_file.endswith("gmsh.texi")
        ]
        + ["doc/texinfo/gmsh.texi"],
    )
    _write_jsonl(
        [
            {
                "kind": record.kind,
                "relative_file": record.relative_file,
                "node": record.node,
                "value": record.value,
            }
            for record in structure.records
        ],
        output_root / "tests" / "fixtures" / "pilot" / "structure.jsonl",
    )
    wanted_code = {str(item) for item in pilot_config.get("code_files", [])}
    code_files = [
        {
            "path": item.relative_file,
            "size": item.size,
            "sha256": item.sha256,
        }
        for item in structure.external_files
        if item.relative_file in wanted_code
    ]
    missing_code = wanted_code - {str(item["path"]) for item in code_files}
    if missing_code:
        raise ValueError(f"pilot code files missing from include graph: {sorted(missing_code)}")
    _write_json(
        {"schema_version": 1, "files": code_files},
        output_root / "tests" / "fixtures" / "pilot" / "code-files.json",
    )

    round_trip_comparisons: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as directory:
        for name, round_trip_catalog in (
            ("empty", {}),
            (
                "current-po",
                {
                    context: value
                    for context, value in active_translations.items()
                    if "fuzzy" not in active_flags.get(context, set())
                },
            ),
        ):
            translated_root = Path(directory) / name
            apply_catalog(source_root, translated_root, units, round_trip_catalog)
            comparison = compare_structures(structure, scan_structure(translated_root))
            round_trip_comparisons.append(
                {
                    "name": name,
                    "passed": comparison.passed,
                    "changed_kinds": list(comparison.changed_kinds),
                }
            )
        empty_root = Path(directory) / "empty"
        byte_paths = {
            unit.relative_file for unit in units
        } | {
            item.relative_file for item in structure.external_files
        } | {str(value) for value in pilot_config.get("image_files", [])}
        byte_identity = all(
            (source_root / relative).read_bytes() == (empty_root / relative).read_bytes()
            for relative in sorted(byte_paths)
        )
    if not byte_identity or not all(
        bool(item["passed"]) for item in round_trip_comparisons
    ):
        raise ValueError("translation round trip changed protected structure or source bytes")

    benchmark = select_benchmark(units)
    units_by_id = {unit.unit_id: unit for unit in units}
    benchmark_validation: dict[str, object] | None = None
    initial_reference_rows: list[dict[str, object]] = []
    initial_reference_validation: dict[str, object] | None = None
    if benchmark_final_candidates is not None and Path(benchmark_final_candidates).is_file():
        benchmark, benchmark_validation = bind_reference_translations(
            benchmark,
            read_jsonl(benchmark_final_candidates),
            units_by_id,
        )
        _write_json(
            benchmark_validation,
            output_root / "benchmarks" / "reference-validation-v1.json",
        )
    if any(row["reference_translation_status"] != "accepted" for row in benchmark):
        raise ValueError("the 100-unit benchmark references have not been frozen")
    if benchmark_initial_candidates is not None and Path(benchmark_initial_candidates).is_file():
        initial_reference_rows = read_jsonl(benchmark_initial_candidates)
        initial_reference_validation = validate_smoke_candidates(
            benchmark,
            initial_reference_rows,
            units_by_id,
            require_protected_content=False,
        )
        _write_json(
            initial_reference_validation,
            output_root / "benchmarks" / "reference-initial-validation-v1.json",
        )
    write_jsonl(benchmark, output_root / "benchmarks" / "units.jsonl")
    groups = {
        "ordinary-tutorial": {"ordinary_tutorial"},
        "script-api": {"script_api"},
        "option-field-plugin": {"option_field_plugin"},
        "format-compile-faq": {"formats_compile_faq"},
    }
    for filename, categories in groups.items():
        write_jsonl(
            [record for record in benchmark if record["category"] in categories],
            output_root / "benchmarks" / "categories" / f"{filename}.jsonl",
        )
    smoke = select_smoke_benchmark(benchmark)
    write_jsonl(smoke, output_root / "benchmarks" / "five-unit-smoke.jsonl")
    smoke_validation: dict[str, object] | None = None
    if smoke_candidates is not None and Path(smoke_candidates).is_file():
        candidates = [
            json.loads(line)
            for line in Path(smoke_candidates).read_text(encoding="utf-8").splitlines()
            if line
        ]
        smoke_validation = validate_smoke_candidates(
            smoke, candidates, units_by_id
        )
        _write_json(
            smoke_validation,
            output_root / "benchmarks" / "five-unit-validation.json",
        )

    seeds = load_term_seeds(terminology_seeds) if terminology_seeds is not None else None
    terms = select_term_candidates(units, preferred_terms=seeds)
    if terminology_decisions is not None:
        terms = apply_term_decisions(terms, load_term_decisions(terminology_decisions))
    else:
        terms = merge_term_decisions(terms, existing_terms)
    write_terms_csv(terms, output_root / "glossary" / "terms.csv")
    repository_root = Path(upstream_manifest).resolve().parent.parent
    if (
        benchmark_initial_candidates is None
        or not Path(benchmark_initial_candidates).is_file()
        or benchmark_final_candidates is None
        or benchmark_review_directory is None
        or benchmark_review_manifest is None
        or not Path(benchmark_review_manifest).is_file()
        or benchmark_validation is None
        or initial_reference_validation is None
    ):
        raise ValueError("benchmark review evidence inputs are incomplete")
    benchmark_review_directory = Path(benchmark_review_directory)
    benchmark_review_manifest = Path(benchmark_review_manifest)
    benchmark_reviews = read_review_batches(
        benchmark_review_directory,
        benchmark_review_manifest,
    )
    evidence_hash = review_evidence_sha256(
        benchmark_review_directory,
        benchmark_review_manifest,
    )
    approval: dict[str, object] | None = None
    if benchmark_approval is not None and Path(benchmark_approval).is_file():
        approval_rows = read_jsonl(benchmark_approval)
        if len(approval_rows) != 1:
            raise ValueError("benchmark approval file must contain exactly one record")
        approval = approval_rows[0]
    manifest_hash = file_sha256(upstream_manifest)
    prompt_hashes = {
        role: file_sha256(repository_root / prompt_file)
        for role, prompt_file in ROLE_PROMPT_FILES.items()
    }
    rules_hashes = semantic_rule_hashes(
        units,
        repository_root / "config" / "translation-rules.toml",
        repository_root / "glossary" / "terms.csv",
    )
    glossary_hash = file_sha256(repository_root / "glossary" / "terms.csv")
    report = reviewed_qualification_report(
        benchmark,
        initial_reference_rows,
        benchmark_reviews,
        approval,
        units_by_id=units_by_id,
        current_rules_hashes=rules_hashes,
        current_glossary_hash=glossary_hash,
        expected_prompt_hashes=prompt_hashes,
        pot_sha256=file_sha256(pot),
        manifest_sha256=manifest_hash,
        initial_reference_sha256=file_sha256(benchmark_initial_candidates),
        reference_sha256=file_sha256(benchmark_final_candidates),
        review_manifest_sha256=file_sha256(benchmark_review_manifest),
        review_evidence_sha256=evidence_hash,
        initial_reference_validation=initial_reference_validation,
        reference_validation=benchmark_validation,
    )
    write_report(report, output_root / "benchmarks" / "qualification-v1.json")

    tool_lock_path = repository_root / "container" / "tool-versions.lock"
    html_anchor_path = repository_root / "tests" / "fixtures" / "pilot" / "html-anchors.json"
    warning_baseline_path = repository_root / "tests" / "baselines" / "texinfo-warnings.json"
    with tool_lock_path.open("rb") as stream:
        tool_lock = tomllib.load(stream)

    summary: dict[str, object] = {
        "schema_version": 1,
        "extractor": "gmsh-doc-cn-custom-texinfo-v1",
        "upstream_manifest_sha256": manifest_hash,
        "tool_lock_sha256": file_sha256(tool_lock_path),
        "tool_versions": {
            name: str(tool_lock["required"][name])
            for name in ("texinfo", "gettext", "po4a")
        },
        "html_anchor_baseline_sha256": file_sha256(html_anchor_path),
        "texinfo_warning_baseline_sha256": file_sha256(warning_baseline_path),
        "unit_count": len(units),
        "obsolete_input_context_count": obsolete_context_count,
        "pilot_unit_count": len(pilot_units),
        "protected_token_count": len(protected),
        "structure_record_count": len(structure.records),
        "external_file_count": len(structure.external_files),
        "benchmark_unit_count": len(benchmark),
        "smoke_validation": (
            "pass" if smoke_validation and smoke_validation["all_passed"] else "not-run"
        ),
        "term_candidate_count": len(terms),
        "po4a_admitted": False,
        "po4a_evaluation": "tests/fixtures/pilot/po4a-evaluation.json",
        "po4a_reason": (
            "po4a 0.74 changed protected Texinfo tokens and structure records; "
            "the dedicated parser is the admitted path"
        ),
        "round_trip": "pass",
        "round_trip_byte_identity": byte_identity,
        "round_trip_comparisons": round_trip_comparisons,
        "benchmark_qualification_state": report["qualification_state"],
    }
    _write_json(
        summary,
        output_root / "tests" / "fixtures" / "pilot" / "pilot-report.json",
    )
    return summary
