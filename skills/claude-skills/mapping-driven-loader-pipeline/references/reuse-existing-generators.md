# Reuse before you write

Step 2 of the skill. The pipeline you are about to generate is a near-sibling
of a system that already exists in this repository, is tested, and carries
fixes that were expensive to find. Scan it first.

## What to scan, and what to take from it

### `skills/claude-skills/extract-engine/` — the closest relative

A metadata-driven engine that already reads SQL Server and writes
pipe-delimited flat files. Read its `SKILL.md` and `DEVELOPER.md` first, then
these modules under `scripts/extract_engine/`:

| Module | Take |
| --- | --- |
| `execution_polars.py` | **`PartWriter`.** The pipe-delimited writer, binary-mode handles with an explicit `line_ending`, boundary-exact row slicing, the per-part trailer, the running SHA-256. Copy it and add the byte-size trigger. Do not write a new one. |
| `rules.py` | The **rule-registry shape**: `@dataclass(frozen=True) Rule`, a module-level `_REGISTRY` dict, a `rule(...)` decorator carrying `params_schema` and `required_params`, `get_rule()` raising `KeyError` that *lists the known names*. Rule names are looked up by exact match and **never `eval`'d**; an unknown name is a validation error before anything runs. |
| `sanitizer.py` | Delimiter/CR/LF/TAB collision handling, and `CollisionError` for the `fail` policy that refuses rather than silently mangling a line. |
| `connection.py` | Windows-auth `read_connection()` / `write_connection()` context managers (read always rolls back), driver enumeration instead of a hardcoded version, and the `conn=` injection parameter that is the seam making every test offline. |
| `query_builder.py` | Bracket-quoting identifiers that came from metadata, `?` bind params for every value, and returning a frozen plan object that carries `sql` and `params` **together** so a call site cannot separate them. |
| `execution_sql.py` | The **generator/deployer split**: the pure function that renders an artifact and needs no connection, versus the separate function gated on `confirmed: bool` that applies it, returning a per-artifact result with a `blocked_reason` instead of raising. |
| `config_loader.py` | The openpyxl reader idiom, and — more importantly — that **parse and validate are strictly separate**: `load_workbook` does no validation, `validate()` accumulates issues into a report and returns it. `mapping_reader.py` follows the same split. |
| `checkpoint.py` | Read this only if the user asked for resume. Per-dataset checkpointing with a `pending/running/completed/failed` status. Do not generate it speculatively. |

### `skills/claude-skills/sql-server-schema/`

| Module | Take |
| --- | --- |
| `skillgen.py` | The **Markdown emitter idiom**, and the only one in this repository: `lines: List[str]`, one `.append()` per line, `"\n".join(lines)`. Deterministic templating over sorted collections — no Jinja, no model call, byte-identical output for identical input. Use it for the transformation mapper. Also copy its multi-file orchestration shape: validate first and return `{"ok": False, "errors": [...]}` rather than raising, plan the file layout before rendering, build a `files: Dict[str, str]` map, then write in sorted order honouring `dry_run`. |
| `guardrails.py` | `resolve_artifact_path()` — refuses to write customer-naming artifacts into `skills/`, `mcp/`, `docs/`, or any in-repo path `git check-ignore` does not confirm. The transformation mapper must route through it. Also the PII column-name heuristics. |
| `analyze.py` | The **digest-as-seam** design: the live server writes a JSON digest; the generator reads a digest and never opens a connection. `mapping_reader.py`'s `mapping_digest.json` is the same idea — reviewable before anything is generated, testable against a fixture, and two digests of successive spec revisions diff cleanly. |
| `cli.py` | The argparse convention: `cmd_*(args) -> int`, `build_parser()`, `sub.add_parser(...).set_defaults(func=cmd_x)`, `main(argv=None) -> int`, `raise SystemExit(main())`, and JSON output via `print(json.dumps(payload, indent=2, sort_keys=True, default=str))`. |

### `skills/claude-skills/rdl-generation/scripts/`

Read `rdl_builder.py` if the generated tree needs a builder API: stdlib-only,
dependency-free, `add_*` methods that append plain dicts and `return self`,
with nothing touching the output until `build()` fixes element order regardless
of call order. Its `validate_rdl.py` is the `@dataclass Finding(severity, code,
message)` + one `_check_*() -> List[Finding]` function per rule pattern, which
is the right shape for the generated `validate.py`.

## Copy or import — decide explicitly

