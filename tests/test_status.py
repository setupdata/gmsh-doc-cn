import unittest

from gmsh_doc_cn.catalog import TranslationUnit
from gmsh_doc_cn.status import candidate_hash, reduce_status


def unit() -> TranslationUnit:
    return TranslationUnit(
        unit_id="unit-1",
        msgctxt="file|Top|paragraph|key",
        msgid="Source text.",
        relative_file="doc/texinfo/gmsh.texi",
        node="Top",
        structure_path="doc/texinfo/gmsh.texi::Top",
        role="paragraph",
        semantic_key="key",
        category="ordinary_tutorial",
        subcategory="prose",
        start=0,
        end=12,
        source_hash="source",
        context_hash="context",
        protected_hash="protected",
        protected_values=(),
    )


def review(role: str, result: str, translation: str = "译文。", event: str = "event") -> dict:
    return {
        "event_id": event,
        "run_id": f"run-{role}",
        "unit_id": "unit-1",
        "role": role,
        "round": 0,
        "source_hash": "source",
        "context_hash": "context",
        "protected_hash": "protected",
        "candidate_hash": candidate_hash(translation),
        "rules_hash": "rules",
        "glossary_hash": "glossary",
        "result": result,
    }


class StatusTests(unittest.TestCase):
    def test_reduces_each_review_stage_deterministically(self) -> None:
        current = unit()
        self.assertEqual(reduce_status([current], {}, [], {})["units"][0]["state"], "untranslated")
        catalog = {current.msgctxt: "译文。"}
        self.assertEqual(reduce_status([current], catalog, [], {})["units"][0]["state"], "draft")
        language = review("language_review", "accept", event="language")
        self.assertEqual(
            reduce_status([current], catalog, [language], {})["units"][0]["state"],
            "language-reviewed",
        )
        technical = review("technical_review", "accept", event="technical")
        self.assertEqual(
            reduce_status([current], catalog, [language, technical], {})["units"][0]["state"],
            "technical-reviewed",
        )
        translation = review("translation", "accept", event="translation")
        report = reduce_status(
            [current], catalog, [translation, language, technical], {"unit-1": True}
        )
        self.assertEqual(report["units"][0]["state"], "formal")
        self.assertEqual(report["counts"], {"formal": 1})

        missing_translation = reduce_status(
            [current], catalog, [language, technical], {"unit-1": True}
        )
        self.assertEqual(missing_translation["units"][0]["state"], "technical-reviewed")

    def test_hash_mismatch_is_stale_and_conflicts_fail(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        stale = review("language_review", "accept") | {"source_hash": "old"}
        self.assertEqual(
            reduce_status([current], catalog, [stale], {})["units"][0]["state"],
            "stale",
        )
        first = review("language_review", "accept", event="one")
        second = review("language_review", "revise", event="two")
        with self.assertRaisesRegex(ValueError, "conflicting review"):
            reduce_status([current], catalog, [first, second], {})

        duplicate = review("language_review", "accept", event="three")
        with self.assertRaisesRegex(ValueError, "duplicate review"):
            reduce_status([current], catalog, [first, duplicate], {})

    def test_obsolete_or_unrecognised_gettext_flags_are_rejected(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        with self.assertRaisesRegex(ValueError, "unsupported gettext flag"):
            reduce_status(
                [current], catalog, [], {}, flags={current.msgctxt: {"python-format"}}
            )
        with self.assertRaisesRegex(ValueError, "obsolete"):
            reduce_status(
                [current], catalog, [], {}, flags={current.msgctxt: {"obsolete"}}
            )
        with self.assertRaisesRegex(ValueError, "unknown msgctxt"):
            reduce_status([current], {"unknown": "译文"}, [], {})
        with self.assertRaisesRegex(ValueError, "unknown unit_id"):
            reduce_status([current], catalog, [], {"unknown": True})
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            reduce_status([current], catalog, [], {"unit-1": "yes"})
        with self.assertRaisesRegex(ValueError, "PO msgid does not match"):
            reduce_status(
                [current],
                catalog,
                [],
                {},
                catalog_msgids={current.msgctxt: "Old source text."},
            )

    def test_revision_event_is_provenance_not_a_review_gate(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        translation = review(
            "translation", "accept", translation="旧译文。", event="translation"
        )
        revision = review("revision", "accept", event="revision")
        language = review("language_review", "accept", event="language")
        technical = review("technical_review", "accept", event="technical")
        report = reduce_status(
            [current],
            catalog,
            [translation, revision, language, technical],
            {"unit-1": True},
        )
        self.assertEqual(report["units"][0]["state"], "formal")

        without_revision = reduce_status(
            [current],
            catalog,
            [translation, language, technical],
            {"unit-1": True},
        )
        self.assertEqual(without_revision["units"][0]["state"], "technical-reviewed")

    def test_one_run_id_cannot_claim_two_independent_roles(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        language = review("language_review", "accept", event="language")
        technical = review("technical_review", "accept", event="technical")
        technical["run_id"] = language["run_id"]
        with self.assertRaisesRegex(ValueError, "shared across roles"):
            reduce_status([current], catalog, [language, technical], {})

    def test_old_candidate_reviews_do_not_poison_a_revised_candidate(self) -> None:
        current = unit()
        old = review("language_review", "revise", translation="旧译文。", event="old")
        catalog = {current.msgctxt: "新译文。"}
        report = reduce_status([current], catalog, [old], {})
        self.assertEqual(report["units"][0]["state"], "draft")

    def test_changed_rules_or_glossary_make_reviewed_translation_stale(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        language = review("language_review", "accept", event="language")
        report = reduce_status(
            [current],
            catalog,
            [language],
            {},
            current_rules_hash="new-rules",
            current_glossary_hash="glossary",
        )
        self.assertEqual(report["units"][0]["state"], "stale")

    def test_per_unit_semantic_hash_ignores_unrelated_glossary_commit_changes(self) -> None:
        current = unit()
        catalog = {current.msgctxt: "译文。"}
        translation = review("translation", "accept", event="translation")
        language = review("language_review", "accept", event="language")
        technical = review("technical_review", "accept", event="technical")
        report = reduce_status(
            [current],
            catalog,
            [translation, language, technical],
            {"unit-1": True},
            current_rules_hash="global-rules-file",
            current_rules_hashes={"unit-1": "rules"},
            current_glossary_hash="unrelated-new-glossary-commit",
        )
        self.assertEqual(report["units"][0]["state"], "formal")
        self.assertEqual(report["units"][0]["semantic_rules_hash"], "rules")

        stale = reduce_status(
            [current],
            catalog,
            [translation, language, technical],
            {"unit-1": True},
            current_rules_hashes={"unit-1": "changed-applicable-rules"},
        )
        self.assertEqual(stale["units"][0]["state"], "stale")


if __name__ == "__main__":
    unittest.main()
