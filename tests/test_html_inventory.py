import json
import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.html_inventory import collect_html_inventory, write_html_inventory


class HtmlInventoryTests(unittest.TestCase):
    def test_collects_sorted_pages_and_anchors_and_checks_internal_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide").mkdir()
            (root / "index.html").write_text(
                '<h1 id="Top">Top</h1><a href="guide/start.html#Start">Start</a>',
                encoding="utf-8",
            )
            (root / "guide" / "start.html").write_text(
                '<a name="Start"></a><a href="../index.html#Top">Top</a>',
                encoding="utf-8",
            )

            report = collect_html_inventory(root)

            self.assertEqual(report["html_file_count"], 2)
            self.assertEqual(report["anchor_count"], 2)
            self.assertEqual(
                report["pages"],
                [
                    {"path": "guide/start.html", "anchors": ["Start"]},
                    {"path": "index.html", "anchors": ["Top"]},
                ],
            )
            self.assertEqual(report["broken_internal_links"], [])
            self.assertEqual(report["duplicate_anchors"], [])

    def test_reports_missing_pages_fragments_and_duplicate_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                """
                <div id="same"></div><span id="same"></span>
                <a href="missing.html">missing page</a>
                <a href="#absent">missing fragment</a>
                <a href="https://example.com/#external">external</a>
                """,
                encoding="utf-8",
            )

            report = collect_html_inventory(root)

            self.assertEqual(report["duplicate_anchors"], [{"page": "index.html", "anchor": "same"}])
            self.assertEqual(
                report["broken_internal_links"],
                [
                    {
                        "source": "index.html",
                        "href": "#absent",
                        "target": "index.html",
                        "fragment": "absent",
                        "reason": "missing-fragment",
                    },
                    {
                        "source": "index.html",
                        "href": "missing.html",
                        "target": "missing.html",
                        "fragment": "",
                        "reason": "missing-page",
                    },
                ],
            )

    def test_writes_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text('<div id="Top"></div>', encoding="utf-8")
            output = root / "inventory.json"

            report = write_html_inventory(root, output)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            self.assertTrue(output.read_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
