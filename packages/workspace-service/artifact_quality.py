"""Check explicit artifact rules using only the supplied text.

Callers select file_rules by exact required relative path and validate the rule
schema and file syntax separately. min_chars counts non-whitespace characters.
CSV min_rows excludes the header and wholly blank records; required_columns
matches stripped header names exactly. BibTeX counts unique source entries,
excluding comment, preamble and string directives. A locator is a nonempty DOI
or an absolute HTTP(S) URL. These checks do not verify sources or scientific
claims, and URL persistence cannot be established without external evidence.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import urlsplit


_HEADER = re.compile(r"@([a-zA-Z]+)\s*([({])")
_KEY = re.compile(r"[^\s,{}()=\"%]+")
_FIELD = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")
_BARE_VALUE = re.compile(r"[^\s,#{}()\"=%]+")


def validate_rules(contract: dict) -> None:
    """Validate explicit file rules at the workflow/task input boundary."""
    rules = contract.get('file_rules', {})
    if not isinstance(rules, dict):
        raise ValueError('file_rules must be an object keyed by required file path')
    declared = {item.get('relative') or item.get('path') if isinstance(item, dict) else item
                for item in contract.get('required_files', [])}
    allowed = {'min_chars', 'min_rows', 'required_columns', 'min_entries', 'require_title_and_locator'}
    for relative, rule in rules.items():
        if relative not in declared or not isinstance(rule, dict) or set(rule) - allowed:
            raise ValueError(f'{relative}: invalid file_rules or undeclared target')
        for key in ('min_chars', 'min_rows', 'min_entries'):
            if key in rule and (type(rule[key]) is not int or rule[key] < 0):
                raise ValueError(f'{relative}: {key} must be a nonnegative integer')
        if 'require_title_and_locator' in rule and type(rule['require_title_and_locator']) is not bool:
            raise ValueError(f'{relative}: require_title_and_locator must be boolean')
        columns = rule.get('required_columns', [])
        if not isinstance(columns, list) or any(not isinstance(x, str) or not x.strip() for x in columns):
            raise ValueError(f'{relative}: required_columns must contain nonempty column names')
        suffix = PurePosixPath(relative).suffix.lower()
        if suffix != '.csv' and any(key in rule for key in ('min_rows', 'required_columns')):
            raise ValueError(f'{relative}: CSV rules require a .csv target')
        if suffix != '.bib' and any(key in rule for key in ('min_entries', 'require_title_and_locator')):
            raise ValueError(f'{relative}: bibliography rules require a .bib target')


class _BibTeX:
    """Read entry boundaries and field values without splitting nested braces."""

    def __init__(self, relative: str, content: str):
        self.relative = relative
        self.text = content
        self.pos = 0

    def fail(self, message: str) -> None:
        raise ValueError(f"{self.relative}: BibTeX {message} at character {self.pos + 1}")

    def space(self) -> None:
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text[self.pos] == "%":
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end + 1
            else:
                break

    def expect(self, token: str) -> None:
        self.space()
        if not self.text.startswith(token, self.pos):
            self.fail(f"expected {token!r}")
        self.pos += len(token)

    def literal(self) -> str:
        opening = self.text[self.pos]
        quoted = opening == '"'
        depth = 0 if quoted else 1
        self.pos += 1
        start = self.pos
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 2
                continue
            if char == '"' and quoted and depth == 0:
                result = self.text[start:self.pos]
                self.pos += 1
                return result
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if not quoted and depth == 0:
                    result = self.text[start:self.pos]
                    self.pos += 1
                    return result
                if depth < 0:
                    self.fail("unbalanced braces in quoted value")
            self.pos += 1
        self.fail("unterminated quoted value" if quoted else "unterminated braced value")

    def value(self) -> str:
        parts = []
        while True:
            self.space()
            if self.pos >= len(self.text):
                self.fail("missing field value")
            if self.text[self.pos] in '{"':
                parts.append(self.literal())
            else:
                token = _BARE_VALUE.match(self.text, self.pos)
                if not token:
                    self.fail("missing field value")
                parts.append(token[0])
                self.pos += len(token[0])
            self.space()
            if not self.text.startswith("#", self.pos):
                return "".join(parts)
            self.pos += 1

    def skip_directive(self, opening: str) -> None:
        if opening == "{":
            self.literal()
            return
        self.pos += 1
        depth = 1
        while self.pos < len(self.text):
            self.space()
            if self.pos >= len(self.text):
                break
            char = self.text[self.pos]
            if char in '{"':
                self.literal()
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    self.pos += 1
                    return
            self.pos += 2 if char == "\\" else 1
        self.fail("unterminated directive")

    def entries(self) -> dict[str, dict[str, str]]:
        entries = {}
        while self.pos < len(self.text):
            self.space()
            header = _HEADER.match(self.text, self.pos)
            if not header:
                self.pos += 1
                continue
            kind, opening = header.groups()
            self.pos += len(header[0]) - 1
            if kind.lower() in {"comment", "preamble", "string"}:
                self.skip_directive(opening)
                continue
            self.pos += 1
            closing = "}" if opening == "{" else ")"
            self.space()
            key = _KEY.match(self.text, self.pos)
            if not key:
                self.fail("missing citation key")
            key = key[0]
            if key in entries:
                self.fail(f"duplicate citation key {key!r}")
            self.pos += len(key)
            fields = {}
            self.space()
            if not self.text.startswith(closing, self.pos):
                self.expect(",")
            while True:
                self.space()
                if self.text.startswith(closing, self.pos):
                    self.pos += 1
                    break
                name = _FIELD.match(self.text, self.pos)
                if not name:
                    self.fail(f"expected field name in entry {key!r}")
                self.pos += len(name[0])
                self.expect("=")
                field = name[0].lower()
                if field in fields:
                    self.fail(f"duplicate field {field!r} in entry {key!r}")
                fields[field] = self.value()
                if not self.text.startswith(closing, self.pos):
                    self.expect(",")
            entries[key] = fields
        return entries


def _nonempty(value: str) -> bool:
    return any(not char.isspace() and char not in "{}" for char in value)


def _http_url(value: str) -> bool:
    value = value.strip()
    if any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _doi_or_url(value: str) -> bool:
    value = value.strip()
    return _http_url(value) or bool(re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I))


def candidate_sources(content: str) -> list[dict[str, str]]:
    """Read the title and stable locator used to identify an accepted candidate."""
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
        if not reader.fieldnames or not {"title", "doi_or_stable_url"}.issubset(reader.fieldnames):
            raise ValueError("candidate_papers.csv requires title and doi_or_stable_url columns")
        sources = []
        seen = set()
        for number, row in enumerate(reader, start=2):
            title = (row.get("title") or "").strip()
            locator = (row.get("doi_or_stable_url") or "").strip()
            if not title or not _doi_or_url(locator):
                raise ValueError(f"candidate_papers.csv row {number} requires title and DOI or stable URL")
            key = (_title_key(title), _locator_key(locator))
            if key in seen:
                raise ValueError(f"candidate_papers.csv row {number} duplicates a title and locator")
            seen.add(key)
            sources.append({"title": title, "doi_or_stable_url": locator})
        return sources
    except csv.Error as exc:
        raise ValueError(f"candidate_papers.csv: invalid CSV: {exc}") from exc


def _title_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _locator_key(value: str) -> str:
    value = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I).rstrip("/")
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.I):
        return "doi:" + doi.casefold()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold()
    if parsed.port:
        host += ":" + str(parsed.port)
    return "url:" + host + parsed.path.rstrip("/") + ("?" + parsed.query if parsed.query else "")


def validate_methods_metrics_candidates(relative: str, content: str, candidates_content: str) -> None:
    """Require every metrics row to map to, and cover, the accepted A pool."""
    sources = candidate_sources(candidates_content)
    if not sources:
        raise ValueError(f"{relative}: accepted candidate pool is empty")
    accepted = {(_title_key(item["title"]), _locator_key(item["doi_or_stable_url"])): item["title"]
                for item in sources}
    seen = set()
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
        for number, row in enumerate(reader, start=2):
            citation = (row.get("citation") or "").strip()
            locator = _locator_key((row.get("doi_or_stable_url") or "").strip())
            key = (_title_key(citation), locator)
            if key not in accepted:
                raise ValueError(f"{relative}: CSV row {number} does not match an accepted candidate title and DOI or stable URL")
            seen.add(key)
        missing = [title for key, title in accepted.items() if key not in seen]
        if missing:
            raise ValueError(f"{relative}: CSV is missing accepted A candidates: {'; '.join(missing)}")
    except csv.Error as exc:
        raise ValueError(f"{relative}: invalid CSV: {exc}") from exc


def validate(relative: str, content: str, rule: dict) -> None:
    """Raise a file-specific ValueError when an explicit rule is not satisfied."""
    if "min_chars" in rule:
        count = sum(not char.isspace() for char in content)
        if count < rule["min_chars"]:
            raise ValueError(f"{relative}: min_chars requires at least {rule['min_chars']} "
                             f"non-whitespace characters; found {count}")

    suffix = PurePosixPath(relative).suffix.lower()
    methods_metrics = PurePosixPath(relative).name.casefold() == "methods_metrics.csv"
    if suffix == ".csv" and (methods_metrics or any(key in rule for key in ("min_rows", "required_columns"))):
        try:
            rows = [row for row in csv.reader(io.StringIO(content, newline=""), strict=True)
                    if any(cell.strip() for cell in row)]
            header = rows[0] if rows else None
            if header is None:
                raise ValueError(f"{relative}: CSV requires a header row")
            columns = {cell.strip().removeprefix("\ufeff").strip() for cell in header}
            missing = [name for name in rule.get("required_columns", []) if name not in columns]
            if missing:
                raise ValueError(f"{relative}: CSV missing required_columns: {', '.join(missing)}")
            count = len(rows) - 1
            if methods_metrics:
                expected = {"citation", "interface", "modification_method", "material_system",
                            "evidence_level", "comparability_note", "doi_or_stable_url"}
                absent = sorted(expected - columns)
                if absent:
                    raise ValueError(f"{relative}: CSV missing required_columns: {', '.join(absent)}")
                reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
                for number, row in enumerate(reader, start=2):
                    if not any((value or "").strip() for value in row.values() if isinstance(value, str)):
                        continue
                    citation = (row.get("citation") or "").strip()
                    locator = (row.get("doi_or_stable_url") or "").strip()
                    if (not citation or citation.casefold().startswith("result_id=") or
                            not _doi_or_url(locator)):
                        raise ValueError(f"{relative}: CSV row {number} requires citation and DOI or stable URL")
                    if not (row.get("comparability_note") or "").strip():
                        raise ValueError(f"{relative}: CSV row {number} requires a comparability_note")
        except csv.Error as exc:
            raise ValueError(f"{relative}: invalid CSV: {exc}") from exc
        if count < rule.get("min_rows", 0):
            raise ValueError(f"{relative}: CSV min_rows requires at least {rule['min_rows']} "
                             f"data rows; found {count}")

    if suffix == ".bib" and any(key in rule for key in ("min_entries", "require_title_and_locator")):
        entries = _BibTeX(relative, content).entries()
        if rule.get("require_title_and_locator"):
            for key, fields in entries.items():
                missing = []
                if not _nonempty(fields.get("title", "")):
                    missing.append("nonempty title")
                if not (_nonempty(fields.get("doi", "")) or _http_url(fields.get("url", ""))):
                    missing.append("nonempty doi or absolute HTTP(S) url")
                if missing:
                    raise ValueError(f"{relative}: BibTeX entry {key!r} requires {' and '.join(missing)}")
        if len(entries) < rule.get("min_entries", 0):
            raise ValueError(f"{relative}: BibTeX min_entries requires at least {rule['min_entries']} "
                             f"source entries; found {len(entries)}")
