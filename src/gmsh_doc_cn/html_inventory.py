"""Deterministic HTML page, anchor, and internal-link inventories."""

from __future__ import annotations

import json
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        element_anchors = {
            value
            for name in ("id", "name")
            if (value := values.get(name)) is not None and value
        }
        self.anchors.extend(sorted(element_anchors))
        if tag.casefold() == "a" and (href := values.get("href")) is not None:
            self.hrefs.append(href)


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _relative_html_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*.html") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )


def _target_path(source: str, href_path: str) -> str | None:
    """Return a normalized site-relative target or None for site-absolute links."""

    decoded = unquote(href_path).replace("\\", "/")
    if decoded.startswith("/"):
        return None
    if not decoded:
        return source
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded))
    if joined == ".." or joined.startswith("../"):
        return f"../{joined.removeprefix('../')}"
    if decoded.endswith("/"):
        joined = posixpath.join(joined, "index.html")
    return joined


def collect_html_inventory(root: Path) -> dict[str, object]:
    """Collect pages and anchors and report broken relative internal links."""

    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"HTML root is not a directory: {root}")

    anchors_by_page: dict[str, set[str]] = {}
    hrefs_by_page: dict[str, list[str]] = {}
    duplicate_anchors: list[dict[str, str]] = []
    for path in _relative_html_files(root):
        relative = path.relative_to(root).as_posix()
        parser = _PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        seen: set[str] = set()
        for anchor in parser.anchors:
            if anchor in seen:
                duplicate_anchors.append({"page": relative, "anchor": anchor})
            seen.add(anchor)
        anchors_by_page[relative] = seen
        hrefs_by_page[relative] = parser.hrefs

    broken: list[dict[str, str]] = []
    for source in sorted(hrefs_by_page, key=_utf8_key):
        for href in hrefs_by_page[source]:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                continue
            target = _target_path(source, parsed.path)
            if target is None:
                continue
            target_file = root / Path(*target.split("/"))
            fragment = unquote(parsed.fragment)
            outside_root = target == ".." or target.startswith("../")
            if outside_root or not target_file.is_file():
                broken.append(
                    {
                        "source": source,
                        "href": href,
                        "target": target,
                        "fragment": fragment,
                        "reason": "missing-page",
                    }
                )
            elif fragment and fragment not in anchors_by_page.get(target, set()):
                broken.append(
                    {
                        "source": source,
                        "href": href,
                        "target": target,
                        "fragment": fragment,
                        "reason": "missing-fragment",
                    }
                )

    pages = [
        {"path": page, "anchors": sorted(anchors_by_page[page], key=_utf8_key)}
        for page in sorted(anchors_by_page, key=_utf8_key)
    ]
    record_key = lambda item: tuple(_utf8_key(item[key]) for key in ("source", "href", "reason"))
    unique_broken = {
        tuple(item[key] for key in ("source", "href", "target", "fragment", "reason")): item
        for item in broken
    }
    return {
        "schema_version": 1,
        "algorithm": "html-anchor-inventory-v1",
        "html_file_count": len(pages),
        "anchor_count": sum(len(page["anchors"]) for page in pages),
        "pages": pages,
        "duplicate_anchors": sorted(
            duplicate_anchors,
            key=lambda item: (_utf8_key(item["page"]), _utf8_key(item["anchor"])),
        ),
        "broken_internal_links": sorted(unique_broken.values(), key=record_key),
    }


def write_html_inventory(root: Path, output: Path) -> dict[str, object]:
    """Write a canonical JSON inventory and return it."""

    report = collect_html_inventory(root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
