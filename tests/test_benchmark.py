import hashlib
import unittest

from gmsh_doc_cn.benchmark import (
    DEFAULT_QUOTAS,
    _eligible,
    qualification_report,
    select_benchmark,
    select_smoke_benchmark,
    validate_smoke_candidates,
)
from gmsh_doc_cn.catalog import TranslationUnit


def make_unit(subcategory: str, number: int) -> TranslationUnit:
    category = {
        "prose": "ordinary_tutorial",
        "tutorial": "ordinary_tutorial",
        "scripting": "script_api",
        "api": "script_api",
        "options": "option_field_plugin",
        "fields": "option_field_plugin",
        "plugins": "option_field_plugin",
        "formats": "formats_compile_faq",
        "compile": "formats_compile_faq",
        "faq": "formats_compile_faq",
    }[subcategory]
    identity = f"{subcategory}-{number:03d}"
    words = [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen", "twenty", "twentyone", "twentytwo", "twentythree",
        "twentyfour", "twentyfive", "twentysix", "twentyseven", "twentyeight", "twentynine",
    ]
    word = words[number] if number < len(words) else f"specialcase{chr(97 + number % 26)}"
    msgid = f"Representative natural language sentence for case {subcategory} {word}."
    msgctxt = f"doc/texinfo/{subcategory}.texi|Node {subcategory}|paragraph|{identity}"
    protected_serialised = b"[]"
    unit_id = hashlib.sha256(
        f"gmsh-cn-unit-v1\0{msgctxt}\0{msgid}".encode("utf-8")
    ).hexdigest()
    return TranslationUnit(
        unit_id=unit_id,
        msgctxt=msgctxt,
        msgid=msgid,
        relative_file=f"doc/texinfo/{subcategory}.texi",
        node=f"Node {subcategory}",
        structure_path=f"doc/texinfo/{subcategory}.texi::Node {subcategory}",
        role="paragraph",
        semantic_key=identity,
        category=category,
        subcategory=subcategory,
        start=0,
        end=1,
        source_hash=hashlib.sha256(msgid.encode("utf-8") + b"\0" + protected_serialised).hexdigest(),
        context_hash=hashlib.sha256(msgctxt.encode("utf-8")).hexdigest(),
        protected_hash=hashlib.sha256(protected_serialised).hexdigest(),
        protected_values=(),
    )


