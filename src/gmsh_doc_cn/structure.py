"""Canonical structure and external-file inventories for Texinfo sources."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class StructureRecord:
    kind: str
    relative_file: str
    node: str
    value: str


@dataclass(frozen=True)
class ExternalFile:
    relative_file: str
    sha256: str
    size: int


@dataclass(frozen=True)
class StructureSnapshot:
    records: tuple[StructureRecord, ...]
    external_files: tuple[ExternalFile, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "records": [asdict(record) for record in self.records],
            "external_files": [asdict(item) for item in self.external_files],
        }


@dataclass(frozen=True)
class StructureComparison:
    passed: bool
    changed_kinds: tuple[str, ...]
    differences: tuple[dict[str, str | None], ...]


def scan_structure(source_root: Path) -> StructureSnapshot:
    source_root = Path(source_root).resolve()
    texinfo_root = source_root / "doc" / "texinfo"
    if not texinfo_root.is_dir():
        raise FileNotFoundError(f"missing Texinfo directory: {texinfo_root}")

    records: list[StructureRecord] = []
    external_paths: set[Path] = set()
    paths = sorted(texinfo_root.glob("*.texi"), key=lambda item: item.name.encode("utf-8"))
    inline = re.compile(r"@(anchor|ref|pxref|xref|image)\{([^{}]*?)\}", re.DOTALL)
    line_command = re.compile(
        r"^@(node|include|verbatiminclude|cindex|findex|vindex|kindex|pindex|tindex)\s+(.+?)\r?$",
        re.MULTILINE,
    )
    for path in paths:
        relative = path.relative_to(source_root).as_posix()
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        node = "Top"
        events: list[tuple[int, int, str, str]] = []
        for match in line_command.finditer(text):
            kind, value = match.groups()
            events.append((match.start(), 0, kind, value.strip()))
        for match in inline.finditer(text):
            kind, value = match.groups()
            events.append((match.start(), 1, kind, value.strip()))
        for _, _, kind, value in sorted(events):
            value = value.strip()
            if kind == "node":
                node = value.split(",", 1)[0].strip()
            records.append(StructureRecord(kind, relative, node, value))
            if kind == "verbatiminclude":
                external_paths.add((path.parent / value).resolve())

    external: list[ExternalFile] = []
    for path in sorted(
        external_paths,
        key=lambda item: item.relative_to(source_root).as_posix().encode("utf-8"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"missing @verbatiminclude target: {path}")
        external.append(
            ExternalFile(
                relative_file=path.relative_to(source_root).as_posix(),
                sha256=_sha256(path),
                size=path.stat().st_size,
            )
        )
    return StructureSnapshot(tuple(records), tuple(external))


def compare_structures(
    expected: StructureSnapshot, actual: StructureSnapshot
) -> StructureComparison:
    changed: list[str] = []
    differences: list[dict[str, str | None]] = []
    if expected.records != actual.records:
        changed.append("records")
        limit = min(len(expected.records), len(actual.records))
        for index in range(limit):
            source = expected.records[index]
            translated = actual.records[index]
            if source != translated:
                differences.append(
                    {
                        "kind": "records",
                        "relative_file": source.relative_file,
                        "node": source.node,
                        "expected": json.dumps(asdict(source), ensure_ascii=False, sort_keys=True),
                        "actual": json.dumps(
                            asdict(translated), ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
                break
        if len(expected.records) != len(actual.records):
            differences.append(
                {
                    "kind": "record-count",
                    "relative_file": None,
                    "node": None,
                    "expected": str(len(expected.records)),
                    "actual": str(len(actual.records)),
                }
            )
    if expected.external_files != actual.external_files:
        changed.append("external_files")
        differences.append(
            {
                "kind": "external-files",
                "relative_file": None,
                "node": None,
                "expected": json.dumps(
                    [asdict(item) for item in expected.external_files],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "actual": json.dumps(
                    [asdict(item) for item in actual.external_files],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
    return StructureComparison(not changed, tuple(changed), tuple(differences))


def write_structure_snapshot(snapshot: StructureSnapshot, output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
