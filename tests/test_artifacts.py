import json
import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.artifacts import write_artifact_manifest
from gmsh_doc_cn.schema_validation import validate_schema


class ArtifactManifestTests(unittest.TestCase):
    def test_manifest_is_sorted_and_excludes_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z.txt").write_text("last", encoding="utf-8")
            nested = root / "a"
            nested.mkdir()
            (nested / "first.txt").write_text("first", encoding="utf-8")
            output = root / "artifact-manifest.json"

            first = write_artifact_manifest(root, output)
            second = write_artifact_manifest(root, output)

            self.assertEqual(first, second)
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["path"] for entry in parsed["files"]],
                ["a/first.txt", "z.txt"],
            )
            self.assertEqual(parsed["schema_version"], 1)
            self.assertEqual(parsed["file_count"], 2)
            self.assertEqual(parsed["files"][0]["size"], 5)
            self.assertEqual(
                parsed["files"][0]["sha256"],
                "a7937b64b8caa58f03721bb6bacf5c78cb235febe"
                "0e70b1b84cd99541461a08e",
            )
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "schemas/artifact-manifest.schema.json"
                ).read_text(encoding="utf-8")
            )
            validate_schema(parsed, schema)


if __name__ == "__main__":
    unittest.main()
