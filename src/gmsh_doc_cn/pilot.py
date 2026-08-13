"""Pinned compatibility-pilot selection."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .catalog import TranslationUnit


def select_pilot_units(
    units: Iterable[TranslationUnit], manifest: Mapping[str, object]
) -> list[TranslationUnit]:
    nodes = {str(item) for item in manifest.get("nodes", [])}
    api_keys = {str(item) for item in manifest.get("api_keys", [])}
    return [
        unit
        for unit in units
        if (unit.relative_file.endswith("gmsh.texi") and unit.node in nodes)
        or (unit.relative_file.endswith("api.texi") and unit.semantic_key in api_keys)
    ]


def validate_pilot_manifest(
    selection: Sequence[TranslationUnit], manifest: Mapping[str, object]
) -> None:
    nodes = {str(item) for item in manifest.get("nodes", [])}
    api_keys = {str(item) for item in manifest.get("api_keys", [])}
    selected_nodes = {
        unit.node for unit in selection if unit.relative_file.endswith("gmsh.texi")
    }
    selected_api = {
        unit.semantic_key for unit in selection if unit.relative_file.endswith("api.texi")
    }
    missing_nodes = nodes - selected_nodes
    missing_api = api_keys - selected_api
    if missing_nodes or missing_api:
        raise ValueError(
            f"pilot manifest mismatch: missing nodes={sorted(missing_nodes)}, "
            f"missing api_keys={sorted(missing_api)}"
        )
    if not selection:
        raise ValueError("pilot selection is empty")


def validate_pilot_files(
    source_root: Path,
    manifest: Mapping[str, object],
    *,
    include_files: Sequence[str],
) -> None:
    """Validate every source, include, code and image path pinned by the pilot."""

    source_root = Path(source_root).resolve()
    closure = {str(value) for value in manifest.get("include_closure", [])}
    expected_paths = (
        {str(value) for value in manifest.get("source_files", [])}
        | closure
        | {str(value) for value in manifest.get("code_files", [])}
        | {str(value) for value in manifest.get("image_files", [])}
    )
    missing = sorted(path for path in expected_paths if not (source_root / path).is_file())
    if missing:
        raise ValueError(f"pilot manifest paths are missing: {missing}")
    actual_includes = set(include_files)
    if actual_includes != closure:
        raise ValueError(
            "pilot include closure mismatch: "
            f"missing={sorted(closure - actual_includes)}, "
            f"unexpected={sorted(actual_includes - closure)}"
        )
