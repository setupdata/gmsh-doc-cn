"""Command-line entry points used by maintainers and CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path

from .artifacts import write_artifact_manifest
from .baseline import generate_baseline
from .build import build_english_site
from .catalog import apply_catalog, extract_tree, read_po_catalog, write_po
from .deterministic_checks import (
    check_result_map,
    run_deterministic_checks,
    write_deterministic_checks,
)
from .html_inventory import collect_html_inventory, write_html_inventory
from .preview import (
    build_preview_site,
    preview_build_report,
    sha256_file,
    write_preview_report,
)
from .review import read_review_batches, review_evidence_sha256
from .schema_validation import validate_schema
from .semantic_rules import semantic_rule_hashes
from .status import reduce_status, write_status
from .upstream import extract_upstream, fetch_upstream


def _manifest(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)["upstream"]


def _toml_table(path: Path, table: str) -> dict[str, object]:
    with Path(path).open("rb") as stream:
        document = tomllib.load(stream)
    value = document.get(table)
    if not isinstance(value, dict):
        raise ValueError(f"missing [{table}] table in {path}")
    return value


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _selected_check_units(
    args: argparse.Namespace, *, all_unit_ids: list[str] | None = None
) -> list[str]:
    selected = set(args.check_unit)
    if args.check_all:
        if all_unit_ids is None:
            all_unit_ids = [
                unit.unit_id for unit in extract_tree(Path(args.source)).units
            ]
        selected.update(all_unit_ids)
    check_units = sorted(selected, key=lambda value: value.encode("utf-8"))
    if not check_units:
        raise ValueError("requires --check-all or at least one --check-unit")
    return check_units


def _preview_settings(args: argparse.Namespace) -> dict[str, object]:
    manifest_path = Path(args.manifest)
    site_path = Path(args.site_config)
    release_path = Path(args.release)
    po_path = Path(args.po)
    status_path = Path(args.status)
    status_schema_path = Path(args.status_schema)
    preview_schema_path = Path(args.schema)
    rules_path = Path(args.rules)
    glossary_path = Path(args.glossary)
    review_directory = Path(args.reviews)
    review_manifest_path = Path(args.review_manifest)
    check_units = _selected_check_units(args)
    manifest = _manifest(manifest_path)
    site = _toml_table(site_path, "site")
    release = _toml_table(release_path, "release")
    version = str(manifest["version"])
    if site.get("version") != version or release.get("upstream_version") != version:
        raise ValueError("site, release and upstream versions must match")
    if release.get("site_base") != site.get("base"):
        raise ValueError("site and release SITE_BASE values must match")
    if release.get("language") != site.get("html_language_zh"):
        raise ValueError("release and site Chinese language tags must match")
    paths = {
        "upstream_manifest": manifest_path,
        "site_config": site_path,
        "release": release_path,
        "po": po_path,
        "status": status_path,
        "status_schema": status_schema_path,
        "preview_schema": preview_schema_path,
        "translation_rules": rules_path,
        "glossary": glossary_path,
        "review_manifest": review_manifest_path,
        "tool_versions": Path(args.tool_versions),
        "containerfile": Path(args.containerfile),
    }
    status = _json_object(status_path)
    validate_schema(status, _json_object(status_schema_path))
    input_hashes = {name: sha256_file(path) for name, path in paths.items()}
    input_hashes["reviews"] = review_evidence_sha256(
        review_directory, review_manifest_path
    )
    input_hashes["check_units"] = hashlib.sha256(
        json.dumps(check_units, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "manifest": manifest,
        "site": site,
        "release": release,
        "po_path": po_path,
        "status": status,
        "review_directory": review_directory,
        "review_manifest_path": review_manifest_path,
        "check_units": check_units,
        "rules_path": rules_path,
        "glossary_path": glossary_path,
        "input_hashes": input_hashes,
    }


def _run_preview_build(
    args: argparse.Namespace, dist_root: Path, settings: Mapping[str, object]
):
    manifest = settings["manifest"]
    site = settings["site"]
    release = settings["release"]
    if not isinstance(manifest, Mapping) or not isinstance(site, Mapping):
        raise ValueError("invalid preview configuration")
    if not isinstance(release, Mapping) or not isinstance(settings["status"], Mapping):
        raise ValueError("invalid preview release or status input")
    makeinfo = shlex.split(args.makeinfo, posix=os.name != "nt")
    return build_preview_site(
        Path(args.source),
        dist_root,
        po_path=Path(settings["po_path"]),
        status=settings["status"],
        review_directory=Path(settings["review_directory"]),
        review_manifest_path=Path(settings["review_manifest_path"]),
        check_units=settings["check_units"],
        rules_path=Path(settings["rules_path"]),
        glossary_path=Path(settings["glossary_path"]),
        version=str(manifest["version"]),
        source_date_epoch=int(manifest["source_date_epoch"]),
        site_base=str(site["base"]),
        upstream_tag=str(manifest["tag"]),
        upstream_commit=str(manifest["commit"]),
        translation_revision=str(release["translation_revision"]),
        translation_date=str(release["translation_date"]),
        makeinfo_command=makeinfo,
    )


def command_fetch(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    manifest = _manifest(manifest_path)
    fetched = fetch_upstream(manifest_path, Path(args.cache))
    source = extract_upstream(
        fetched.path,
        Path(args.cache) / "source",
        str(manifest["source_directory"]),
    )
    print(json.dumps({"archive": str(fetched.path), "source": str(source), "sha256": fetched.sha256}))


def command_extract(args: argparse.Namespace) -> None:
    source = Path(args.source)
    output = Path(args.output)
    extraction = extract_tree(source)
    write_po(extraction.units, output / "po" / "gmsh.pot")
    write_po(extraction.units, output / "po" / "zh_CN.po", language="zh_CN")
    print(json.dumps({"unit_count": len(extraction.units)}))


def command_baseline(args: argparse.Namespace) -> None:
    result = generate_baseline(
        Path(args.source),
        Path(args.output),
        upstream_manifest=Path(args.manifest),
        pilot_manifest=Path(args.pilot_manifest),
        existing_po=Path(args.po) if args.po else None,
        terminology_seeds=Path(args.terminology_seeds) if args.terminology_seeds else None,
        terminology_decisions=(
            Path(args.terminology_decisions) if args.terminology_decisions else None
        ),
        existing_terms=Path(args.terms) if args.terms else None,
        smoke_candidates=Path(args.smoke_candidates) if args.smoke_candidates else None,
        benchmark_initial_candidates=(
            Path(args.benchmark_initial) if args.benchmark_initial else None
        ),
        benchmark_final_candidates=(Path(args.benchmark_final) if args.benchmark_final else None),
        benchmark_review_directory=(
            Path(args.benchmark_review_directory) if args.benchmark_review_directory else None
        ),
        benchmark_review_manifest=(
            Path(args.benchmark_review_manifest) if args.benchmark_review_manifest else None
        ),
        benchmark_approval=(Path(args.benchmark_approval) if args.benchmark_approval else None),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def command_apply(args: argparse.Namespace) -> None:
    source = Path(args.source)
    extraction = extract_tree(source)
    po = read_po_catalog(Path(args.po))
    publishable = {
        context: value
        for context, value in po.translations.items()
        if "fuzzy" not in po.flags.get(context, set())
    }
    apply_catalog(source, Path(args.output), extraction.units, publishable)


def command_build_english(args: argparse.Namespace) -> None:
    manifest = _manifest(Path(args.manifest))
    makeinfo = shlex.split(args.makeinfo, posix=os.name != "nt")
    result = build_english_site(
        Path(args.source),
        Path(args.dist),
        version=str(manifest["version"]),
        source_date_epoch=int(manifest["source_date_epoch"]),
        makeinfo_command=makeinfo,
    )
    print(
        json.dumps(
            {
                "public_root": str(result.public_root),
                "warning_fingerprints": result.warning_fingerprints,
            }
        )
    )


def command_build_preview(args: argparse.Namespace) -> None:
    settings = _preview_settings(args)
    result = _run_preview_build(args, Path(args.dist), settings)
    print(
        json.dumps(
            {
                "english_root": str(result.english_root),
                "preview_root": str(result.preview_root),
                "formal_unit_count": result.formal_unit_count,
                "fallback_unit_count": result.fallback_unit_count,
                "preview_html_file_count": result.preview_html_file_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def command_artifacts(args: argparse.Namespace) -> None:
    report = write_artifact_manifest(Path(args.root), Path(args.output))
    print(json.dumps({"file_count": report["file_count"]}))


def command_html_inventory(args: argparse.Namespace) -> None:
    report = write_html_inventory(Path(args.root), Path(args.output))
    if report["duplicate_anchors"]:
        raise SystemExit("HTML inventory contains duplicate anchors")
    print(
        json.dumps(
            {
                "html_file_count": report["html_file_count"],
                "anchor_count": report["anchor_count"],
                "broken_internal_link_count": len(report["broken_internal_links"]),
            },
            sort_keys=True,
        )
    )


def command_status(args: argparse.Namespace) -> None:
    source = Path(args.source)
    po_path = Path(args.po)
    extraction = extract_tree(source)
    po = read_po_catalog(po_path)
    catalog = po.translations
    reviews = read_review_batches(Path(args.reviews), Path(args.review_manifest))
    check_units = _selected_check_units(
        args, all_unit_ids=[unit.unit_id for unit in extraction.units]
    )
    checks_report = run_deterministic_checks(source, po_path, check_units)
    if args.checks_output:
        write_deterministic_checks(checks_report, Path(args.checks_output))
    checks = check_result_map(checks_report)
    rules_hash = hashlib.sha256(Path(args.rules).read_bytes()).hexdigest()
    glossary_hash = hashlib.sha256(Path(args.glossary).read_bytes()).hexdigest()
    per_unit_rules = semantic_rule_hashes(
        extraction.units, Path(args.rules), Path(args.glossary)
    )
    report = reduce_status(
        extraction.units,
        catalog,
        reviews,
        checks,
        flags=po.flags,
        catalog_msgids=po.msgids,
        current_rules_hash=rules_hash,
        current_rules_hashes=per_unit_rules,
        current_glossary_hash=glossary_hash,
        obsolete_count=po.obsolete_count,
    )
    write_status(report, Path(args.output))
    print(json.dumps(report["counts"], sort_keys=True))


def command_check_units(args: argparse.Namespace) -> None:
    report = run_deterministic_checks(Path(args.source), Path(args.po), args.unit_id)
    write_deterministic_checks(report, Path(args.output))
    print(
        json.dumps(
            {"checked_count": report["checked_count"], "all_passed": report["all_passed"]},
            sort_keys=True,
        )
    )
    if not report["all_passed"]:
        raise SystemExit(1)


def command_reproducible(args: argparse.Namespace) -> None:
    manifest = _manifest(Path(args.manifest))
    makeinfo = shlex.split(args.makeinfo, posix=os.name != "nt")
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        reports = []
        html_inventories = []
        warning_sets: list[tuple[str, ...]] = []
        for directory in (first, second):
            result = build_english_site(
                Path(args.source),
                Path(directory) / "dist",
                version=str(manifest["version"]),
                source_date_epoch=int(manifest["source_date_epoch"]),
                makeinfo_command=makeinfo,
            )
            reports.append(
                write_artifact_manifest(
                    result.public_root, Path(directory) / "artifact-manifest.json"
                )
            )
            html_inventories.append(collect_html_inventory(result.public_root))
            warning_sets.append(result.warning_fingerprints)
        if reports[0] != reports[1]:
            raise SystemExit("two clean English builds produced different manifests")
        if html_inventories[0] != html_inventories[1]:
            raise SystemExit("two clean English builds produced different HTML anchor inventories")
        if html_inventories[0]["duplicate_anchors"]:
            raise SystemExit("English build contains duplicate HTML anchors")
        expected_html = json.loads(Path(args.html_baseline).read_text(encoding="utf-8"))
        if html_inventories[0] != expected_html:
            raise SystemExit("English HTML anchors differ from tests/fixtures/pilot/html-anchors.json")
        baseline = json.loads(Path(args.warning_baseline).read_text(encoding="utf-8"))
        expected_warnings = tuple(
            sorted(str(item["fingerprint"]) for item in baseline["warnings"])
        )
        if any(values != expected_warnings for values in warning_sets):
            raise SystemExit(
                "English build warnings differ from tests/baselines/texinfo-warnings.json"
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(reports[0], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            json.dumps(
                {
                    "reproducible": True,
                    "file_count": reports[0]["file_count"],
                    "html_file_count": html_inventories[0]["html_file_count"],
                    "anchor_count": html_inventories[0]["anchor_count"],
                    "warning_fingerprints": expected_warnings,
                }
            )
        )


def command_reproducible_preview(args: argparse.Namespace) -> None:
    settings = _preview_settings(args)
    dist_root = Path(args.dist)
    with tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as reports:
        first_result = _run_preview_build(args, dist_root, settings)
        second_result = _run_preview_build(args, Path(second) / "dist", settings)
        first_manifest = write_artifact_manifest(
            first_result.dist_root, Path(reports) / "first-artifacts.json"
        )
        second_manifest = write_artifact_manifest(
            second_result.dist_root, Path(reports) / "second-artifacts.json"
        )
        manifest = settings["manifest"]
        site = settings["site"]
        release = settings["release"]
        input_hashes = settings["input_hashes"]
        if not all(isinstance(value, Mapping) for value in (manifest, site, release)):
            raise ValueError("invalid preview configuration")
        if not isinstance(input_hashes, Mapping):
            raise ValueError("invalid preview input hashes")
        report_arguments = {
            "site_base": str(site["base"]),
            "version": str(manifest["version"]),
            "upstream_tag": str(manifest["tag"]),
            "upstream_commit": str(manifest["commit"]),
            "translation_revision": str(release["translation_revision"]),
            "translation_date": str(release["translation_date"]),
            "source_date_epoch": int(manifest["source_date_epoch"]),
            "input_hashes": {str(key): str(value) for key, value in input_hashes.items()},
        }
        first_report = preview_build_report(
            first_result, artifact_manifest=first_manifest, **report_arguments
        )
        second_report = preview_build_report(
            second_result, artifact_manifest=second_manifest, **report_arguments
        )
        if first_report != second_report:
            raise SystemExit("two clean bilingual preview builds produced different reports")
        schema = _json_object(Path(args.schema))
        validate_schema(first_report, schema)
        write_preview_report(first_report, Path(args.output))
        print(
            json.dumps(
                {
                    "reproducible": True,
                    "formal_unit_count": first_result.formal_unit_count,
                    "fallback_unit_count": first_result.fallback_unit_count,
                    "preview_html_file_count": first_result.preview_html_file_count,
                },
                sort_keys=True,
            )
        )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="gmsh-doc-cn")
    commands = root.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch")
    fetch.add_argument("--manifest", default="upstream/manifest.toml")
    fetch.add_argument("--cache", default="upstream/cache")
    fetch.set_defaults(function=command_fetch)

    extract = commands.add_parser("extract")
    extract.add_argument("--source", required=True)
    extract.add_argument("--output", default=".")
    extract.set_defaults(function=command_extract)

    baseline = commands.add_parser("baseline")
    baseline.add_argument("--source", required=True)
    baseline.add_argument("--output", default=".")
    baseline.add_argument("--manifest", default="upstream/manifest.toml")
    baseline.add_argument("--pilot-manifest", default="tests/fixtures/pilot/manifest.toml")
    baseline.add_argument("--po", default="po/zh_CN.po")
    baseline.add_argument("--terminology-seeds", default="config/terminology-seeds.toml")
    baseline.add_argument(
        "--terminology-decisions", default="config/terminology-decisions.toml"
    )
    baseline.add_argument("--terms", default="glossary/terms.csv")
    baseline.add_argument(
        "--smoke-candidates", default="benchmarks/five-unit-candidates.jsonl"
    )
    baseline.add_argument(
        "--benchmark-initial", default="benchmarks/reference-initial-v1.jsonl"
    )
    baseline.add_argument(
        "--benchmark-final", default="benchmarks/reference-final-v1.jsonl"
    )
    baseline.add_argument(
        "--benchmark-review-directory", default="reviews/v4.15.2-benchmark"
    )
    baseline.add_argument(
        "--benchmark-review-manifest",
        default="reviews/v4.15.2-benchmark/batches.json",
    )
    baseline.add_argument(
        "--benchmark-approval", default="benchmarks/approvals.jsonl"
    )
    baseline.set_defaults(function=command_baseline)

    apply = commands.add_parser("apply")
    apply.add_argument("--source", required=True)
    apply.add_argument("--po", default="po/zh_CN.po")
    apply.add_argument("--output", required=True)
    apply.set_defaults(function=command_apply)

    build = commands.add_parser("build-english")
    build.add_argument("--source", required=True)
    build.add_argument("--dist", default="dist")
    build.add_argument("--manifest", default="upstream/manifest.toml")
    build.add_argument("--makeinfo", default="makeinfo")
    build.set_defaults(function=command_build_english)

    preview = commands.add_parser("build-preview")
    preview.add_argument("--source", required=True)
    preview.add_argument("--dist", default="dist")
    preview.add_argument("--manifest", default="upstream/manifest.toml")
    preview.add_argument("--site-config", default="config/site.toml")
    preview.add_argument("--release", default="releases/v4.15.2-r1.toml")
    preview.add_argument("--po", default="po/zh_CN.po")
    preview.add_argument("--status", default="reports/status.json")
    preview.add_argument("--status-schema", default="schemas/status.schema.json")
    preview.add_argument("--reviews", default="reviews/v4.15.2")
    preview.add_argument("--review-manifest", default="reviews/v4.15.2/batches.json")
    preview.add_argument("--check-unit", action="append", default=[])
    preview.add_argument("--check-all", action="store_true")
    preview.add_argument("--rules", default="config/translation-rules.toml")
    preview.add_argument("--glossary", default="glossary/terms.csv")
    preview.add_argument("--tool-versions", default="container/tool-versions.lock")
    preview.add_argument("--containerfile", default="container/Containerfile")
    preview.add_argument("--schema", default="schemas/preview-build.schema.json")
    preview.add_argument("--makeinfo", default="makeinfo")
    preview.set_defaults(function=command_build_preview)

    artifacts = commands.add_parser("artifacts")
    artifacts.add_argument("--root", required=True)
    artifacts.add_argument("--output", required=True)
    artifacts.set_defaults(function=command_artifacts)

    html_inventory = commands.add_parser("html-inventory")
    html_inventory.add_argument("--root", required=True)
    html_inventory.add_argument("--output", required=True)
    html_inventory.set_defaults(function=command_html_inventory)

    status = commands.add_parser("status")
    status.add_argument("--source", required=True)
    status.add_argument("--po", default="po/zh_CN.po")
    status.add_argument("--reviews", default="reviews/v4.15.2")
    status.add_argument("--review-manifest", default="reviews/v4.15.2/batches.json")
    status.add_argument("--check-unit", action="append", default=[])
    status.add_argument("--check-all", action="store_true")
    status.add_argument("--checks-output")
    status.add_argument("--rules", default="config/translation-rules.toml")
    status.add_argument("--glossary", default="glossary/terms.csv")
    status.add_argument("--output", default="reports/status.json")
    status.set_defaults(function=command_status)

    checks = commands.add_parser("check-units")
    checks.add_argument("--source", required=True)
    checks.add_argument("--po", default="po/zh_CN.po")
    checks.add_argument("--unit-id", action="append", required=True)
    checks.add_argument("--output", required=True)
    checks.set_defaults(function=command_check_units)

    reproducible = commands.add_parser("reproducible-english")
    reproducible.add_argument("--source", required=True)
    reproducible.add_argument("--manifest", default="upstream/manifest.toml")
    reproducible.add_argument("--makeinfo", default="makeinfo")
    reproducible.add_argument(
        "--warning-baseline", default="tests/baselines/texinfo-warnings.json"
    )
    reproducible.add_argument(
        "--html-baseline", default="tests/fixtures/pilot/html-anchors.json"
    )
    reproducible.add_argument("--output", default="reports/english-artifacts.json")
    reproducible.set_defaults(function=command_reproducible)

    reproducible_preview = commands.add_parser("reproducible-preview")
    reproducible_preview.add_argument("--source", required=True)
    reproducible_preview.add_argument("--dist", default="build/preview-site")
    reproducible_preview.add_argument("--manifest", default="upstream/manifest.toml")
    reproducible_preview.add_argument("--site-config", default="config/site.toml")
    reproducible_preview.add_argument("--release", default="releases/v4.15.2-r1.toml")
    reproducible_preview.add_argument("--po", default="po/zh_CN.po")
    reproducible_preview.add_argument("--status", default="reports/status.json")
    reproducible_preview.add_argument("--status-schema", default="schemas/status.schema.json")
    reproducible_preview.add_argument("--reviews", default="reviews/v4.15.2")
    reproducible_preview.add_argument(
        "--review-manifest", default="reviews/v4.15.2/batches.json"
    )
    reproducible_preview.add_argument("--check-unit", action="append", default=[])
    reproducible_preview.add_argument("--check-all", action="store_true")
    reproducible_preview.add_argument("--rules", default="config/translation-rules.toml")
    reproducible_preview.add_argument("--glossary", default="glossary/terms.csv")
    reproducible_preview.add_argument("--tool-versions", default="container/tool-versions.lock")
    reproducible_preview.add_argument("--containerfile", default="container/Containerfile")
    reproducible_preview.add_argument("--makeinfo", default="makeinfo")
    reproducible_preview.add_argument("--schema", default="schemas/preview-build.schema.json")
    reproducible_preview.add_argument("--output", default="reports/preview-artifacts.json")
    reproducible_preview.set_defaults(function=command_reproducible_preview)
    return root


def main(argv: list[str] | None = None) -> None:
    arguments = parser().parse_args(argv)
    arguments.function(arguments)
