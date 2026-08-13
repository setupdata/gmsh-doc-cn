import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from gmsh_doc_cn.build import build_english_site


class BuildEnglishSiteTests(unittest.TestCase):
    def test_builds_split_single_and_images_under_versioned_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            images = texinfo / "images"
            images.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text("@node Top\n", encoding="utf-8")
            (images / "t1.png").write_bytes(b"png")
            fake = root / "fake_makeinfo.py"
            fake.write_text(
                textwrap.dedent(
                    """
                    import os
                    import sys
                    from pathlib import Path

                    args = sys.argv[1:]
                    output = Path(args[args.index('--output') + 1])
                    stamp = os.environ['SOURCE_DATE_EPOCH']
                    print("gmsh.texi:104: warning: sample warning", file=sys.stderr)
                    print(
                        "Possible precedence problem at /usr/share/texinfo/Texinfo/Convert/LaTeX.pm line 1068.",
                        file=sys.stderr,
                    )
                    if '--no-split' in args:
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(f'<html data-epoch="{stamp}">single</html>\\n', encoding='utf-8')
                    else:
                        output.mkdir(parents=True, exist_ok=True)
                        (output / 'index.html').write_text(f'<html data-epoch="{stamp}">split</html>\\n', encoding='utf-8')
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            result = build_english_site(
                source,
                root / "dist",
                version="4.15.2",
                source_date_epoch=1774339200,
                makeinfo_command=[sys.executable, str(fake)],
            )

            public_root = root / "dist" / "v4.15.2" / "en"
            self.assertEqual(result.public_root, public_root)
            self.assertTrue((public_root / "index.html").is_file())
            self.assertTrue((public_root / "gmsh.html").is_file())
            self.assertEqual((public_root / "images" / "t1.png").read_bytes(), b"png")
            self.assertIn("1774339200", (public_root / "gmsh.html").read_text())
            self.assertEqual(
                result.warning_fingerprints,
                (
                    "GNU Texinfo|Texinfo/Convert/LaTeX.pm|Possible precedence problem",
                    "GNU Texinfo|doc/texinfo/gmsh.texi|sample warning",
                ),
            )

    def test_rejects_non_empty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "source" / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text("@node Top\n", encoding="utf-8")
            occupied = root / "dist" / "v4.15.2" / "en"
            occupied.mkdir(parents=True)
            (occupied / "stale.html").write_text("stale", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "not empty"):
                build_english_site(
                    root / "source",
                    root / "dist",
                    version="4.15.2",
                    source_date_epoch=1774339200,
                    makeinfo_command=[sys.executable],
                )


if __name__ == "__main__":
    unittest.main()
