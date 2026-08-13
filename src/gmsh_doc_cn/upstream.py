from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FetchResult:
    path: Path
    sha256: str
    downloaded_from_network: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_local_path(path: Path, label: str) -> Path:
    """Return an absolute path only when no existing component is a symlink."""

    absolute = Path(path).absolute()
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise ValueError(f"{label} must not contain a symbolic link: {candidate}")
    return absolute


def _tree_sha256(root: Path) -> str:
    """Hash every regular file path and byte stream below one extracted root."""

    if root.is_symlink():
        raise ValueError(f"extracted source root must not be a symbolic link: {root}")
    digest = hashlib.sha256()
    entries = list(root.rglob("*"))
    symbolic_links = [path for path in entries if path.is_symlink()]
    if symbolic_links:
        raise ValueError(f"extracted source contains a symbolic link: {symbolic_links[0]}")
    files = sorted(
        (path for path in entries if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _validated_archive_members(
    package: tarfile.TarFile, expected_root: str
) -> list[tarfile.TarInfo]:
    """Return archive members only after validating their complete path and type set."""

    members = package.getmembers()
    names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        canonical_name = path.as_posix()
        if (
            path.is_absolute()
            or "\\" in member.name
            or any(":" in part for part in path.parts)
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != expected_root
            or canonical_name in names
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"unsafe archive member: {member.name}")
        names.add(canonical_name)
    return members


def _archive_tree_sha256(
    package: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    expected_root: str,
) -> str:
    """Hash the canonical regular-file tree directly from the verified archive bytes."""

    root = PurePosixPath(expected_root)
    files = sorted(
        (member for member in members if member.isfile()),
        key=lambda member: PurePosixPath(member.name)
        .relative_to(root)
        .as_posix()
        .encode("utf-8"),
    )
    digest = hashlib.sha256()
    for member in files:
        relative = PurePosixPath(member.name).relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(member.size.to_bytes(8, "big"))
        stream = package.extractfile(member)
        if stream is None:
            raise ValueError(f"cannot read regular archive member: {member.name}")
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def fetch_upstream(manifest_path: Path, cache_directory: Path) -> FetchResult:
    """Fetch the pinned archive and reject any byte mismatch."""

    manifest_path = Path(manifest_path)
    cache_directory = _safe_local_path(Path(cache_directory), "upstream cache directory")
    with manifest_path.open("rb") as stream:
        upstream = tomllib.load(stream)["upstream"]

    url = str(upstream["archive_url"])
    expected = str(upstream["archive_sha256"]).lower()
    archive_name = str(upstream["archive_name"])
    if (
        not archive_name
        or archive_name in {".", ".."}
        or "/" in archive_name
        or "\\" in archive_name
        or Path(archive_name).name != archive_name
    ):
        raise ValueError("upstream archive_name must be one plain file name")
    destination = cache_directory / archive_name
    cache_directory.mkdir(parents=True, exist_ok=True)

    if destination.is_symlink():
        raise ValueError("cached upstream archive must not be a symbolic link")
    if destination.exists():
        if not destination.is_file():
            raise ValueError("cached upstream archive must be a regular file")
        actual = _sha256(destination)
        if actual != expected:
            raise ValueError(
                "cached upstream archive SHA-256 mismatch: "
                f"expected {expected}, got {actual}; remove the exact cache file before refetching"
            )
        return FetchResult(destination, expected, False)

    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(
            "partial download path already exists; remove that exact file before retrying"
        )
    parsed = urllib.parse.urlparse(url)
    downloaded_from_network = parsed.scheme not in {"", "file"}
    try:
        with urllib.request.urlopen(url) as response, temporary.open("xb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = _sha256(temporary)
        if actual != expected:
            raise ValueError(
                f"upstream SHA-256 mismatch: expected {expected}, got {actual}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return FetchResult(destination, expected, downloaded_from_network)


def extract_upstream(
    archive_path: Path, destination: Path, expected_root: str
) -> Path:
    """Safely extract one pinned source archive and return its release root."""

    if (
        not expected_root
        or expected_root in {".", ".."}
        or "/" in expected_root
        or "\\" in expected_root
        or Path(expected_root).name != expected_root
    ):
        raise ValueError("expected_root must be one plain directory name")
    archive_path = _safe_local_path(Path(archive_path), "upstream archive path")
    destination = _safe_local_path(Path(destination), "extraction destination")
    expected = destination / expected_root
    marker = destination / ".archive-sha256"
    archive_hash = _sha256(archive_path)
    if expected.is_symlink():
        raise ValueError("existing extracted source root must not be a symbolic link")
    if marker.is_symlink():
        raise ValueError("archive marker must not be a symbolic link")
    with tarfile.open(archive_path, "r:gz") as package:
        members = _validated_archive_members(package, expected_root)
        archive_tree_hash = _archive_tree_sha256(package, members, expected_root)
        if expected.is_dir():
            entrypoint = expected / "doc" / "texinfo" / "gmsh.texi"
            if not entrypoint.is_file():
                raise ValueError("existing extracted source has no Texinfo entrypoint")
            actual_tree_hash = _tree_sha256(expected)
            if actual_tree_hash != archive_tree_hash:
                raise ValueError(
                    "existing extracted source tree does not match the verified archive tree hash"
                )
            state = {
                "schema_version": 1,
                "archive_sha256": archive_hash,
                "tree_sha256": archive_tree_hash,
            }
            marker.write_text(
                json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n",
                encoding="ascii",
                newline="\n",
            )
            return expected
        if destination.exists():
            if not destination.is_dir():
                raise ValueError(f"extraction destination is not a directory: {destination}")
            if any(destination.iterdir()):
                raise FileExistsError(f"extraction directory is not empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        package.extractall(destination, members=members, filter="data")
    if not expected.is_dir():
        raise ValueError(f"archive does not contain expected root: {expected_root}")
    actual_tree_hash = _tree_sha256(expected)
    if actual_tree_hash != archive_tree_hash:
        raise ValueError("extracted source tree does not match the verified archive tree hash")
    state = {
        "schema_version": 1,
        "archive_sha256": archive_hash,
        "tree_sha256": archive_tree_hash,
    }
    marker.write_text(
        json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return expected
