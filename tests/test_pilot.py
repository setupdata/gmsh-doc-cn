import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import extract_tree
from gmsh_doc_cn.pilot import select_pilot_units, validate_pilot_files, validate_pilot_manifest


class PilotTests(unittest.TestCase):
    def test_selects_declared_nodes_and_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@node Overview of Gmsh\n@chapter Overview of Gmsh\n\nOverview text.\n"
                "@node t1\n@section t1\n\nTutorial text.\n",
                encoding="utf-8",
            )
            (texinfo / "api.texi").write_text(
                "@node Namespace gmsh\n@section Namespace @code{gmsh}\n"
                "@ftable @code\n@item gmsh/initialize\nInitialize the API.\n@end ftable\n",
                encoding="utf-8",
            )
            extraction = extract_tree(root)
            manifest = {
                "nodes": ["Overview of Gmsh", "t1"],
                "api_keys": ["gmsh/initialize"],
            }
            selection = select_pilot_units(extraction.units, manifest)
            validate_pilot_manifest(selection, manifest)
            self.assertEqual({unit.node for unit in selection if unit.relative_file.endswith("gmsh.texi")}, {"Overview of Gmsh", "t1"})
            self.assertTrue(any(unit.semantic_key == "gmsh/initialize" for unit in selection))

    def test_validates_declared_source_and_include_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("doc/texinfo/gmsh.texi", "doc/texinfo/part.texi", "image.png"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            manifest = {
                "source_files": ["doc/texinfo/gmsh.texi"],
                "include_closure": ["doc/texinfo/part.texi"],
                "image_files": ["image.png"],
            }
            validate_pilot_files(
                root,
                manifest,
                include_files=["doc/texinfo/part.texi"],
            )
            with self.assertRaisesRegex(ValueError, "include closure"):
                validate_pilot_files(root, manifest, include_files=[])


if __name__ == "__main__":
    unittest.main()
