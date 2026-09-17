# The generated pipeline: layout and code templates

What step 5 emits. One directory per output file, shared machinery in
`common/`, everything streaming.

These are **templates, not a library** — they are emitted into the user's
output tree and then refined. Fill the `{{...}}` slots from the mapping digest.
The style is deliberately the repository's existing style (see
`reuse-existing-generators.md`): `from __future__ import annotations`, frozen
dataclasses, explicit typing imports, prose docstrings, `"".format()`.

## Tree

```
<out_root>/
  README.md
  requirements.txt
  common/
    __init__.py
    connection.py
    part_writer.py
    rules.py
    sanitizer.py
    validate.py
    run_log.py
  pipelines/
    __init__.py
    <slug>/                      # one per mapping-document file name
      __init__.py
      extract.sql
      layout.py
      extract.py
      transforms.py
      pipeline.py
      config.yml
  run.py
  tests/
    fakes.py
    test_<slug>_transforms.py
    test_part_writer_split.py
    test_layout.py
```

Why one directory per file: a mapping document commonly specifies several
loader files that share nothing but the source database. Keeping each file's
layout, query, and transforms together means a change to the member loader
cannot touch the claim loader, and the directory can be handed to whoever owns
that feed. `common/` is the only shared surface, and it is deliberately thin.

## `pipelines/<slug>/layout.py` — the layout is data

The single source of column order and width. Nothing else may decide what
column goes where.

```python
"""Field layout for the {{FILE_NAME}} loader file.

Generated from {{WORKBOOK_NAME}} (sha256 {{SHA8}}), sheet '{{SHEET}}'.
Do not hand-edit the ordinals: regenerate from the mapping document instead,
so the layout and the transformation mapper cannot drift apart.

Ordinal is the mapping document's `loader file column`; it is authoritative and
contiguous. Width checks use `size`; `base_type` drives the cast and the pad
direction ({{PAD_CONVENTION}}).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class FieldSpec:
    ordinal: int
    name: str                       # mapping document column C
    base_type: str                  # CHAR | VARCHAR | NUMBER | DECIMAL | DATE | INT
    size: int
    scale: Optional[int]            # from a `9(7)V99` / `18,2` size
    fmt: Optional[str]              # target `format`
    mandatory: bool
    rule_name: str                  # resolves in transforms.RULES
    source_field: Optional[str]     # None for a constant
    constant: Optional[str] = None  # set when rule_name == "constant"
    loader_name: Optional[str] = None  # loader's own field name, when it differs


FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec(1, "MEMBER_ID", "CHAR", 12, None, None, True,
              "pad_left_zero", "MemberKey"),
    FieldSpec(2, "LAST_NAME", "CHAR", 30, None, None, True,
              "trim_upper", "LastName"),
    FieldSpec(3, "EFF_DATE", "DATE", 8, None, "YYYYMMDD", True,
              "date_yyyymmdd", "EffectiveDate"),
    # {{ONE ROW PER INCLUDED FIELD, IN ORDINAL ORDER}}
)

#: Fields the mapping document marked `target file needed = N`. Kept so the
#: exclusion stays visible and reversible; never written to the output.
EXCLUDED: Tuple[str, ...] = ({{EXCLUDED_NAMES}},)


def header_names() -> Tuple[str, ...]:
    """Loader field names in ordinal order, for the optional header row."""
    return tuple(f.loader_name or f.name for f in FIELDS)


def validate_layout() -> None:
    """Fail fast on a layout the mapping document could not have meant.

    Contiguous ordinals from 1, no duplicate names, every rule resolvable. This
    runs at import time in ``pipeline.py`` because a gap in the ordinals shifts
    every following field in the delimited line - a failure the loader reports
    as a data problem three days later, not as a layout problem now.
    """
    ordinals = [f.ordinal for f in FIELDS]
    if ordinals != list(range(1, len(FIELDS) + 1)):
        raise ValueError("ordinals are not contiguous from 1: {}".format(ordinals))
    names = [f.name for f in FIELDS]
    if len(set(names)) != len(names):
        raise ValueError("duplicate target column names in layout")
```

## `pipelines/<slug>/extract.sql` — the approved query, verbatim