Two defensible choices, and the skill must **state which it made and why** in
the generated `README.md`:

- **Copy-and-adapt (default).** The generated pipeline stands alone: it can be
  handed to another team, run in another repository, and reviewed without
  reading `extract-engine`. Cost: drift. `extract-engine` is itself a copy of
  `sql-server-schema`'s `connection.py`, and `skills/report-lineage/PORTING.md`
  exists precisely to record that provenance so the copies can be diffed later.
  Do the same — name the source file and the commit in the copied module's
  docstring.
- **Import.** Only when the user has confirmed the pipeline lives inside this
  repository permanently. Less duplication, but it couples every generated
  pipeline to `extract_engine`'s package layout and to the `meta.*` schema its
  modules assume.

## Consider not generating code at all

`extract-engine` already turns an Excel-authored feed into pipe-delimited split
files, with the mapping as data in `meta.*` tables rather than as code. If the
mapping document is a straightforward field list whose rules are already in
that rule catalog, the lower-maintenance route is to translate the mapping into
an extract-engine workbook (`Feed` / `Datasets` / `FieldMap` sheets) and run
`extract run` — a spec change is then an Excel edit and a rerun, never a code
edit and a deploy.

Say so and offer it before generating a parallel codebase. Then generate code
anyway if the user prefers it, or if any of these hold:

- Rules that the catalog cannot express and that are not worth adding to it.
- The pipeline must run somewhere `extract-engine`'s `meta.*` tables do not
  exist, or with no write access to create them.
- The output must be handed to a team that will own and modify it.
- Grain-changing logic (aggregates, pick-one-per-key) that belongs in a
  bespoke query.

## Conventions the generated code must match

Uniform across all three existing skills. Matching them is not cosmetic — it is
what lets someone who knows one of these skills read the generated tree.

- `from __future__ import annotations` as the first import in **every** module.
- Explicit `typing` imports (`Any, Dict, Iterator, List, Optional, Sequence,
  Tuple`), not PEP 585 builtins. Full annotations on public functions,
  including return types.
- `@dataclass(frozen=True)` for value and result objects; mutable `@dataclass`
  only when it holds live state, as `PartWriter` does.
- `"...".format(...)` in extract-engine/sql-server-schema-derived code;
  f-strings only if the surrounding file already uses them. Match the
  neighbour.
- Prose module docstrings that explain **why**, not just what — and that name
  the file this code was derived from.
- Keyword-only configuration flags after `*`.
- Domain error classes subclassing a stdlib error, living in the module that
  owns the concern (`TransformError(ValueError)`, `CollisionError(RuntimeError)`).
- Validation that must not raise returns a report object instead
  (`ValidationReport`, `List[Finding]`).
- Section banners (`# ---...---` + a short title) splitting a module into
  phases.
- Comments that record **empirically discovered traps**, in the house style —
  "text mode on Windows silently translates `\n` to `\r\n`" is worth more than
  three paragraphs of API description, and the existing modules are full of
  them.
- DB access: `cursor = conn.cursor()` / `try: ... finally: cursor.close()`,
  positional `?` params as varargs, rows converted through a local `_rows()`
  helper into dicts and then into dataclasses.
- Artifact writes: `path.write_text(content, encoding="utf-8", newline="\n")`
  after `parent.mkdir(parents=True, exist_ok=True)`.
- `sys.path` bootstrap for scripts runnable directly:
  `_HERE = Path(__file__).resolve().parent` then insert if absent.

## What not to carry over

- **`meta.*` table dependencies**, unless the user chose the import route. The
  generated pipeline's configuration is `config.yml` and `layout.py`, not a
  database catalog.
- **The write guardrail allow-list** as-is. `extract-engine` writes to the
  database (config upserts, run bookkeeping, view DDL) and needs
  `classify_write_statement`. A generated loader pipeline reads only — which is
  a stronger guarantee, structurally enforced by there being no write path at
  all. Do not add one to get a run log; write the run manifest to a file.
- **Polars**, unless the user wants the dependency. The templates in
  `generated-pipeline-layout.md` are stdlib + `pyodbc` only, because a loader
  pipeline handed to another team is easier to run that way. Polars is the
  right answer when the transformations are genuinely vectorisable and the row
  counts are large — say so, and offer it.
- **The two execution backends.** `extract-engine` maintains byte-identical
  Polars and T-SQL paths because proving that equivalence was its point. A
  generated pipeline needs one path.
