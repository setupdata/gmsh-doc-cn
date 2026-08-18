"""A small, deterministic Texinfo-to-PO bridge for the Gmsh manual.

The bridge deliberately understands only the subset used by the pinned Gmsh
manual.  It never translates structural commands or example environments.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping


_HEADING_COMMANDS = {
    "chapter",
    "section",
    "subsection",
    "subsubsection",
    "unnumbered",
    "unnumberedsec",
    "appendix",
    "appendixsec",
    "appendixsubsec",
    "heading",
    "subheading",
}
_SKIPPED_ENVIRONMENTS = {
    "copying",
    "ifinfo",
    "menu",
    "example",
    "smallexample",
    "verbatim",
    "lisp",
    "smalllisp",
    "display",
    "smalldisplay",
    "format",
    "smallformat",
    "tex",
    "titlepage",
    "ignore",
}
_PROTECTED_NODES = {"Copying conditions", "License"}
_STRUCTURAL_COMMANDS = {
    "bye",
    "clear",
    "copying",
    "documentdescription",
    "documentencoding",
    "documentlanguage",
    "end",
    "enumerate",
    "ftable",
    "group",
    "ifclear",
    "ifhtml",
    "ifinfo",
    "ifnothtml",
    "ifnotinfo",
    "ifnottex",
    "ifset",
    "iftex",
    "include",
    "itemize",
    "macro",
    "multitable",
    "printindex",
    "author",
    "dircategory",
    "direntry",
    "footnotestyle",
    "set",
    "setfilename",
    "settitle",
    "setchapternewpage",
    "shortcontents",
    "shorttitlepage",
    "subtitle",
    "syncodeindex",
    "table",
    "title",
    "titlepage",
    "top",
    "verbatiminclude",
}
_FULLY_PROTECTED_COMMANDS = {
    "anchor",
    "code",
    "command",
    "email",
    "env",
    "file",
    "kbd",
    "key",
    "math",
    "option",
    "pxref",
    "ref",
    "samp",
    "dots",
    "today",
    "value",
    "var",
    "xref",
}
_LABEL_COMMANDS = {"uref", "url"}
_COMMAND_RE = re.compile(r"@([A-Za-z][A-Za-z0-9-]*|[*@{}])")
_PLAIN_PROTECTED_RE = re.compile(
    r"https?://[^\s,)}]+"
    r"|\b(?:gmsh(?:[/.:][A-Za-z0-9_./:-]+)+)\b"
    r"|\b(?:General|Geometry|Mesh|Solver|PostProcessing|Print|View)\.[A-Za-z0-9_.]+\b"
    r"|(?<![\w-])--[A-Za-z][A-Za-z0-9-]*"
    r"|(?<![\w])-[A-Za-z][A-Za-z0-9-]*(?=\s+(?:value|int|float|string|file|list)\b)"
    r"|\b\d+(?:\.\d+)*(?:[eE][+-]?\d+)?\s?(?:%|mm|cm|km|ms|kHz|MHz|GB|MB|KB|bytes?|bits?|rad|deg|Hz)\b"
    r"|\b\d+(?:\.\d+)*(?:[eE][+-]?\d+)?\b"
)
_CREDITS_PROPER_NAME_RE = re.compile(
    r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\".-]+"
    r"(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\".-]+)+\b"
)
_PROTECTED_KEY_RE = re.compile(
    r"^(?:gmsh(?:/[A-Za-z0-9_]+)+|"
    r"(?:General|Geometry|Mesh|Solver|PostProcessing|Print|View)\.[A-Za-z0-9_.]+|"
    r"Plugin\([^)]+\)|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)$"
)


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _matching_brace(text: str, opening: int, limit: int | None = None) -> int | None:
    """Find one unescaped Texinfo command brace without interpreting its contents."""

    if opening >= len(text) or text[opening] != "{":
        return None
    limit = len(text) if limit is None else limit
    depth = 0
    index = opening
    while index < limit:
        char = text[index]
        escaped = index > 0 and text[index - 1] == "@"
        if char == "{" and not escaped:
            depth += 1
        elif char == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _top_level_commas(text: str, start: int, end: int) -> list[int]:
    commas: list[int] = []
    depth = 0
    index = start
    while index < end:
        if text[index] == "@" and index + 1 < end and text[index + 1] in "@{}":
            index += 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth = max(depth - 1, 0)
        elif text[index] == "," and depth == 0:
            commas.append(index)
        index += 1
    return commas


def _texinfo_tokens(text: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Return protected command syntax while leaving formatting text translatable."""

    values: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []

    def protect(start: int, end: int) -> None:
        values.append((start, end, text[start:end]))
        covered.append((start, end))

    def walk(start: int, end: int) -> None:
        index = start
        while index < end:
            char = text[index]
            if char == "@" and index + 1 < end:
                following = text[index + 1]
                if following in "*@{}":
                    values.append((index, index + 2, text[index : index + 2]))
                    index += 2
                    continue
                if following.isalpha():
                    name_end = index + 2
                    while name_end < end and (
                        text[name_end].isalnum() or text[name_end] == "-"
                    ):
                        name_end += 1
                    name = text[index + 1 : name_end]
                    if name_end < end and text[name_end] == "{":
                        closing = _matching_brace(text, name_end, end)
                        if closing is None:
                            values.append((index, name_end + 1, text[index : name_end + 1]))
                            walk(name_end + 1, end)
                            return
                        if name in _FULLY_PROTECTED_COMMANDS:
                            protect(index, closing + 1)
                            index = closing + 1
                            continue
                        commas = _top_level_commas(text, name_end + 1, closing)
                        if name in _LABEL_COMMANDS:
                            if not commas:
                                protect(index, closing + 1)
                            else:
                                protect(index, commas[0] + 1)
                                segment_start = commas[0] + 1
                                for comma in commas[1:]:
                                    walk(segment_start, comma)
                                    values.append((comma, comma + 1, ","))
                                    segment_start = comma + 1
                                walk(segment_start, closing)
                                values.append((closing, closing + 1, "}"))
                            index = closing + 1
                            continue
                        if name == "image":
                            if len(commas) < 3:
                                protect(index, closing + 1)
                            else:
                                protect(index, commas[2] + 1)
                                alt_end = commas[3] if len(commas) > 3 else closing
                                walk(commas[2] + 1, alt_end)
                                if len(commas) > 3:
                                    protect(commas[3], closing + 1)
                                else:
                                    values.append((closing, closing + 1, "}"))
                            index = closing + 1
                            continue
                        values.append((index, name_end + 1, text[index : name_end + 1]))
                        walk(name_end + 1, closing)
                        values.append((closing, closing + 1, "}"))
                        index = closing + 1
                        continue
                    values.append((index, name_end, text[index:name_end]))
                    index = name_end
                    continue
                punctuation_end = index + 2
                if punctuation_end < end and text[punctuation_end] == "{":
                    closing = _matching_brace(text, punctuation_end, end)
                    if closing is not None:
                        protect(index, closing + 1)
                        index = closing + 1
                        continue
                values.append((index, punctuation_end, text[index:punctuation_end]))
                index = punctuation_end
                continue
            if char in "{}":
                values.append((index, index + 1, char))
            index += 1

    walk(0, len(text))
    return values, covered