```sql
-- Source query for the {{FILE_NAME}} loader file.
--
-- Provided by / approved by: {{WHO}} on {{WHEN}}.
-- Base tables: {{BASE_TABLES}}
-- Grain: one row per {{GRAIN}}  (identity column: {{IDENTITY_COLUMN}})
--
-- The ORDER BY is not cosmetic. Part boundaries are positional, so without a
-- deterministic order a re-run splits the same rows into different files and a
-- re-transmission does not reconcile against the first one.
--
-- SELECT only. Nothing in this pipeline writes to the source database.

SELECT
      m.[MemberKey]        AS [MemberKey]
    , m.[LastName]         AS [LastName]
    , m.[EffectiveDate]    AS [EffectiveDate]
    -- {{ONE LINE PER DISTINCT source field REACHED BY ANY RULE}}
FROM {{DRIVING_TABLE}} AS m
{{JOINS}}
WHERE 1 = 1
  {{FILTERS -- e.g. AND m.[EffectiveDate] <= ?}}
ORDER BY m.[{{IDENTITY_COLUMN}}]
```

Rules for this file: if the user supplied a query, it goes in **unchanged**
apart from an appended `ORDER BY` (flagged to them) and the header comment.
Never silently rewrite a query someone else is accountable for. If you drafted
it, say so in the header and get it approved before step 6.

## `pipelines/<slug>/extract.py` — a generator, not a fetchall

```python
"""Streamed read of {{FILE_NAME}}'s source query.

A generator by necessity, not by style: these extracts run to millions of rows
and the whole point of a batched read is that no run holds the result set in
memory. Never add a ``.fetchall()`` here.

Schema is pinned from the mapping document's `source type` column rather than
inferred, so a batch in which a nullable column happens to be entirely null
cannot change a column's type midway through a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple

_SQL_PATH = Path(__file__).resolve().parent / "extract.sql"

BATCH_SIZE = 50_000

#: source field -> declared source type, from the mapping document.
SOURCE_TYPES: Dict[str, str] = {{{SOURCE_TYPES}}}


def read_sql() -> str:
    """The approved query text. Read from disk so a DBA can edit it in place."""
    return _SQL_PATH.read_text(encoding="utf-8")


def read_batches(conn: Any, params: Sequence[Any] = (),
                 batch_size: int = BATCH_SIZE) -> Iterator[Tuple[List[str], List[Tuple]]]:
    """Yield ``(columns, rows)`` per batch until the cursor is drained.

    Columns are yielded with every batch so a consumer never has to assume
    ordinal positions, and ``conn`` is injected rather than opened here - that
    is the seam the offline tests use.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(read_sql(), *params)
        columns = [d[0] for d in cursor.description]
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield columns, [tuple(r) for r in rows]
    finally:
        cursor.close()
```

## `pipelines/<slug>/transforms.py` — one named function per rule

