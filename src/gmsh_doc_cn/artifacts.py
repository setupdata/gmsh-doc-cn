from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _digest(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def write_artifact_manifest(root: Path, output: Path) -> dict[str, object]:
    """Write a canonical inventory for every published byte below *root*."""

    root = Path(root).resolve()
    output = Path(output).resolve()
    entries: list[dict[str, object]] = []
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )
    for path in paths:
        if path == output:
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(
            {"path": relative, "size": path.stat().st_size, "sha256": _digest(path)}
        )

    document: dict[str, object] = {
        "schema_version": 1,
        "algorithm": "sha256-raw-bytes-v1",
        "file_count": len(entries),
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return document