def protected_values(text: str, *, protect_names: bool = False) -> tuple[str, ...]:
    """Return the ordered protected-token stream used by validation hashes."""

    values, covered = _texinfo_tokens(text)
    for match in _PLAIN_PROTECTED_RE.finditer(text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        values.append((match.start(), match.end(), match.group(0)))
    if protect_names:
        for match in _CREDITS_PROPER_NAME_RE.finditer(text):
            if any(start <= match.start() < end for start, end in covered):
                continue
            values.append((match.start(), match.end(), match.group(0)))
    values.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(value for _, _, value in values)


def _is_natural_language(text: str) -> bool:
    without_commands = _COMMAND_RE.sub(" ", text)
    return any(len(word) > 1 for word in re.findall(r"[A-Za-z]+", without_commands))


def _is_top_level_author_attribution(text: str, node: str) -> bool:
    """Recognise the standalone author line in the Gmsh Top node."""

    if node != "Top" or " and " not in text:
        return False
    plain = re.sub(r"@[,.\"'=^~uvHckdb]\{([^{}]+)\}", r"\1", text)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ-]+", plain)
    return bool(words) and all(word == "and" or word[0].isupper() for word in words)


def _category(relative_file: str, node: str, section_root: str) -> tuple[str, str]:
    name = Path(relative_file).name
    lowered = f"{section_root} {node}".casefold()
    if name == "api.texi":
        return "script_api", "api"
    if name.startswith("opt_"):
        return "option_field_plugin", "options"
    if name == "fields.texi":
        return "option_field_plugin", "fields"
    if name == "plugins.texi":
        return "option_field_plugin", "plugins"
    if "file format" in lowered:
        return "formats_compile_faq", "formats"
    if "compil" in lowered:
        return "formats_compile_faq", "compile"
    if "frequently asked" in lowered or lowered.startswith("faq"):
        return "formats_compile_faq", "faq"
    if "scripting" in lowered or "script" in lowered:
        return "script_api", "scripting"
    if "tutorial" in lowered or re.search(r"(?:^|\s)[tx]\d+(?:\s|$)", lowered):
        return "ordinary_tutorial", "tutorial"
    return "ordinary_tutorial", "prose"