```python
"""Transformations for the {{FILE_NAME}} loader file.

One function per distinct transformation rule in the mapping document, with
that rule quoted verbatim in the docstring. Functions are resolved from
``RULES`` by exact name - nothing here is ever ``eval``'d, so an unrecognised
rule name is a generation-time error rather than a runtime surprise.

Every function takes the raw source value (or a dict of them, for a multi-input
rule) plus its ``FieldSpec`` and returns a ``str`` ready for the delimited
line. Returning ``None`` means "emit the null sentinel"; raising
``TransformError`` means the row cannot be produced and the configured
violation policy decides what happens to it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Dict, Mapping, Optional


class TransformError(ValueError):
    """A value the mapping document's rule cannot render.

    Carries the field name because the run log needs to say which column of
    which row failed, not just that something did.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__("{}: {}".format(field, message))
        self.field = field


RULES: Dict[str, Callable[..., Optional[str]]] = {}


def rule(name: str) -> Callable:
    """Register a transformation under the name ``layout.py`` refers to."""

    def _decorator(fn: Callable) -> Callable:
        if name in RULES:
            raise ValueError("duplicate rule name: {}".format(name))
        RULES[name] = fn
        return fn

    return _decorator


# ---------------------------------------------------------------------------
# Passthrough and constants
# ---------------------------------------------------------------------------

@rule("passthrough")
def passthrough(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "Direct move".

    Description: {{DESCRIPTION}}
    """
    return None if value is None else str(value)


@rule("constant")
def constant(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "Hardcode {{LITERAL}}".

    Ignores the source entirely; ``spec.constant`` carries the literal so the
    value stays visible in the layout rather than buried in a function.
    """
    return spec.constant


# ---------------------------------------------------------------------------
# String rules
# ---------------------------------------------------------------------------

@rule("pad_left_zero")
def pad_left_zero(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "Left pad with zeroes to {{SIZE}}".

    Description: {{DESCRIPTION}}

    A value already wider than ``spec.size`` is a spec violation, not something
    to quietly slice: the mapping document promised the loader a {{SIZE}}-wide
    key and the source produced something that does not fit.
    """
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > spec.size:
        raise TransformError(spec.name,
                             "value {!r} exceeds size {}".format(text, spec.size))
    return text.rjust(spec.size, "0")


@rule("trim_upper")
def trim_upper(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "Trim and convert to upper case".

    Description: {{DESCRIPTION}}
    """
    return None if value is None else str(value).strip().upper()


# ---------------------------------------------------------------------------
# Date rules - parse per `source format`, render per `format`
# ---------------------------------------------------------------------------

@rule("date_yyyymmdd")
def date_yyyymmdd(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "Format as YYYYMMDD".

    Source format: {{SOURCE_FORMAT}}

    Parses to a real date before rendering rather than slicing the string. A
    source that switches between ``2024-01-15`` and ``20240115`` breaks string
    slicing silently and breaks a parse loudly.
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise TransformError(spec.name, "unparseable date {!r}".format(text))


# ---------------------------------------------------------------------------
# Code translation - from the mapping document's `codes` column
# ---------------------------------------------------------------------------

#: {{TARGET_COLUMN}} codes, transcribed from the mapping document's `codes`
#: column. {{CODE_SET_PROVENANCE}}
{{CODE_SET_NAME}}: Dict[str, str] = {{{CODE_PAIRS}}}


@rule("{{CODE_RULE_NAME}}")
def {{CODE_RULE_NAME}}(value: Any, spec: Any) -> Optional[str]:
    """Mapping rule: "{{VERBATIM_RULE}}".

    Unmatched-value policy: {{UNMATCHED_POLICY}} - as confirmed by the user,
    because the mapping document did not state one.
    """
    if value is None:
        return None
    key = str(value).strip().upper()
    try:
        return {{CODE_SET_NAME}}[key]
    except KeyError:
        raise TransformError(spec.name, "unmapped code {!r}".format(key))


def apply_field(spec: Any, row: Mapping[str, Any]) -> Optional[str]:
    """Run one field's rule against a source row.

    Looked up by name, never constructed dynamically.
    """
    fn = RULES.get(spec.rule_name)
    if fn is None:
        raise KeyError("unknown rule {!r} for field {}; known: {}".format(
            spec.rule_name, spec.name, sorted(RULES)))
    raw = None if spec.source_field is None else row.get(spec.source_field)
    return fn(raw, spec)
```

## `common/part_writer.py` — pipe-delimited, splits on rows **and** bytes

Adapted from `extract-engine/scripts/extract_engine/execution_polars.py`'s
`PartWriter`. The additions are the byte-size trigger and the
whichever-trips-first semantics; the binary-mode/explicit-line-ending decision
and the boundary-exact slicing come straight from the original and should not
be "simplified" away.

