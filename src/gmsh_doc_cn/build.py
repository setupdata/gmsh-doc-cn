"""Reproducible GNU Texinfo HTML builds for the pinned Gmsh source tree."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class BuildResult:
    """Paths produced by an English-manual build."""

    public_root: Path
    warning_fingerprints: tuple[str, ...]


_WARNING_RE = re.compile(r"^(?P<path>.*?):\d+(?::\d+)?: warning: (?P<message>.*)$")
_PERL_WARNING_RE = re.compile(r"^(?P<message>.+) at (?P<path>.+) line \d+\.$")


def _relative_warning_path(value: str, source_root: Path) -> str:
    normalised = value.replace("\\", "/")
    marker = "/Texinfo/"
    if marker in normalised:
        return "Texinfo/" + normalised.split(marker, 1)[1]
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(source_root).as_posix()
        except ValueError:
            return candidate.name
    if "/" in normalised:
        return normalised.lstrip("./")
    return f"doc/texinfo/{normalised}"


def _warning_fingerprints(stderr: str, source_root: Path) -> tuple[str, ...]:
    values: set[str] = set()
    for line in stderr.splitlines():
        match = _WARNING_RE.match(line.strip())
        if match is None:
            match = _PERL_WARNING_RE.match(line.strip())
        if match is None:
            continue
        filename = _relative_warning_path(match.group("path"), source_root)
        message = match.group("message").replace("`", "'")
        values.add(f"GNU Texinfo|{filename}|{message}")
    return tuple(sorted(values))


def build_english_site(
    source_root: Path,
    dist_root: Path,
    *,
    version: str,
    source_date_epoch: int,
    makeinfo_command: Sequence[str] = ("makeinfo",),
) -> BuildResult:
    """Build split and single-page English HTML below a versioned URL root."""

    source_root = Path(source_root).resolve()
    texinfo_root = source_root / "doc" / "texinfo"
    entrypoint = texinfo_root / "gmsh.texi"
    if not entrypoint.is_file():
        raise FileNotFoundError(f"missing Texinfo entrypoint: {entrypoint}")
    if not makeinfo_command:
        raise ValueError("makeinfo_command must not be empty")

    public_root = Path(dist_root).resolve() / f"v{version}" / "en"
    if public_root.exists() and any(public_root.iterdir()):
        raise FileExistsError(f"English build output directory is not empty: {public_root}")
    public_root.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment.update(
        {
            "SOURCE_DATE_EPOCH": str(source_date_epoch),
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "PYTHONHASHSEED": "0",
        }
    )

    common = [*makeinfo_command, "--html"]
    split = subprocess.run(
        [*common, "--split=node", "--output", str(public_root), "gmsh.texi"],
        cwd=texinfo_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    single = subprocess.run(
        [
            *common,
            "--no-split",
            "--output",
            str(public_root / "gmsh.html"),
            "gmsh.texi",
        ],
        cwd=texinfo_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    images = texinfo_root / "images"
    if images.is_dir():
        shutil.copytree(images, public_root / "images", dirs_exist_ok=True)

    warnings = _warning_fingerprints(split.stderr + "\n" + single.stderr, source_root)
    return BuildResult(public_root=public_root, warning_fingerprints=warnings)