@dataclass(frozen=True)
class TranslationUnit:
    unit_id: str
    msgctxt: str
    msgid: str
    relative_file: str
    node: str
    structure_path: str
    role: str
    semantic_key: str
    category: str
    subcategory: str
    start: int
    end: int
    source_hash: str
    context_hash: str
    protected_hash: str
    protected_values: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Extraction:
    units: tuple[TranslationUnit, ...]


@dataclass(frozen=True)
class PoCatalog:
    translations: Mapping[str, str]
    flags: Mapping[str, set[str]]
    msgids: Mapping[str, str]
    obsolete_entries: tuple["PoEntry", ...]

    @property
    def obsolete_count(self) -> int:
        return len(self.obsolete_entries)


@dataclass(frozen=True)
class PoEntry:
    msgctxt: str
    msgid: str
    msgstr: str
    flags: frozenset[str] = frozenset()


@dataclass
class _Draft:
    msgid: str
    relative_file: str
    node: str
    section_root: str
    role: str
    semantic_key: str
    start: int
    end: int
    category: str | None = None
    subcategory: str | None = None


def _extract_file(source_root: Path, path: Path) -> list[_Draft]:
    relative = path.relative_to(source_root).as_posix()
    text = path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    drafts: list[_Draft] = []
    node = "Top"
    section_root = "Top"
    current_item = ""
    environment_stack: list[str] = []
    paragraph: list[str] = []
    paragraph_start = 0
    offset = 0

    def flush() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        raw = "".join(paragraph)
        candidate = raw.rstrip("\r\n")
        end = paragraph_start + len(candidate)
        if (
            candidate
            and node not in _PROTECTED_NODES
            and not _is_top_level_author_attribution(candidate, node)
            and _is_natural_language(candidate)
        ):
            drafts.append(
                _Draft(
                    msgid=candidate,
                    relative_file=relative,
                    node=node,
                    section_root=section_root,
                    role="paragraph",
                    semantic_key=current_item,
                    start=paragraph_start,
                    end=end,
                )
            )
        paragraph = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped.startswith("\\input texinfo.tex"):
            flush()
            offset += len(line)
            continue
        inline_comment = re.search(r"(?<!@)@(?:c|comment)(?=\s|$)", stripped)
        if inline_comment is not None:
            prefix = line[: inline_comment.start()].rstrip()
            if prefix:
                if not paragraph:
                    paragraph_start = offset
                paragraph.append(prefix)
            flush()
            offset += len(line)
            continue
        command_match = re.match(r"^@([A-Za-z][A-Za-z0-9-]*)(?:\s+(.*))?$", stripped)
        command = command_match.group(1) if command_match else ""
        argument = (command_match.group(2) or "") if command_match else ""

        if command == "end":
            flush()
            if environment_stack and argument.strip() == environment_stack[-1]:
                environment_stack.pop()
            offset += len(line)
            continue
        if environment_stack:
            if command in _SKIPPED_ENVIRONMENTS:
                environment_stack.append(command)
            offset += len(line)
            continue
        if command in _SKIPPED_ENVIRONMENTS:
            flush()
            environment_stack.append(command)
            offset += len(line)
            continue
        if not stripped:
            flush()
            offset += len(line)
            continue
        if stripped.startswith("@c ") or stripped in {"@c", "@comment"}:
            flush()
            offset += len(line)
            continue
        if command == "node":
            flush()
            node = argument.split(",", 1)[0].strip() or "Top"
            current_item = ""
            offset += len(line)
            continue
        if command in _HEADING_COMMANDS:
            flush()
            title = argument.strip()
            if node not in _PROTECTED_NODES and title and _is_natural_language(title):
                start = offset + stripped.index(argument)
                drafts.append(
                    _Draft(
                        title,
                        relative,
                        node,
                        section_root,
                        "heading",
                        node,
                        start,
                        start + len(argument),
                    )
                )
            if command in {"chapter", "unnumbered", "appendix"}:
                section_root = node
            current_item = ""
            offset += len(line)
            continue
        if command in {"cindex", "findex", "vindex", "kindex", "pindex", "tindex"}:
            flush()
            value = argument.strip()
            if node not in _PROTECTED_NODES and value and _is_natural_language(value):
                start = offset + stripped.index(argument)
                drafts.append(
                    _Draft(
                        value,
                        relative,
                        node,
                        section_root,
                        "index",
                        value,
                        start,
                        start + len(argument),
                    )
                )
            offset += len(line)
            continue
        if command == "item":
            flush()
            value = argument.strip()
            generated_identifier = (
                path.name in {"fields.texi", "plugins.texi"}
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value) is not None
            ) or (
                path.name == "cmake_options.texi"
                and re.fullmatch(r"[A-Z][A-Z0-9_]*", value) is not None
            ) or (
                path.name == "commandline.texi" and value.startswith("-")
            )
            if not value:
                current_item = ""
            elif _PROTECTED_KEY_RE.fullmatch(value) or generated_identifier:
                current_item = value
            elif node not in _PROTECTED_NODES and _is_natural_language(value):
                current_item = value
                start = offset + stripped.index(argument)
                drafts.append(
                    _Draft(
                        value,
                        relative,
                        node,
                        section_root,
                        "item",
                        value,
                        start,
                        start + len(argument),
                    )
                )
            offset += len(line)
            continue
        if command in _STRUCTURAL_COMMANDS or command in {"anchor", "image"}:
            flush()
            offset += len(line)
            continue
        if stripped.startswith("@") and command_match and not _is_natural_language(stripped):
            flush()
            offset += len(line)
            continue

        if not paragraph:
            paragraph_start = offset
        paragraph.append(line)
        offset += len(line)
    flush()
    return drafts


