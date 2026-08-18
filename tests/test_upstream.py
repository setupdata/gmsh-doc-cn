import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.upstream import _tree_sha256, extract_upstream, fetch_upstream


class FetchUpstreamTests(unittest.TestCase):
    def test_fetches_local_archive_and_verifies_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tgz"
            source.write_bytes(b"fixed upstream archive")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.toml"
            manifest.write_text(
                "\n".join(
                    [
                        '[upstream]',
                        'version = "4.15.2"',
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{digest}"',
                        'archive_name = "gmsh-4.15.2-source.tgz"',
                    ]
                ),
                encoding="utf-8",
            )

            result = fetch_upstream(manifest, root / "cache")

            self.assertEqual(result.sha256, digest)
            self.assertEqual(result.path.read_bytes(), source.read_bytes())
            self.assertFalse(result.downloaded_from_network)

    def test_rejects_archive_with_unexpected_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tgz"
            source.write_bytes(b"tampered")
            manifest = root / "manifest.toml"
            manifest.write_text(
                "\n".join(
                    [
                        '[upstream]',
                        'version = "4.15.2"',
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{"0" * 64}"',
                        'archive_name = "gmsh-4.15.2-source.tgz"',
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                fetch_upstream(manifest, root / "cache")

    def test_rejects_invalid_cached_archive_instead_of_silently_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tgz"
            source.write_bytes(b"verified")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.toml"
            manifest.write_text(
                "\n".join(
                    [
                        "[upstream]",
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{digest}"',
                        'archive_name = "gmsh.tgz"',
                    ]
                ),
                encoding="utf-8",
            )
            cache = root / "cache"
            cache.mkdir()
            (cache / "gmsh.tgz").write_bytes(b"tampered cached file")

            with self.assertRaisesRegex(ValueError, "cached upstream archive"):
                fetch_upstream(manifest, cache)

    def test_rejects_archive_names_that_escape_the_cache_and_stale_part_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tgz"
            source.write_bytes(b"archive")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.toml"
            manifest.write_text(
                "\n".join(
                    [
                        "[upstream]",
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{digest}"',
                        'archive_name = "../outside.tgz"',
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "plain file name"):
                fetch_upstream(manifest, root / "cache")
            self.assertFalse((root / "outside.tgz").exists())

            manifest.write_text(
                "\n".join(
                    [
                        "[upstream]",
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{digest}"',
                        'archive_name = "fixed.tgz"',
                    ]
                ),
                encoding="utf-8",
            )
            cache = root / "cache"
            cache.mkdir(exist_ok=True)
            (cache / "fixed.tgz.part").write_bytes(b"do not overwrite")
            with self.assertRaisesRegex(ValueError, "partial download"):
                fetch_upstream(manifest, cache)
            self.assertEqual((cache / "fixed.tgz.part").read_bytes(), b"do not overwrite")

    def test_rejects_symlinked_cache_and_extraction_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tgz"
            with tarfile.open(source, "w:gz") as package:
                content = b"@node Top\n"
                entry = tarfile.TarInfo("gmsh-4.15.2/doc/texinfo/gmsh.texi")
                entry.size = len(content)
                package.addfile(entry, io.BytesIO(content))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.toml"
            manifest.write_text(
                "\n".join(
                    [
                        "[upstream]",
                        f'archive_url = "{source.as_uri()}"',
                        f'archive_sha256 = "{digest}"',
                        'archive_name = "fixed.tgz"',
                    ]
                ),
                encoding="utf-8",
            )
            outside_cache = root / "outside-cache"
            outside_cache.mkdir()
            linked_cache = root / "linked-cache"
            outside_extract = root / "outside-extract"
            outside_extract.mkdir()
            linked_extract = root / "linked-extract"
            try:
                os.symlink(outside_cache, linked_cache, target_is_directory=True)
                os.symlink(outside_extract, linked_extract, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ValueError, "cache directory"):
                fetch_upstream(manifest, linked_cache)
            self.assertEqual(list(outside_cache.iterdir()), [])
            with self.assertRaisesRegex(ValueError, "extraction destination"):
                extract_upstream(source, linked_extract, "gmsh-4.15.2")
            self.assertEqual(list(outside_extract.iterdir()), [])

    def test_rejects_unsafe_expected_root_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tgz"
            with tarfile.open(archive, "w:gz"):
                pass
            with self.assertRaisesRegex(ValueError, "expected_root"):
                extract_upstream(archive, root / "out", "../outside")

    def test_extracts_expected_release_root_and_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tgz"
            with tarfile.open(archive, "w:gz") as package:
                content = b"@node Top\n"
                entry = tarfile.TarInfo("gmsh-4.15.2/doc/texinfo/gmsh.texi")
                entry.size = len(content)
                package.addfile(entry, io.BytesIO(content))
            extracted = extract_upstream(archive, root / "out", "gmsh-4.15.2")
            self.assertEqual(
                (extracted / "doc" / "texinfo" / "gmsh.texi").read_bytes(),
                b"@node Top\n",
            )
            self.assertTrue((root / "out" / ".archive-sha256").is_file())
            self.assertEqual(
                extract_upstream(archive, root / "out", "gmsh-4.15.2"),
                extracted,
            )
            (extracted / "doc" / "texinfo" / "gmsh.texi").write_bytes(b"tampered\n")
            with self.assertRaisesRegex(ValueError, "tree hash"):
                extract_upstream(archive, root / "out", "gmsh-4.15.2")

            marker = root / "out" / ".archive-sha256"
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "tree_sha256": _tree_sha256(extracted),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "verified archive"):
                extract_upstream(archive, root / "out", "gmsh-4.15.2")

            (extracted / "doc" / "texinfo" / "gmsh.texi").write_bytes(b"@node Top\n")
            marker.unlink()
            # Re-extract into a clean destination before testing a directory link.
            clean = root / "clean"
            extracted = extract_upstream(archive, clean, "gmsh-4.15.2")
            outside = root / "outside-directory"
            outside.mkdir()
            link = extracted / "external-link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    extract_upstream(archive, clean, "gmsh-4.15.2")

            linked_destination = root / "linked-destination"
            linked_destination.mkdir()
            outside_root = root / "outside-root"
            outside_root.mkdir()
            try:
                os.symlink(
                    outside_root,
                    linked_destination / "gmsh-4.15.2",
                    target_is_directory=True,
                )
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(ValueError, "source root"):
                    extract_upstream(archive, linked_destination, "gmsh-4.15.2")

            bad = root / "bad.tgz"
            with tarfile.open(bad, "w:gz") as package:
                entry = tarfile.TarInfo("../outside")
                entry.size = 1
                package.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                extract_upstream(bad, root / "bad-out", "gmsh-4.15.2")

            bad_backslash = root / "bad-backslash.tgz"
            with tarfile.open(bad_backslash, "w:gz") as package:
                entry = tarfile.TarInfo("gmsh-4.15.2\\..\\outside")
                entry.size = 1
                package.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                extract_upstream(
                    bad_backslash, root / "bad-backslash-out", "gmsh-4.15.2"
                )


if __name__ == "__main__":
    unittest.main()
