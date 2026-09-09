"""
execution_polars.py - execution path A: transform in process with Polars.

Structured as independently testable pure functions (transform, sanitize,
row_sequence assignment, part-writing) plus a thin ``run_dataset``
orchestration function that wires them to a real database connection. The
pure pieces are what tests/test_memory_ceiling.py and friends exercise with a
synthetic batch generator; ``run_dataset`` itself needs a live connection and
is covered by the LocalDB-gated acceptance walkthrough instead.

Per docs/extract-engine-mvp-prompt-v2-polars.md: schema is derived from
meta.field_map and passed as schema_overrides on every read - never left to
per-batch inference, which can silently change dtype on a batch where a
nullable column happens to be entirely null. The whole transform for a batch
is one df.select(...) call - no Python loops over rows, no map_elements, no
apply, anywhere in this module.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import polars as pl

from . import rules as rules_mod
from . import sanitizer
from .metadata import Dataset, DatasetLookup, FieldMap, Feed
from .query_builder import ReadPlan, build_select

# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_batches(conn: Any, plan: ReadPlan, batch_size: int = 100_000) -> Iterator[pl.DataFrame]:
    """Stream the source query in batches, schema pinned, never inferred."""
    return pl.read_database(
        plan.sql,
        conn,
        iter_batches=True,
        batch_size=batch_size,
        execute_options={"parameters": plan.params} if plan.params else None,
        schema_overrides=plan.schema_overrides,
    )


# ---------------------------------------------------------------------------
# Transforming: one df.select(exprs) per batch, built once per dataset.
# ---------------------------------------------------------------------------


def build_transform_exprs(field_maps: Sequence[FieldMap]) -> List[Tuple[str, pl.Expr]]:
    """Non-stateful field_maps only, in ordinal order.

    Stateful fields (row_sequence) are excluded here and added afterward by
    ``add_row_sequence_columns``, since they need a per-batch running offset
    that a stateless per-batch expression cannot express.
    """
    exprs: List[Tuple[str, pl.Expr]] = []
    for fm in sorted(field_maps, key=lambda f: f.ordinal):
        rule_obj = rules_mod.get_rule(fm.rule_name)
        if rules_mod.is_stateful(rule_obj):
            continue
        transformed = rule_obj.polars_fn(pl.col(fm.source_column), fm.rule_params)
        if fm.default_value is not None:
            transformed = transformed.fill_null(fm.default_value)
        exprs.append((fm.target_column, transformed.alias(fm.target_column)))
    return exprs


def apply_transform(batch: pl.DataFrame, field_maps: Sequence[FieldMap]) -> pl.DataFrame:
    """The whole transform for one batch: a single select, no per-row Python."""
    exprs = build_transform_exprs(field_maps)
    return batch.select([expr for _, expr in exprs])


def add_row_sequence_columns(
    df: pl.DataFrame, field_maps: Sequence[FieldMap], running_offsets: Dict[str, int]
) -> pl.DataFrame:
    """Assign row_sequence columns using a per-target running offset.

    ``running_offsets`` is mutated in place (target_column -> next start
    value), so the caller reuses the same dict across batches within one
    dataset to get a continuous sequence.
    """
    result = df
    n = df.height
    for fm in field_maps:
        rule_obj = rules_mod.get_rule(fm.rule_name)
        if not rules_mod.is_stateful(rule_obj):
            continue
        start = running_offsets.get(fm.target_column, 1)
        result = result.with_columns(pl.int_range(start, start + n, eager=True).alias(fm.target_column))
        running_offsets[fm.target_column] = start + n
    return result


def reorder_columns(df: pl.DataFrame, field_maps: Sequence[FieldMap]) -> pl.DataFrame:
    """Final column order: ordinal order from meta.field_map, not select order."""
    ordered_targets = [fm.target_column for fm in sorted(field_maps, key=lambda f: f.ordinal)]
    return df.select(ordered_targets)


# ---------------------------------------------------------------------------
# Sanitizing collisions.
# ---------------------------------------------------------------------------


def apply_sanitization(df: pl.DataFrame, feed: Feed, batch_ordinal: int = -1) -> pl.DataFrame:
    """Sanitize every string column, or abort on the first collision.

    'fail' mode checks every string column and raises sanitizer.CollisionError
    naming only the column and batch ordinal - never the value - if any hit is
    found, leaving the data otherwise untouched. 'sanitize' mode always
    replaces delimiter/CR/LF/TAB, whether or not a collision is present.
    """
    string_columns = [name for name, dtype in df.schema.items() if dtype == pl.Utf8]
    if feed.collision_action == "fail":
        for column in string_columns:
            sanitizer.check_collision_polars(df, column, feed.delimiter, batch_ordinal)
        return df
    exprs = [
        sanitizer.sanitize_expr_polars(pl.col(name), feed.delimiter, feed.collision_char).alias(name)
        if name in string_columns
        else pl.col(name)
        for name in df.columns
    ]
    return df.select(exprs)


# ---------------------------------------------------------------------------
# Key-set accumulation (for dependent-mode pull-in).
# ---------------------------------------------------------------------------


class KeyAccumulator:
    """Accumulates a bounded set of distinct key values across batches.

    Cardinality here is bounded by distinct members/providers/etc., not by
    row volume, so holding the growing unique set in memory is safe even
    though the source table itself may be enormous.
    """

    def __init__(self, key_column: str):
        self.key_column = key_column
        self._parts: List[pl.Series] = []

    def add_batch(self, df: pl.DataFrame) -> None:
        if self.key_column in df.columns:
            self._parts.append(df.get_column(self.key_column).drop_nulls().unique())

    def finish(self) -> List[Any]:
        if not self._parts:
            return []
        return pl.concat(self._parts).unique().to_list()


# ---------------------------------------------------------------------------
# Writing: binary-append, part-rolling, byte-exact line endings.
# ---------------------------------------------------------------------------


@dataclass
class PartWriter:
    """Writes batches to pipe-delimited parts, rolling at max_rows_per_file.

    Opens every handle in binary mode with an explicit encoding - text mode on
    Windows silently translates \\n to \\r\\n, which this class controls
    explicitly instead via ``line_ending`` (bytes, not the platform default).
    A batch that would straddle the row-count boundary is sliced exactly at
    the boundary before either part receives it, never split mid-write.
    """

    output_dir: Path
    file_stem: str
    delimiter: str
    line_ending: bytes  # b"\r\n" or b"\n"
    null_sentinel: str
    max_rows_per_file: int
    emit_header_row: bool = False
    emit_trailer_row: bool = True
    encoding: str = "utf-8"

    _part_number: int = field(default=1, init=False)
    _rows_in_part: int = field(default=0, init=False)
    _total_rows: int = field(default=0, init=False)
    _handle: Any = field(default=None, init=False)
    _columns: Optional[List[str]] = field(default=None, init=False)
    _parts_written: List[Path] = field(default_factory=list, init=False)
    _hasher: Any = field(default=None, init=False)

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._hasher = hashlib.sha256()

    def _part_path(self, part_number: int) -> Path:
        return self.output_dir / "{}_part{:03d}.txt".format(self.file_stem, part_number)

    def _open_part(self) -> None:
        path = self._part_path(self._part_number)
        # Binary mode, explicit encoding: text mode on Windows would silently
        # translate \n to \r\n regardless of what line_ending says.
        self._handle = open(path, "wb")
        self._parts_written.append(path)
        self._rows_in_part = 0
        if self._columns and self.emit_header_row:
            self._write_line(self.delimiter.join(self._columns))

    def _write_line(self, text: str) -> None:
        raw = text.encode(self.encoding) + self.line_ending
        self._handle.write(raw)
        self._hasher.update(raw)

    def _row_to_line(self, row: Sequence[Any]) -> str:
        return self.delimiter.join(
            self.null_sentinel if value is None else str(value) for value in row
        )

    def write_batch(self, df: pl.DataFrame) -> None:
        if self._columns is None:
            self._columns = list(df.columns)
        if self._handle is None:
            self._open_part()

        remaining = df
        while remaining.height > 0:
            space_left = self.max_rows_per_file - self._rows_in_part
            if space_left <= 0:
                self._close_part()
                self._part_number += 1
                self._open_part()
                space_left = self.max_rows_per_file

            chunk = remaining.head(space_left) if remaining.height > space_left else remaining
            for row in chunk.iter_rows():
                self._write_line(self._row_to_line(row))
            self._rows_in_part += chunk.height
            self._total_rows += chunk.height
            remaining = remaining.slice(chunk.height, remaining.height - chunk.height)

    def _close_part(self) -> None:
        if self._handle is not None:
            if self.emit_trailer_row:
                self._write_line("TRAILER{}{}".format(self.delimiter, self._rows_in_part))
            self._handle.close()
            self._handle = None

    def finish(self) -> "PartWriterResult":
        self._close_part()
        return PartWriterResult(
            parts=list(self._parts_written),
            row_count=self._total_rows,
            part_count=len(self._parts_written),
            checksum=self._hasher.hexdigest(),
        )


@dataclass(frozen=True)
class PartWriterResult:
    parts: List[Path]
    row_count: int
    part_count: int
    checksum: str