def _extract_plaintext_file(source_root: Path, path: Path) -> list[_Draft]:
    """Extract the release history and the non-license prose from CREDITS."""

    relative = path.relative_to(source_root).as_posix()
    text = path.read_bytes().decode("utf-8")
    paragraph_re = re.compile(
        r"^(?P<paragraph>[^\r\n].*?)(?=\r?\n\r?\n|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    drafts: list[_Draft] = []
    credits_started = False
    for match in paragraph_re.finditer(text):
        paragraph = match.group("paragraph").rstrip("\r\n")
        if not paragraph or not _is_natural_language(paragraph):
            continue
        if relative == "CREDITS.txt":
            if paragraph.startswith("Code contributions"):
                credits_started = True
            if not credits_started:
                continue
            lowered = paragraph.casefold()
            if "copyright" in lowered and (
                paragraph.startswith("The ") or "permission is granted" in lowered
            ):
                break
            category, subcategory = "excluded", "credits"
            semantic_key = ""
        else:
            category, subcategory = "excluded", "version_history"
            version = re.match(r"(\d+\.\d+(?:\.\d+)?)\s+\(", paragraph)
            semantic_key = version.group(1) if version else ""
        drafts.append(
            _Draft(
                msgid=paragraph,
                relative_file=relative,
                node="Version history" if relative == "CHANGELOG.txt" else "Credits",
                section_root="Appendix",
                role="paragraph",
                semantic_key=semantic_key,
                start=match.start("paragraph"),
                end=match.start("paragraph") + len(paragraph),
                category=category,
                subcategory=subcategory,
            )
        )
    return drafts


def extract_tree(source_root: Path) -> Extraction:
    """Extract translatable blocks from all release Texinfo files."""

    source_root = Path(source_root).resolve()
    texinfo_root = source_root / "doc" / "texinfo"
    if not texinfo_root.is_dir():
        raise FileNotFoundError(f"missing Texinfo directory: {texinfo_root}")
    paths = sorted(texinfo_root.glob("*.texi"), key=lambda item: item.name.encode("utf-8"))
    drafts: list[_Draft] = []
    for path in paths:
        drafts.extend(_extract_file(source_root, path))
    for filename in ("CHANGELOG.txt", "CREDITS.txt"):
        path = source_root / filename
        if path.is_file():
            drafts.extend(_extract_plaintext_file(source_root, path))

    duplicate_counts: dict[tuple[str, str, str, str], int] = {}
    units: list[TranslationUnit] = []
    seen_contexts: set[str] = set()
    seen_ids: set[str] = set()
    for draft in drafts:
        msgid = _normalise(draft.msgid)
        key = draft.semantic_key or _sha256(msgid)
        duplicate_key = (draft.relative_file, draft.node, draft.role, key)
        occurrence = duplicate_counts.get(duplicate_key, 0) + 1
        duplicate_counts[duplicate_key] = occurrence
        if occurrence > 1:
            key = f"{key}#{occurrence}"
        msgctxt = "|".join((draft.relative_file, draft.node, draft.role, key))
        context_hash = _sha256(_normalise(msgctxt))
        values = protected_values(msgid, protect_names=draft.relative_file == "CREDITS.txt")
        protected_serialised = json.dumps(
            list(values), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        protected_hash = _sha256(protected_serialised)
        source_hash = _sha256(msgid.encode("utf-8") + b"\0" + protected_serialised)
        unit_id = _sha256("gmsh-cn-unit-v1\0" + msgctxt + "\0" + msgid)
        if msgctxt in seen_contexts or unit_id in seen_ids:
            raise ValueError(f"translation-unit identity collision: {msgctxt}")
        seen_contexts.add(msgctxt)
        seen_ids.add(unit_id)
        category, subcategory = (
            (draft.category, draft.subcategory)
            if draft.category is not None and draft.subcategory is not None
            else _category(draft.relative_file, draft.node, draft.section_root)
        )
        units.append(
            TranslationUnit(
                unit_id=unit_id,
                msgctxt=msgctxt,
                msgid=msgid,
                relative_file=draft.relative_file,
                node=draft.node,
                structure_path=f"{draft.relative_file}::{draft.node}",
                role=draft.role,
                semantic_key=key,
                category=category,
                subcategory=subcategory,
                start=draft.start,
                end=draft.end,
                source_hash=source_hash,
                context_hash=context_hash,
                protected_hash=protected_hash,
                protected_values=values,
            )
        )
    return Extraction(tuple(units))


def write_units(units: Iterable[TranslationUnit], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for unit in units:
            stream.write(json.dumps(unit.to_json(), ensure_ascii=False, sort_keys=True) + "\n")


def write_po(
    units: Iterable[TranslationUnit],
    output: Path,
    *,
    language: str | None = None,
    translations: Mapping[str, str] | None = None,
    flags: Mapping[str, set[str]] | None = None,
    obsolete_entries: Iterable[PoEntry] = (),
) -> None:
    """Write a stable, unwrapped PO/POT file understood by GNU gettext."""

    translations = translations or {}
    flags = flags or {}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "Project-Id-Version: gmsh-doc-cn 4.15.2\n",
        "Report-Msgid-Bugs-To: https://github.com/setupdata/gmsh-doc-cn/issues\n",
        "POT-Creation-Date: 2026-03-24 08:00+0000\n",
        "MIME-Version: 1.0\n",
        "Content-Type: text/plain; charset=UTF-8\n",
        "Content-Transfer-Encoding: 8bit\n",
    ]
    if language:
        header.extend(
            [
                "PO-Revision-Date: 2026-08-18 00:00+0000\n",
                "Last-Translator: Gmsh Chinese Documentation Project\n",
                "Language-Team: Chinese (Simplified)\n",
                f"Language: {language}\n",
                "Plural-Forms: nplurals=1; plural=0;\n",
            ]
        )
    lines = ["msgid \"\"", f"msgstr {json.dumps(''.join(header), ensure_ascii=False)}", ""]
    for unit in units:
        unit_flags = sorted(flags.get(unit.msgctxt, set()))
        if unit_flags:
            lines.append(f"#, {', '.join(unit_flags)}")
        lines.extend(
            [
                f"#. unit_id: {unit.unit_id}",
                f"#. source_hash: {unit.source_hash}",
                f"#: {unit.relative_file}",
                f"msgctxt {json.dumps(unit.msgctxt, ensure_ascii=False)}",
                f"msgid {json.dumps(unit.msgid, ensure_ascii=False)}",
                f"msgstr {json.dumps(translations.get(unit.msgctxt, ''), ensure_ascii=False)}",
                "",
            ]
        )
    for entry in sorted(obsolete_entries, key=lambda value: value.msgctxt.encode("utf-8")):
        if entry.flags:
            lines.append(f"#~ #, {', '.join(sorted(entry.flags))}")
        lines.extend(
            [
                f"#~ msgctxt {json.dumps(entry.msgctxt, ensure_ascii=False)}",
                f"#~ msgid {json.dumps(entry.msgid, ensure_ascii=False)}",
                f"#~ msgstr {json.dumps(entry.msgstr, ensure_ascii=False)}",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def read_po(path: Path) -> dict[str, str]:
    """Read the deterministic one-line PO representation produced above."""

    return dict(read_po_catalog(path).translations)


def read_po_catalog(path: Path) -> PoCatalog:
    """Read deterministic PO entries together with standard gettext state."""

    translations: dict[str, str] = {}
    msgids: dict[str, str] = {}
    flags: dict[str, set[str]] = {}
    obsolete_entries: list[PoEntry] = []
    context: str | None = None
    message = ""
    translation = ""
    entry_flags: set[str] = set()
    obsolete = False
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines() + [""]:
        line = raw_line
        if line.startswith("#~ "):
            obsolete = True
            line = line[3:]
        if line.startswith("#, "):
            entry_flags.update(value.strip() for value in line[3:].split(",") if value.strip())
        elif line.startswith("msgctxt "):
            context = json.loads(line[len("msgctxt ") :])
        elif line.startswith("msgid "):
            message = json.loads(line[len("msgid ") :])
        elif line.startswith("msgstr "):
            translation = json.loads(line[len("msgstr ") :])
        elif not raw_line and context is not None:
            if obsolete:
                obsolete_entries.append(
                    PoEntry(context, message, translation, frozenset(entry_flags))
                )
            else:
                if context in translations:
                    raise ValueError(f"duplicate msgctxt in PO: {context}")
                translations[context] = translation
                msgids[context] = message
                if entry_flags:
                    flags[context] = set(entry_flags)
            context = None
            message = ""
            translation = ""
            entry_flags = set()
            obsolete = False
    return PoCatalog(translations, flags, msgids, tuple(obsolete_entries))


def apply_catalog(
    source_root: Path,
    output_root: Path,
    units: Iterable[TranslationUnit],
    catalog: Mapping[str, str],
    *,
    preserve_index_sorting: bool = False,
) -> None:
    """Copy a source tree and apply validated non-empty translations.

    Preview builds can retain each translated index entry's source-language
    sort key so Texinfo emits the same index groups and stable anchors.
    """

    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    if output_root.exists():
        raise FileExistsError(f"output tree already exists: {output_root}")
    shutil.copytree(source_root, output_root)
    grouped: dict[str, list[TranslationUnit]] = {}
    for unit in units:
        grouped.setdefault(unit.relative_file, []).append(unit)
    unknown = set(catalog) - {unit.msgctxt for group in grouped.values() for unit in group}
    if unknown:
        raise ValueError(f"catalog contains unknown msgctxt: {sorted(unknown)[0]}")

    for relative, file_units in grouped.items():
        source_path = source_root / relative
        text = source_path.read_bytes().decode("utf-8")
        changed = False
        for unit in sorted(file_units, key=lambda value: value.start, reverse=True):
            candidate = catalog.get(unit.msgctxt, "")
            if not candidate:
                continue
            candidate = _normalise(candidate)
            if protected_values(
                candidate, protect_names=unit.relative_file == "CREDITS.txt"
            ) != unit.protected_values:
                raise ValueError(f"protected content changed for {unit.unit_id}")
            original = _normalise(text[unit.start : unit.end])
            if original != unit.msgid:
                raise ValueError(f"source text changed for {unit.unit_id}")
            replacement = candidate
            if preserve_index_sorting and unit.role == "index":
                sort_key = unit.msgid
                previous = None
                while previous != sort_key:
                    previous = sort_key
                    sort_key = re.sub(r"@var\{([^{}]*)\}", r"\1", sort_key)
                unsupported = re.search(r"@(?![@{}])", sort_key)
                if unsupported is not None:
                    raise ValueError(
                        f"unsupported Texinfo command in index sort key for {unit.unit_id}"
                    )
                replacement = f"{candidate} @sortas{{{sort_key}}}"
            text = text[: unit.start] + replacement + text[unit.end :]
            changed = True
        if changed:
            (output_root / relative).write_bytes(text.encode("utf-8"))