```python
"""Pipe-delimited part writer with record-count and byte-size splitting.

Adapted from skills/claude-skills/extract-engine/scripts/extract_engine/
execution_polars.py. Two behaviours carried over deliberately:

* Handles open in **binary** mode with an explicit ``line_ending``. Text mode
  on Windows translates ``\\n`` to ``\\r\\n`` silently, which corrupts a file
  whose vendor spec says LF.
* A roll happens at the exact boundary. A row is never split across parts and
  no row is lost or duplicated at the seam.

Added here: ``max_bytes_per_file``. When both limits are set, whichever trips
first rolls the part - and the byte check is applied **before** the line is
written, because a part that overshoots the vendor's limit by one line is as
rejected as one that overshoots by a thousand.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class PartWriterResult:
    parts: List[Path]
    row_count: int
    part_count: int
    checksum: str
    bytes_written: int


@dataclass
class PartWriter:
    """Writes rows to pipe-delimited parts, rolling on rows and/or bytes.

    At least one of ``max_rows_per_file`` / ``max_bytes_per_file`` must be set.
    There is no default: the split rule comes from the vendor's requirement,
    and guessing it produces a transmission that is rejected rather than wrong
    by a little.
    """

    output_dir: Path
    file_stem: str
    delimiter: str = "|"
    line_ending: bytes = b"\r\n"
    null_sentinel: str = ""
    max_rows_per_file: Optional[int] = None
    max_bytes_per_file: Optional[int] = None
    emit_header_row: bool = False
    emit_trailer_row: bool = True
    trailer_prefix: str = "TRAILER"
    part_name_template: str = "{stem}_part{part:03d}.txt"
    encoding: str = "utf-8"
    header_names: Sequence[str] = ()

    _part_number: int = field(default=1, init=False)
    _rows_in_part: int = field(default=0, init=False)
    _bytes_in_part: int = field(default=0, init=False)
    _total_rows: int = field(default=0, init=False)
    _total_bytes: int = field(default=0, init=False)
    _handle: Any = field(default=None, init=False)
    _parts_written: List[Path] = field(default_factory=list, init=False)
    _hasher: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not self.max_rows_per_file and not self.max_bytes_per_file:
            raise ValueError(
                "a split rule is required: set max_rows_per_file, "
                "max_bytes_per_file, or both")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._hasher = hashlib.sha256()

    # -- part lifecycle ----------------------------------------------------

    def _part_path(self, part_number: int) -> Path:
        return self.output_dir / self.part_name_template.format(
            stem=self.file_stem, part=part_number)

    def _open_part(self) -> None:
        path = self._part_path(self._part_number)
        self._handle = open(path, "wb")
        self._parts_written.append(path)
        self._rows_in_part = 0
        self._bytes_in_part = 0
        if self.emit_header_row and self.header_names:
            self._write_line(self.delimiter.join(self.header_names))

    def _roll(self) -> None:
        self._close_part()
        self._part_number += 1
        self._open_part()

    def _encode(self, text: str) -> bytes:
        return text.encode(self.encoding) + self.line_ending

    def _write_line(self, text: str) -> None:
        raw = self._encode(text)
        self._handle.write(raw)
        self._hasher.update(raw)
        self._bytes_in_part += len(raw)
        self._total_bytes += len(raw)

    def _trailer_bytes(self) -> int:
        """Space the trailer will need, reserved when sizing a part.

        A trailer written past the byte limit defeats the limit, so the byte
        budget accounts for it up front rather than discovering it at close.
        """
        if not self.emit_trailer_row:
            return 0
        return len(self._encode("{}{}{}".format(
            self.trailer_prefix, self.delimiter, self._rows_in_part)))

    # -- writing -----------------------------------------------------------

    def _row_to_line(self, row: Sequence[Any]) -> str:
        return self.delimiter.join(
            self.null_sentinel if v is None else str(v) for v in row)

    def write_row(self, row: Sequence[Any]) -> None:
        if self._handle is None:
            self._open_part()

        line = self._row_to_line(row)
        raw_len = len(self._encode(line))

        rows_full = (self.max_rows_per_file is not None
                     and self._rows_in_part >= self.max_rows_per_file)
        bytes_full = (self.max_bytes_per_file is not None
                      and self._rows_in_part > 0
                      and self._bytes_in_part + raw_len + self._trailer_bytes()
                      > self.max_bytes_per_file)
        if rows_full or bytes_full:
            self._roll()

        self._write_line(line)
        self._rows_in_part += 1
        self._total_rows += 1

    def write_rows(self, rows: Iterable[Sequence[Any]]) -> None:
        for row in rows:
            self.write_row(row)

    def _close_part(self) -> None:
        if self._handle is not None:
            if self.emit_trailer_row:
                self._write_line("{}{}{}".format(
                    self.trailer_prefix, self.delimiter, self._rows_in_part))
            self._handle.close()
            self._handle = None

    def finish(self) -> PartWriterResult:
        self._close_part()
        return PartWriterResult(
            parts=list(self._parts_written),
            row_count=self._total_rows,
            part_count=len(self._parts_written),
            checksum=self._hasher.hexdigest(),
            bytes_written=self._total_bytes,
        )

    def __enter__(self) -> "PartWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.finish()
        elif self._handle is not None:
            self._handle.close()
            self._handle = None
```

Note the `_rows_in_part > 0` guard on `bytes_full`: a single row longer than
`max_bytes_per_file` would otherwise roll forever, producing empty parts. One
oversized row means the limit and the layout are inconsistent — let it write
and report it, rather than looping.

## `pipelines/<slug>/pipeline.py` — the orchestration generator