class BenchmarkTests(unittest.TestCase):
    def test_selects_exact_stratified_sample_independent_of_input_order(self) -> None:
        units = [
            make_unit(subcategory, number)
            for subcategory, quota in DEFAULT_QUOTAS.items()
            for number in range(quota + 3)
        ]
        forward = select_benchmark(units)
        reverse = select_benchmark(reversed(units))
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward), 100)
        self.assertEqual(forward[0]["benchmark_id"], "bmk-v1-001")

        report = qualification_report(forward, pot_sha256="pot", manifest_sha256="manifest")
        self.assertEqual(report["qualification_state"], "selection_qualified_pending_review")
        self.assertEqual(report["ai_generation"]["ai_calls"], 0)
        self.assertFalse(report["publish_eligible"])

        corrupted = [dict(record) for record in forward]
        corrupted[0]["protected_hash"] = "0" * 64
        failed = qualification_report(corrupted, pot_sha256="pot", manifest_sha256="manifest")
        self.assertFalse(failed["selection_checks"]["protected_hash_valid"])
        self.assertEqual(failed["qualification_state"], "selection_failed")

    def test_quota_shortfall_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "quota shortfall"):
            select_benchmark([])

    def test_excludes_protected_only_units_and_selects_a_mixed_smoke_set(self) -> None:
        units = [
            make_unit(subcategory, number)
            for subcategory, quota in DEFAULT_QUOTAS.items()
            for number in range(quota + 3)
        ]
        protected = make_unit("prose", 999)
        protected = TranslationUnit(
            **{
                **protected.__dict__,
                "msgid": "@image{images/example,10cm,,Example image}",
                "protected_values": ("@image{images/example,10cm,,Example image}",),
            }
        )
        selection = select_benchmark([protected, *units])
        self.assertNotIn(protected.unit_id, {row["unit_id"] for row in selection})

        smoke = select_smoke_benchmark(selection)
        self.assertEqual(len(smoke), 5)
        self.assertEqual(
            [row["subcategory"] for row in smoke],
            ["prose", "tutorial", "scripting", "api", "formats"],
        )
        self.assertTrue(all(row["role"] == "paragraph" for row in smoke))

    def test_excludes_generated_api_boilerplate(self) -> None:
        units = [
            make_unit(subcategory, number)
            for subcategory, quota in DEFAULT_QUOTAS.items()
            for number in range(quota + 3)
        ]
        boilerplate = make_unit("api", 999)
        boilerplate = TranslationUnit(
            **{
                **boilerplate.__dict__,
                "msgid": "Return:",
                "role": "item",
                "semantic_key": "Return:#99",
            }
        )
        selected = select_benchmark([boilerplate, *units])
        self.assertNotIn(boilerplate.unit_id, {row["unit_id"] for row in selected})

    def test_excludes_tutorial_link_directory_boilerplate(self) -> None:
        units = [
            make_unit(subcategory, number)
            for subcategory, quota in DEFAULT_QUOTAS.items()
            for number in range(quota + 3)
        ]
        links = make_unit("tutorial", 999)
        links = TranslationUnit(
            **{
                **links.__dict__,
                "msgid": "See\n@url{https://example.invalid/t1.geo,t1.geo}.\nAlso available in C++.",
            }
        )
        selected = select_benchmark([links, *units])
        self.assertNotIn(links.unit_id, {row["unit_id"] for row in selected})

    def test_excludes_standalone_image_alt_text_from_the_model_benchmark(self) -> None:
        image = make_unit("prose", 999)
        image = TranslationUnit(
            **{
                **image.__dict__,
                "msgid": "@center@image{images/t1,9cm,,Screenshot of tutorial t1}",
                "protected_values": ("@center", "@image{images/t1,9cm,,", "}"),
            }
        )
        self.assertFalse(_eligible(image))

    def test_excludes_index_and_trivial_generated_fragments(self) -> None:
        units = [
            make_unit(subcategory, number)
            for subcategory, quota in DEFAULT_QUOTAS.items()
            for number in range(quota + 3)
        ]
        index = make_unit("prose", 998)
        index = TranslationUnit(**{**index.__dict__, "role": "index", "msgid": "Mesh, background"})
        default_only = make_unit("plugins", 999)
        default_only = TranslationUnit(
            **{**default_only.__dict__, "msgid": "Default value: @code{0}"}
        )
        selected = select_benchmark([index, default_only, *units])
        selected_ids = {row["unit_id"] for row in selected}
        self.assertNotIn(index.unit_id, selected_ids)
        self.assertNotIn(default_only.unit_id, selected_ids)

    def test_validates_smoke_candidate_identity_and_protected_values(self) -> None:
        unit = make_unit("prose", 0)
        unit = TranslationUnit(
            **{
                **unit.__dict__,
                "msgid": "Open @file{model.geo}.",
                "protected_values": ("@file{model.geo}",),
            }
        )
        benchmark = select_benchmark(
            [
                make_unit(subcategory, number)
                for subcategory, quota in DEFAULT_QUOTAS.items()
                for number in range(quota + 3)
            ],
            quotas={"prose": 1},
        )
        benchmark[0] = benchmark[0] | {
            "unit_id": unit.unit_id,
            "benchmark_id": "bmk-v1-001",
            "msgid": unit.msgid,
            "protected_hash": unit.protected_hash,
        }
        candidates = [
            {
                "benchmark_id": "bmk-v1-001",
                "unit_id": unit.unit_id,
                "reference_translation": "打开 @file{model.geo}。",
            }
        ]
        report = validate_smoke_candidates(benchmark, candidates, {unit.unit_id: unit})
        self.assertTrue(report["all_passed"])
        candidates[0]["reference_translation"] = "打开 @file{other.geo}。"
        with self.assertRaisesRegex(ValueError, "protected content"):
            validate_smoke_candidates(benchmark, candidates, {unit.unit_id: unit})


if __name__ == "__main__":
    unittest.main()
