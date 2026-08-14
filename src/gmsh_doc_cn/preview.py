"""Deterministic bilingual preview builds with explicit English fallback."""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from .build import build_html_site
from .catalog import PoCatalog, TranslationUnit, apply_catalog, extract_tree, read_po_catalog
from .deterministic_checks import check_result_map, run_deterministic_checks
from .html_inventory import collect_html_inventory
from .review import read_review_batches
from .semantic_rules import semantic_rule_hashes
from .status import candidate_hash, reduce_status


_STATUS_STATES = {
    "untranslated",
    "draft",
    "language-reviewed",
    "technical-reviewed",
    "formal",
    "unresolved",
    "stale",
}
_ROBOTS_META_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*([\"'])robots\1[^>]*>", re.IGNORECASE
)
_NAV_PANEL_RE = re.compile(
    r"(?P<open><div\s+class=\"nav-panel\">)(?P<body>.*?)(?P<close></div>)",
    re.IGNORECASE | re.DOTALL,
)
_FORMAL_REFERENCE_RE = re.compile(
    "\ue000(?P<unit_id>[0-9a-f]{64})\ue001"
    "(?P<body>.*?)"
    "\ue002(?P=unit_id)\ue003",
    re.DOTALL,
)


@dataclass(frozen=True)
class PreviewBuildResult:
    """Outputs and verification counts from one bilingual preview build."""

    dist_root: Path
    english_root: Path
    preview_root: Path
    unit_count: int
    formal_unit_count: int
    fallback_unit_count: int
    english_html_file_count: int
    preview_html_file_count: int
    noindex_nofollow_page_count: int
    visible_preview_marker_page_count: int
    english_fallback_marker_page_count: int
    english_anchor_count: int
    preview_anchor_count: int
    missing_english_anchor_count: int
    preview_only_anchor_count: int
    english_duplicate_anchor_count: int
    preview_duplicate_anchor_count: int
    english_broken_internal_link_count: int
    preview_broken_internal_link_count: int
    preview_added_broken_internal_link_count: int
    site_absolute_link_count: int
    broken_site_absolute_link_count: int
    resource_link_count: int
    broken_resource_link_count: int
    english_warning_fingerprints: tuple[str, ...]
    preview_warning_fingerprints: tuple[str, ...]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _normalise_site_base(value: str) -> str:
    if not value.startswith("/") or not value.endswith("/") or "\\" in value:
        raise ValueError("SITE_BASE must start and end with '/' and use POSIX separators")
    parts = PurePosixPath(value).parts
    if ".." in parts or "//" in value:
        raise ValueError("SITE_BASE must not contain traversal or empty path components")
    return value


def _validate_version(value: str) -> str:
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", value) is None:
        raise ValueError("version must be a single safe path component")
    return value


def _set_texinfo_document_language(source_root: Path) -> None:
    entrypoint = Path(source_root) / "doc" / "texinfo" / "gmsh.texi"
    text = entrypoint.read_text(encoding="utf-8")
    if "@documentlanguage" in text:
        raise ValueError("translated Texinfo source already declares @documentlanguage")
    first_newline = text.find("\n")
    if first_newline < 0 or not text[:first_newline].startswith("\\input texinfo"):
        raise ValueError("unexpected Gmsh Texinfo preamble")
    text = text[: first_newline + 1] + "@documentlanguage zh\n" + text[first_newline + 1 :]
    entrypoint.write_text(text, encoding="utf-8", newline="\n")


def _strict_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def select_formal_catalog(
    units: Iterable[TranslationUnit],
    po: PoCatalog,
    status: Mapping[str, object],
    *,
    rules_hash: str,
    glossary_hash: str,
    semantic_rules_hashes: Mapping[str, str],
) -> dict[str, str]:
    """Return only hash-bound ``formal`` translations from a status report."""

    unit_list = list(units)
    by_id = {unit.unit_id: unit for unit in unit_list}
    by_context = {unit.msgctxt: unit for unit in unit_list}
    if len(by_id) != len(unit_list) or len(by_context) != len(unit_list):
        raise ValueError("translation units must have unique IDs and contexts")
    if _strict_integer(status.get("schema_version"), "status schema_version") != 1:
        raise ValueError("unsupported status schema_version")
    if status.get("algorithm") != "translation-status-reducer-v1":
        raise ValueError("unsupported status algorithm")
    if _strict_integer(status.get("unit_count"), "status unit_count") != len(unit_list):
        raise ValueError("status unit_count does not match current extraction")
    obsolete_count = _strict_integer(status.get("obsolete_count"), "status obsolete_count")
    if obsolete_count < 0 or obsolete_count != po.obsolete_count:
        raise ValueError("status obsolete_count does not match the current PO")

    input_hashes = _mapping(status.get("input_hashes"), "status input_hashes")
    if input_hashes.get("translation_rules") != rules_hash:
        raise ValueError("status translation-rules hash does not match current input")
    if input_hashes.get("glossary") != glossary_hash:
        raise ValueError("status glossary hash does not match current input")

    active_contexts = set(po.translations)
    if active_contexts != set(by_context):
        raise ValueError("PO active contexts do not match current extraction")
    if set(po.msgids) != set(by_context):
        raise ValueError("PO msgid contexts do not match current extraction")
    if set(semantic_rules_hashes) != set(by_id):
        raise ValueError("semantic rule hashes do not cover the current extraction")
    for context, unit in by_context.items():
        if po.msgids[context] != unit.msgid:
            raise ValueError(f"PO msgid does not match current source: {context}")

    rows = status.get("units")
    if not isinstance(rows, list) or len(rows) != len(unit_list):
        raise ValueError("status units do not cover the current extraction")
    status_by_id: dict[str, Mapping[str, object]] = {}
    states: Counter[str] = Counter()
    for raw_row in rows:
        row = _mapping(raw_row, "status unit")
        unit_id = row.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in by_id:
            raise ValueError(f"status references unknown unit_id: {unit_id}")
        if unit_id in status_by_id:
            raise ValueError(f"status contains duplicate unit_id: {unit_id}")
        unit = by_id[unit_id]
        if row.get("msgctxt") != unit.msgctxt or row.get("source_hash") != unit.source_hash:
            raise ValueError(f"status source identity mismatch for {unit_id}")
        if row.get("semantic_rules_hash") != semantic_rules_hashes[unit_id]:
            raise ValueError(f"status semantic rules hash mismatch for {unit_id}")
        translation = po.translations[unit.msgctxt]
        expected_candidate_hash = candidate_hash(translation) if translation else None
        if row.get("candidate_hash") != expected_candidate_hash:
            raise ValueError(f"status candidate hash mismatch for {unit_id}")
        state = row.get("state")
        if not isinstance(state, str) or state not in _STATUS_STATES:
            raise ValueError(f"status contains invalid state for {unit_id}")
        states[state] += 1
        status_by_id[unit_id] = row

    counts = status.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError("status counts must be an object")
    for state, count in counts.items():
        if state not in _STATUS_STATES:
            raise ValueError(f"status counts contain an invalid state: {state}")
        _strict_integer(count, f"status count for {state}")
    if dict(counts) != dict(sorted(states.items())):
        raise ValueError("status counts do not match its unit rows")

    formal: dict[str, str] = {}
    for unit in unit_list:
        row = status_by_id[unit.unit_id]
        if row["state"] != "formal":
            continue
        translation = po.translations[unit.msgctxt]
        if not translation:
            raise ValueError(f"formal unit has no translation: {unit.unit_id}")
        if "fuzzy" in po.flags.get(unit.msgctxt, set()):
            raise ValueError(f"formal unit is fuzzy: {unit.unit_id}")
        formal[unit.msgctxt] = translation
    return formal


def _derive_status(
    source_root: Path,
    po_path: Path,
    units: Sequence[TranslationUnit],
    po: PoCatalog,
    *,
    review_directory: Path,
    review_manifest_path: Path,
    check_units: Iterable[str],
    rules_path: Path,
    glossary_path: Path,
) -> tuple[dict[str, object], dict[str, str]]:
    """Re-run the formal-state gates used by the preview build."""

    reviews = read_review_batches(review_directory, review_manifest_path)
    checks = check_result_map(run_deterministic_checks(source_root, po_path, check_units))
    rules_hash = sha256_file(rules_path)
    glossary_hash = sha256_file(glossary_path)
    per_unit_rules = semantic_rule_hashes(units, rules_path, glossary_path)
    report = reduce_status(
        units,
        po.translations,
        reviews,
        checks,
        flags=po.flags,
        catalog_msgids=po.msgids,
        current_rules_hash=rules_hash,
        current_rules_hashes=per_unit_rules,
        current_glossary_hash=glossary_hash,
        obsolete_count=po.obsolete_count,
    )
    return report, per_unit_rules


def derive_preview_status(
    source_root: Path,
    po_path: Path,
    *,
    review_directory: Path,
    review_manifest_path: Path,
    check_units: Iterable[str],
    rules_path: Path,
    glossary_path: Path,
) -> dict[str, object]:
    """Derive the current status report from PO, reviews, rules and fresh checks."""

    source_root = Path(source_root).resolve()
    po_path = Path(po_path).resolve()
    extraction = extract_tree(source_root)
    po = read_po_catalog(po_path)
    report, _ = _derive_status(
        source_root,
        po_path,
        list(extraction.units),
        po,
        review_directory=Path(review_directory),
        review_manifest_path=Path(review_manifest_path),
        check_units=check_units,
        rules_path=Path(rules_path),
        glossary_path=Path(glossary_path),
    )
    return report


def _add_tag_attribute(text: str, tag: str, name: str, value: str) -> str:
    opening = re.compile(rf"<{tag}(?P<attributes>[^>]*)>", re.IGNORECASE)
    match = opening.search(text)
    if match is None:
        raise ValueError(f"generated HTML is missing <{tag}>")
    if re.search(rf"\s{re.escape(name)}\s*=", match.group("attributes"), re.IGNORECASE):
        raise ValueError(f"generated HTML already contains {name} on <{tag}>")
    replacement = (
        f"<{tag}{match.group('attributes')} "
        f'{name}="{html.escape(value, quote=True)}">'
    )
    return text[: match.start()] + replacement + text[match.end() :]


class _OrderedAnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[str] = []
        self.urls: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        if anchor := values.get("id"):
            self.anchors.append(anchor)
        if tag.casefold() == "a" and (anchor := values.get("name")):
            self.anchors.append(anchor)
        for attribute in ("href", "src"):
            if value := values.get(attribute):
                self.urls.append((tag.casefold(), attribute, value))


def _ordered_anchors(path: Path) -> list[str]:
    parser = _OrderedAnchorParser()
    parser.feed(path.read_text(encoding="utf-8", errors="strict"))
    return parser.anchors


def _verify_site_absolute_links(
    dist_root: Path, html_root: Path, site_base: str
) -> tuple[int, int]:
    """Resolve SITE_BASE links against the staged deployment tree."""

    site_base = _normalise_site_base(site_base)
    absolute_checked = 0
    resource_checked = 0
    for path in sorted(html_root.rglob("*.html")):
        parser = _OrderedAnchorParser()
        parser.feed(path.read_text(encoding="utf-8", errors="strict"))
        for tag, attribute, url in parser.urls:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or parsed.path.startswith("data:"):
                continue
            is_site_absolute = parsed.path.startswith(site_base)
            resource_suffixes = {".css", ".ico", ".png", ".svg", ".webmanifest"}
            is_resource = attribute == "src" or (
                tag == "link"
                and PurePosixPath(parsed.path).suffix.casefold() in resource_suffixes
            )
            if parsed.path.startswith("/") and not is_site_absolute:
                raise ValueError(f"site link does not use SITE_BASE in {path.name}: {url}")
            if not is_site_absolute and not is_resource:
                continue
            if is_site_absolute:
                absolute_checked += 1
                relative = unquote(parsed.path[len(site_base) :])
                target = dist_root.joinpath(*PurePosixPath(relative).parts)
            else:
                resource_checked += 1
                relative = unquote(parsed.path)
                target = (
                    path
                    if not relative and parsed.fragment
                    else path.parent.joinpath(*PurePosixPath(relative).parts).resolve()
                )
            pure = PurePosixPath(relative)
            if (not relative and not parsed.fragment) or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe internal link in {path.name}: {url}")
            try:
                target.relative_to(dist_root)
            except ValueError as error:
                raise ValueError(f"internal link escapes the site in {path.name}: {url}") from error
            if not target.is_file():
                raise ValueError(f"broken internal resource in {path.name}: {url}")
            if parsed.fragment and unquote(parsed.fragment) not in set(_ordered_anchors(target)):
                raise ValueError(f"broken SITE_BASE fragment in {path.name}: {url}")
    return absolute_checked, resource_checked


def _anchor_identity_map(english: Sequence[str], preview: Sequence[str]) -> dict[str, str]:
    """Pair translated anchor identities with missing fixed-English identities."""

    english_set = set(english)
    preview_set = set(preview)
    mapping: dict[str, str] = {}
    english_index = 0
    preview_index = 0
    while english_index < len(english) and preview_index < len(preview):
        english_anchor = english[english_index]
        preview_anchor = preview[preview_index]
        if english_anchor == preview_anchor:
            english_index += 1
            preview_index += 1
            continue
        english_missing = english_anchor not in preview_set
        preview_extra = preview_anchor not in english_set
        if english_missing and preview_extra:
            mapping[preview_anchor] = english_anchor
            english_index += 1
            preview_index += 1
        elif preview_extra:
            preview_index += 1
        else:
            raise ValueError(
                "English and Chinese preview anchors changed order without a unique mapping"
            )
    if any(
        anchor not in preview_set and anchor not in mapping.values()
        for anchor in english[english_index:]
    ):
        raise ValueError("Chinese preview is missing an English anchor identity")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("translated anchors do not map uniquely to English identities")
    return mapping


def _replace_anchor_identities(text: str, mapping: Mapping[str, str]) -> str:
    if not mapping:
        return text

    attribute = re.compile(
        r"(?P<prefix>\b(?:id|name)\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        re.IGNORECASE,
    )
    href = re.compile(
        r"(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        re.IGNORECASE,
    )
    tag = re.compile(r"<[^>]*>", re.DOTALL)

    def replace_attribute(match: re.Match[str]) -> str:
        value = mapping.get(match.group("value"), match.group("value"))
        return f"{match.group('prefix')}{match.group('quote')}{value}{match.group('quote')}"

    def replace_href(match: re.Match[str]) -> str:
        value = match.group("value")
        path, separator, fragment = value.partition("#")
        if separator and fragment in mapping:
            value = f"{path}#{mapping[fragment]}"
        return f"{match.group('prefix')}{match.group('quote')}{value}{match.group('quote')}"

    def replace_tag(match: re.Match[str]) -> str:
        rewritten = attribute.sub(replace_attribute, match.group(0))
        return href.sub(replace_href, rewritten)

    return tag.sub(replace_tag, text)


def _canonicalize_preview_anchors(english_root: Path, preview_root: Path) -> None:
    """Keep stable English anchor identities while retaining translated display text."""

    english_paths = sorted(
        path.relative_to(english_root).as_posix() for path in english_root.rglob("*.html")
    )
    preview_paths = sorted(
        path.relative_to(preview_root).as_posix() for path in preview_root.rglob("*.html")
    )
    if english_paths != preview_paths:
        raise ValueError("English and Chinese preview page paths do not match")
    combined_mapping: dict[str, str] = {}
    for relative in english_paths:
        english_anchors = _ordered_anchors(english_root / relative)
        preview_path = preview_root / relative
        preview_anchors = _ordered_anchors(preview_path)
        mapping = _anchor_identity_map(english_anchors, preview_anchors)
        for translated, fixed in mapping.items():
            previous = combined_mapping.get(translated)
            if previous is not None and previous != fixed:
                raise ValueError(
                    f"translated anchor maps to multiple English identities: {translated}"
                )
            combined_mapping[translated] = fixed
    if combined_mapping:
        for relative in preview_paths:
            preview_path = preview_root / relative
            rewritten = _replace_anchor_identities(
                preview_path.read_text(encoding="utf-8"), combined_mapping
            )
            preview_path.write_text(rewritten, encoding="utf-8", newline="\n")


def _localize_texinfo_chrome(text: str) -> str:
    """Localize deterministic makeinfo boilerplate absent from the Chinese catalog."""

    def localize_formal_reference(match: re.Match[str]) -> str:
        return re.sub(
            r"\b[Ss]ee (?=<a\s+class=\"(?:p?x)ref\")",
            "参见 ",
            match.group("body"),
        )

    text = _FORMAL_REFERENCE_RE.sub(localize_formal_reference, text)
    if any(marker in text for marker in ("\ue000", "\ue001", "\ue002", "\ue003")):
        raise ValueError("formal reference marker did not survive makeinfo as one balanced region")

    def localize_panel(match: re.Match[str]) -> str:
        body = match.group("body")
        replacements = (
            ("Next: ", "下一页："),
            ("Previous: ", "上一页："),
            ("Up: ", "上一级："),
            ('title="Table of contents"', 'title="目录"'),
            ('title="Index"', 'title="索引"'),
            (">Contents</a>", ">目录</a>"),
            (">Index</a>", ">索引</a>"),
        )
        for source, target in replacements:
            body = body.replace(source, target)
        return match.group("open") + body + match.group("close")

    return _NAV_PANEL_RE.sub(localize_panel, text)


def _mark_formal_references(
    units: Iterable[TranslationUnit], formal_catalog: Mapping[str, str]
) -> dict[str, str]:
    """Mark only accepted translations whose pxref boilerplate needs localization."""

    by_context = {unit.msgctxt: unit for unit in units}
    marked: dict[str, str] = {}
    for context, candidate in formal_catalog.items():
        unit = by_context[context]
        if "@pxref{" in candidate and unit.role not in {"heading", "index"}:
            marked[context] = (
                f"\ue000{unit.unit_id}\ue001{candidate}\ue002{unit.unit_id}\ue003"
            )
        else:
            marked[context] = candidate
    return marked


def _decorate_preview_pages(
    preview_root: Path,
    *,
    site_base: str,
    version: str,
    upstream_tag: str,
    upstream_commit: str,
    translation_revision: str,
    translation_date: str,
) -> tuple[int, tuple[str, ...]]:
    site_base = _normalise_site_base(site_base)
    version = _validate_version(version)
    html_paths = sorted(
        preview_root.rglob("*.html"),
        key=lambda path: path.relative_to(preview_root).as_posix().encode("utf-8"),
    )
    if not html_paths:
        raise ValueError("preview build produced no HTML pages")
    relative_paths: list[str] = []
    for path in html_paths:
        relative = path.relative_to(preview_root).as_posix()
        relative_paths.append(relative)
        text = path.read_text(encoding="utf-8")
        if _ROBOTS_META_RE.search(text):
            raise ValueError(f"upstream HTML already contains robots metadata: {relative}")
        text = _localize_texinfo_chrome(text)
        text = _add_tag_attribute(text, "html", "data-translation-state", "mixed")
        text = _add_tag_attribute(text, "html", "data-release-channel", "preview")
        english_url = (
            f"{site_base}v{quote(version, safe='._-')}/en/"
            f"{quote(relative, safe='/._-')}"
        )
        preview_url = (
            f"{site_base}preview/v{quote(version, safe='._-')}/zh-cn/"
            f"{quote(relative, safe='/._-')}"
        )
        head_metadata = (
            '\n<meta name="robots" content="noindex,nofollow">\n'
            f'<link rel="canonical" href="{preview_url}">\n'
            f'<link rel="alternate" hreflang="zh-CN" href="{preview_url}">\n'
            f'<link rel="alternate" hreflang="en" href="{english_url}">\n'
            "<style>\n"
            ".translation-preview{border:2px solid #9a6700;background:#fff8c5;"
            "color:#24292f;margin:1rem;padding:.75rem 1rem}"
            ".translation-preview p{margin:.25rem 0}"
            ".english-fallback-marker{font-weight:700}\n"
            "</style>\n"
        )
        head_end = re.search(r"</head\s*>", text, re.IGNORECASE)
        if head_end is None:
            raise ValueError(f"generated HTML is missing </head>: {relative}")
        text = text[: head_end.start()] + head_metadata + text[head_end.start() :]
        body = re.search(r"<body\b[^>]*>", text, re.IGNORECASE)
        if body is None:
            raise ValueError(f"generated HTML is missing <body>: {relative}")
        banner = (
            '\n<aside class="translation-preview" role="note" '
            'data-translation-state="mixed">\n'
            "<p><strong>非官方简体中文预览</strong> · "
            f"Gmsh {html.escape(version)} · {html.escape(translation_revision)} · "
            f"{html.escape(translation_date)}</p>\n"
            '<p><span class="english-fallback-marker">英文回退</span>：'
            "尚未达到正式状态的内容显示固定上游英文。"
            f'<a href="{english_url}">查看对应英文页面</a>。'
            f"上游来源：{html.escape(upstream_tag)} "
            f"({html.escape(upstream_commit)}).</p>\n"
            "</aside>\n"
        )
        text = text[: body.end()] + banner + text[body.end() :]
        path.write_text(text, encoding="utf-8", newline="\n")

        rendered = path.read_text(encoding="utf-8")
        required = (
            'lang="zh-CN"',
            'data-translation-state="mixed"',
            'data-release-channel="preview"',
            '<meta name="robots" content="noindex,nofollow">',
            f'<link rel="canonical" href="{preview_url}">',
            'class="translation-preview"',
            'class="english-fallback-marker">英文回退</span>',
        )
        if any(value not in rendered for value in required):
            raise ValueError(f"preview metadata verification failed: {relative}")
    return len(html_paths), tuple(relative_paths)


def build_preview_site(
    source_root: Path,
    dist_root: Path,
    *,
    po_path: Path,
    status: Mapping[str, object],
    review_directory: Path,
    review_manifest_path: Path,
    check_units: Iterable[str],
    rules_path: Path,
    glossary_path: Path,
    version: str,
    source_date_epoch: int,
    site_base: str,
    upstream_tag: str,
    upstream_commit: str,
    translation_revision: str,
    translation_date: str,
    makeinfo_command: Sequence[str] = ("makeinfo",),
) -> PreviewBuildResult:
    """Build English reference pages and a noindex Chinese preview."""

    source_root = Path(source_root).resolve()
    dist_root = Path(dist_root).resolve()
    version = _validate_version(version)
    extraction = extract_tree(source_root)
    po = read_po_catalog(Path(po_path))
    derived_status, per_unit_rules = _derive_status(
        source_root,
        Path(po_path),
        list(extraction.units),
        po,
        review_directory=Path(review_directory),
        review_manifest_path=Path(review_manifest_path),
        check_units=check_units,
        rules_path=Path(rules_path),
        glossary_path=Path(glossary_path),
    )
    if status != derived_status:
        raise ValueError("status report does not match freshly derived translation state")
    formal_catalog = select_formal_catalog(
        extraction.units,
        po,
        derived_status,
        rules_hash=sha256_file(rules_path),
        glossary_hash=sha256_file(glossary_path),
        semantic_rules_hashes=per_unit_rules,
    )
    staged_catalog = _mark_formal_references(extraction.units, formal_catalog)
    english_root = dist_root / f"v{version}" / "en"
    preview_root = dist_root / "preview" / f"v{version}" / "zh-cn"
    with tempfile.TemporaryDirectory(prefix="gmsh-doc-cn-preview-") as directory:
        translated_source = Path(directory) / "source"
        apply_catalog(source_root, translated_source, extraction.units, staged_catalog)
        _set_texinfo_document_language(translated_source)
        english = build_html_site(
            source_root,
            english_root,
            source_date_epoch=source_date_epoch,
            language="en",
            makeinfo_command=makeinfo_command,
        )
        preview = build_html_site(
            translated_source,
            preview_root,
            source_date_epoch=source_date_epoch,
            language="zh-CN",
            makeinfo_command=makeinfo_command,
        )

    _canonicalize_preview_anchors(english_root, preview_root)
    preview_count, preview_paths = _decorate_preview_pages(
        preview_root,
        site_base=site_base,
        version=version,
        upstream_tag=upstream_tag,
        upstream_commit=upstream_commit,
        translation_revision=translation_revision,
        translation_date=translation_date,
    )
    english_paths = tuple(
        path.relative_to(english_root).as_posix()
        for path in sorted(
            english_root.rglob("*.html"),
            key=lambda path: path.relative_to(english_root).as_posix().encode("utf-8"),
        )
    )
    if english_paths != preview_paths:
        raise ValueError("English and Chinese preview page paths do not match")
    for relative in english_paths:
        rendered = (english_root / relative).read_text(encoding="utf-8")
        if 'lang="en"' not in rendered:
            raise ValueError(f"English page is missing language metadata: {relative}")
        robots = _ROBOTS_META_RE.search(rendered)
        if robots is not None and "noindex" in robots.group(0).casefold():
            raise ValueError(f"versioned English page unexpectedly contains noindex: {relative}")

    english_inventory = collect_html_inventory(english_root)
    preview_inventory = collect_html_inventory(preview_root)
    english_duplicates = english_inventory["duplicate_anchors"]
    preview_duplicates = preview_inventory["duplicate_anchors"]
    if english_duplicates or preview_duplicates:
        raise ValueError("English or Chinese preview build contains duplicate anchors")
    english_pages = {
        str(page["path"]): set(page["anchors"]) for page in english_inventory["pages"]
    }
    preview_pages = {
        str(page["path"]): set(page["anchors"]) for page in preview_inventory["pages"]
    }
    missing_english_anchors = sorted(
        (page, anchor)
        for page, anchors in english_pages.items()
        for anchor in anchors - preview_pages.get(page, set())
    )
    if missing_english_anchors:
        raise ValueError(
            "Chinese preview is missing a corresponding English anchor: "
            f"{missing_english_anchors[0]}"
        )
    preview_only_anchor_count = sum(
        len(anchors - english_pages.get(page, set()))
        for page, anchors in preview_pages.items()
    )
    broken_keys = ("source", "href", "target", "fragment", "reason")
    english_broken = {
        tuple(str(item[key]) for key in broken_keys)
        for item in english_inventory["broken_internal_links"]
    }
    preview_broken = {
        tuple(str(item[key]) for key in broken_keys)
        for item in preview_inventory["broken_internal_links"]
    }
    added_broken = sorted(preview_broken - english_broken)
    if added_broken:
        raise ValueError(f"Chinese preview introduced a broken internal link: {added_broken[0]}")

    added_warnings = sorted(
        set(preview.warning_fingerprints) - set(english.warning_fingerprints)
    )
    if added_warnings:
        raise ValueError(f"Chinese preview introduced a Texinfo warning: {added_warnings[0]}")
    english_site_link_count, english_resource_link_count = _verify_site_absolute_links(
        dist_root, english_root, site_base
    )
    preview_site_link_count, preview_resource_link_count = _verify_site_absolute_links(
        dist_root, preview_root, site_base
    )
    site_absolute_link_count = english_site_link_count + preview_site_link_count
    resource_link_count = english_resource_link_count + preview_resource_link_count
    unit_count = len(extraction.units)
    formal_count = len(formal_catalog)
    return PreviewBuildResult(
        dist_root=dist_root,
        english_root=english_root,
        preview_root=preview_root,
        unit_count=unit_count,
        formal_unit_count=formal_count,
        fallback_unit_count=unit_count - formal_count,
        english_html_file_count=len(english_paths),
        preview_html_file_count=preview_count,
        noindex_nofollow_page_count=preview_count,
        visible_preview_marker_page_count=preview_count,
        english_fallback_marker_page_count=preview_count,
        english_anchor_count=int(english_inventory["anchor_count"]),
        preview_anchor_count=int(preview_inventory["anchor_count"]),
        missing_english_anchor_count=len(missing_english_anchors),
        preview_only_anchor_count=preview_only_anchor_count,
        english_duplicate_anchor_count=len(english_duplicates),
        preview_duplicate_anchor_count=len(preview_duplicates),
        english_broken_internal_link_count=len(english_broken),
        preview_broken_internal_link_count=len(preview_broken),
        preview_added_broken_internal_link_count=len(added_broken),
        site_absolute_link_count=site_absolute_link_count,
        broken_site_absolute_link_count=0,
        resource_link_count=resource_link_count,
        broken_resource_link_count=0,
        english_warning_fingerprints=english.warning_fingerprints,
        preview_warning_fingerprints=preview.warning_fingerprints,
    )


def preview_build_report(
    result: PreviewBuildResult,
    *,
    site_base: str,
    version: str,
    upstream_tag: str,
    upstream_commit: str,
    translation_revision: str,
    translation_date: str,
    source_date_epoch: int,
    input_hashes: Mapping[str, str],
    artifact_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Create the versioned report written after two identical clean builds."""

    if result.formal_unit_count + result.fallback_unit_count != result.unit_count:
        raise ValueError("preview unit counts are inconsistent")
    page_counts = {
        result.english_html_file_count,
        result.preview_html_file_count,
        result.noindex_nofollow_page_count,
        result.visible_preview_marker_page_count,
        result.english_fallback_marker_page_count,
    }
    if len(page_counts) != 1:
        raise ValueError("preview page-boundary counts are inconsistent")
    if result.missing_english_anchor_count != 0:
        raise ValueError("preview report cannot contain missing English anchors")
    files = artifact_manifest.get("files")
    file_count = artifact_manifest.get("file_count")
    if not isinstance(files, list) or file_count != len(files):
        raise ValueError("artifact manifest file_count does not match its files")

    return {
        "schema_version": 1,
        "algorithm": "bilingual-preview-build-v1",
        "reproducible": True,
        "site_base": _normalise_site_base(site_base),
        "upstream_version": _validate_version(version),
        "upstream_tag": upstream_tag,
        "upstream_commit": upstream_commit,
        "translation_revision": translation_revision,
        "translation_date": translation_date,
        "source_date_epoch": source_date_epoch,
        "unit_count": result.unit_count,
        "formal_unit_count": result.formal_unit_count,
        "fallback_unit_count": result.fallback_unit_count,
        "english_root": result.english_root.relative_to(result.dist_root).as_posix(),
        "preview_root": result.preview_root.relative_to(result.dist_root).as_posix(),
        "english_html_file_count": result.english_html_file_count,
        "preview_html_file_count": result.preview_html_file_count,
        "noindex_nofollow_page_count": result.noindex_nofollow_page_count,
        "visible_preview_marker_page_count": result.visible_preview_marker_page_count,
        "english_fallback_marker_page_count": result.english_fallback_marker_page_count,
        "english_anchor_count": result.english_anchor_count,
        "preview_anchor_count": result.preview_anchor_count,
        "missing_english_anchor_count": result.missing_english_anchor_count,
        "preview_only_anchor_count": result.preview_only_anchor_count,
        "english_duplicate_anchor_count": result.english_duplicate_anchor_count,
        "preview_duplicate_anchor_count": result.preview_duplicate_anchor_count,
        "english_broken_internal_link_count": result.english_broken_internal_link_count,
        "preview_broken_internal_link_count": result.preview_broken_internal_link_count,
        "preview_added_broken_internal_link_count": (
            result.preview_added_broken_internal_link_count
        ),
        "site_absolute_link_count": result.site_absolute_link_count,
        "broken_site_absolute_link_count": result.broken_site_absolute_link_count,
        "resource_link_count": result.resource_link_count,
        "broken_resource_link_count": result.broken_resource_link_count,
        "warning_fingerprints": {
            "english": list(result.english_warning_fingerprints),
            "preview": list(result.preview_warning_fingerprints),
            "preview_additions": sorted(
                set(result.preview_warning_fingerprints)
                - set(result.english_warning_fingerprints)
            ),
        },
        "input_hashes": dict(sorted(input_hashes.items())),
        "artifact_manifest": dict(artifact_manifest),
    }


def write_preview_report(report: Mapping[str, object], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