```python
"""End-to-end pipeline for the {{FILE_NAME}} loader file.

extract -> transform -> validate -> sanitize -> write, composed as generators
so each stage sees one batch at a time and the run's memory is flat in the row
count. ``run()`` is the only function here that touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ...common.part_writer import PartWriter, PartWriterResult
from ...common.sanitizer import sanitize_value
from ...common.validate import ViolationPolicy, check_field
from . import extract, layout, transforms

layout.validate_layout()


def transform_rows(columns: Sequence[str],
                   rows: Sequence[Tuple]) -> Iterator[List[Optional[str]]]:
    """One source row in, one ordered list of rendered fields out."""
    for raw in rows:
        row = dict(zip(columns, raw))
        yield [transforms.apply_field(spec, row) for spec in layout.FIELDS]


def finalize_rows(rendered: Iterator[List[Optional[str]]],
                  policy: ViolationPolicy,
                  delimiter: str) -> Iterator[List[Optional[str]]]:
    """Apply mandatory/size checks and delimiter sanitization, in that order.

    Order matters: a sanitization that replaces a delimiter can change a
    value's length, so the width check runs against the sanitized value.
    """
    for values in rendered:
        out: List[Optional[str]] = []
        rejected = False
        for spec, value in zip(layout.FIELDS, values):
            value = sanitize_value(value, delimiter, policy.collision)
            verdict = check_field(spec, value, policy)
            if verdict.reject_row:
                rejected = True
                break
            out.append(verdict.value)
        if not rejected:
            yield out


def run(conn: Any, config: Dict[str, Any],
        params: Sequence[Any] = ()) -> PartWriterResult:
    """Produce the loader file's parts. Returns the manifest for the run log."""
    policy = ViolationPolicy.from_config(config)
    with PartWriter(
        output_dir=Path(config["output_dir"]),
        file_stem=config["file_stem"],
        delimiter=config["delimiter"],
        line_ending=b"\r\n" if config["line_ending"] == "CRLF" else b"\n",
        null_sentinel=config["null_sentinel"],
        max_rows_per_file=config.get("max_rows_per_file"),
        max_bytes_per_file=config.get("max_bytes_per_file"),
        emit_header_row=config.get("emit_header_row", False),
        emit_trailer_row=config.get("emit_trailer_row", True),
        part_name_template=config.get(
            "part_name_template", "{stem}_part{part:03d}.txt"),
        header_names=layout.header_names(),
    ) as writer:
        for columns, rows in extract.read_batches(conn, params):
            writer.write_rows(
                finalize_rows(transform_rows(columns, rows),
                              policy, config["delimiter"]))
        return writer.finish()
```

## `pipelines/<slug>/config.yml`

Everything the user answered in step 3 that is not a transformation. No value
in this file may be a guess; a field the user did not specify is absent and the
run fails loudly on it.

```yaml
# {{FILE_NAME}} loader file - runtime contract.
# Every value here was confirmed with the requester on {{WHEN}}; see the
# transformation mapper's "Split and record-count contract" section.
file_stem: {{FILE_STEM}}
output_dir: {{OUTPUT_DIR}}
delimiter: "|"
line_ending: {{CRLF|LF}}
null_sentinel: ""
emit_header_row: {{true|false}}
emit_trailer_row: {{true|false}}
part_name_template: "{{PART_NAME_TEMPLATE}}"

# Split rule - asked for, never defaulted. Whichever trips first rolls.
max_rows_per_file: {{ROWS_OR_NULL}}
max_bytes_per_file: {{BYTES_OR_NULL}}

# Violation policy per the requester's answers.
on_mandatory_null: {{fail_run|reject_row|substitute}}
on_size_overflow: {{fail_run|reject_row|truncate}}
on_delimiter_collision: {{fail|replace|strip}}
on_unmapped_code: {{fail_run|reject_row|passthrough}}
```

## `run.py` — argparse, matching `sql-server-schema/scripts/cli.py`

Four verbs, and the three read-only ones need no database:

```
run.py list                              # pipelines discovered under pipelines/
run.py validate-layout --pipeline <slug> # layout contiguity + rule resolution
run.py dry-run --pipeline <slug>         # print resolved SQL, layout, split rule
run.py run --pipeline <slug> [--output-dir ...] [--max-rows ...] [--max-bytes ...]
```

`dry-run` before `run`, always. It is the cheapest possible check that the
mapping document was read correctly, and it costs nothing.

## `tests/`

Offline, against `fakes.py`'s scripted-cursor `FakeConnection` — the same shape
as `extract-engine/tests/fakes.py`. Required cases:

- **One test per rule**, using the mapping document's own stated examples where
  it gives any. A rule with no example is a gap worth naming in the mapper.
- **`test_part_writer_split.py`**: exact boundaries. N rows at limit K produce
  `ceil(N/K)` parts with no lost or duplicated row; a byte limit rolls before
  overshooting; both limits together roll on whichever trips first; the
  oversized-single-row case writes rather than looping.
- **`test_layout.py`**: ordinal contiguity, no duplicate names, every
  `rule_name` present in `transforms.RULES`, and the rendered line's field
  count equals `len(FIELDS)`.
- **Golden line test**: one fully-populated fake source row rendered to its
  expected pipe-delimited line, transcribed from the mapping document. This is
  the test that catches an off-by-one in the layout.
