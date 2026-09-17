# `reportlineage` deep dive

Companion to `skills/report-lineage/README.md` (what it does, how to run it)
and `skills/report-lineage/PORTING.md` (where each module's approach came
from). This document is the module-by-module reference: exact field names,
exact thresholds, exact CLI arguments, verified against the source in
`skills/report-lineage/reportlineage/` as it stands today.

---

## 1. Overview

`reportlineage` builds a single lineage graph over an SSRS/SSIS reporting
estate: every `.rdl` report, `.dtsx` SSIS package, `.conmgr` connection
manager, and standalone `.sql` file it can find, joined into one
`EstateGraph` of nodes, edges, and diagnostics. On top of that graph it adds
two more capabilities that only make sense once an estate-wide view exists:
duplicate/overlap detection between reports (fingerprint every `.rdl` on six
independent dimensions, cluster the overlapping ones, recommend a keeper),
and natural-language search/reuse ranking against the fingerprinted estate
("does a report like this already exist?").

The three capabilities share one join key: a table's
`database::schema.table` signature (`signature.py`). Because every table
node's id is derived from that signature, a table an SSIS package writes and
a table an RDL dataset reads collapse onto the same node with no explicit
join step, and `source -> package -> table -> dataset -> report` forms
automatically.

### Provenance and drift risk

This package is not a copy of anything; it is an independent
reimplementation, `pip install`-able on its own, with zero import of
`app.*`, `backend.*`, or `shared.*`. `PORTING.md` records, module by module,
which file in the source backend repository established the pattern each
module here reproduces (for example `signature.py` reimplements
`backend/app/core/lineage/signature.py`, `duplicates.py` reimplements
`backend/app/core/complexity/duplicates.py`). The tradeoff that buys is
explicit: portability in exchange for drift risk. If the backend's version of
a module changes, this package's copy does not follow automatically;
`PORTING.md` is the diff manifest for reconciling the two later. Two library
features the backend has and this package deliberately does not: SSAS
(`.bim`) / shared-dataset (`.rsd`) lineage, and the SOAP `ReportService2010`
report-server transport for SSRS 2008 R2 through 2016 (this package's
scanner is REST v2.0 only).

---

## 2. Core data model (`models.py`, `types.py`)

`EstateGraph` (a plain pydantic `BaseModel`, no host dependency) is the one
artifact everything else in this package builds toward or consumes:

```python
class EstateGraph(BaseModel):
    meta: EstateMeta
    layers: List[LayerDef]
    nodes: List[LineageNode]
    edges: List[LineageEdge]
    diagnostics: List[LineageDiagnostic]
    inventory_by_kind: List[InventoryByKind]
    confidence: Dict[str, int]
    provenance: Dict[str, int]
    totals: Dict[str, int]
    kpis: Dict[str, float]
```

`to_graph_json()` calls `model_dump(by_alias=True, mode="json")` so the
serialized form uses `"schema"`, not the Python-side `schema_name` (`schema`
is a reserved name on pydantic's `BaseModel`, hence the alias).

### Layers

Six fixed layers, source to consumption, left to right, each an `int` id plus
a `key`/`label` pair (`LayerDef`):

| id | key | label |
| --- | --- | --- |
| 0 | `source` | Source |
| 1 | `etl` | ETL |
| 2 | `staging` | Staging |
| 3 | `dw` | DW (Dim / Fact) |
| 4 | `semantic` | Semantic Model |
| 5 | `report` | Reporting |

Module-level constants `LAYER_SOURCE .. LAYER_REPORT` (0-5) are what
`builder.py` actually assigns; `DEFAULT_LAYERS` is the `List[LayerDef]` an
`EstateGraph` carries by default.

### Node kinds

`NodeKind` is a class of string constants, not an `Enum` -- deliberately an
*open* set, so a new source type never requires a schema change:
`TABLE = "table"`, `VIEW = "view"`, `STORED_PROCEDURE = "stored_procedure"`,
`SSIS_PACKAGE = "ssis_package"`, `SSIS_TASK = "ssis_task"`,
`SSIS_COMPONENT = "ssis_component"`, `SSRS_REPORT = "ssrs_report"`,
`SSRS_DATASET = "ssrs_dataset"`. `builder.py` only ever emits
`table`/`view`/`stored_procedure`/`ssis_package`/`ssrs_report`/`ssrs_dataset`
today; `ssis_task` and `ssis_component` are declared but unused by any
current extractor.

### Closed enumerations

```python
class Confidence(str, Enum):
    HIGH = "high"; MEDIUM = "medium"; LOW = "low"; INFERRED = "inferred"

class Provenance(str, Enum):
    PARSER = "parser"        # statically extracted from a source artifact
    REFERENCED = "referenced"  # known only by name (referenced, not defined)
    LLM = "llm"               # inferred by a model
    SIDECAR = "sidecar"       # supplied out-of-band

class EdgeKind(str, Enum):
    DERIVES_FROM = "derives_from"  # data flows upstream to downstream
    CONTAINS = "contains"          # structural containment (report contains dataset)
    REFERENCES = "references"      # non-data reference

class Severity(str, Enum):
    BLOCKER = "blocker"; WARN = "warn"; INFO = "info"; ERROR = "error"
```

`builder.py` only ever produces `Provenance.PARSER` (a table whose database
is known) or `Provenance.REFERENCED` (a table referenced only by
unqualified/partial name); `LLM` and `SIDECAR` exist on the model for callers
that layer inference or out-of-band data on top of a scan, but nothing in
this package sets them. Likewise `Severity.BLOCKER`/`ERROR` are declared but
`builder.py` only ever emits `Severity.WARN` (unresolved dataset SQL,
unresolved conmgr, SSIS diagnostics) or `Severity.ERROR` (a file that raised
during parsing) -- no code path emits `BLOCKER` today.

### Graph elements

```python
class LineageNode(BaseModel):
    id: str
    kind: str
    name: str
    qualified_name: str
    layer: int
    schema_name: Optional[str] = Field(default=None, alias="schema", serialization_alias="schema")
    domain: Optional[str] = None
    confidence: Confidence = Confidence.HIGH
    provenance: Provenance = Provenance.PARSER
    orphan: bool = False
    source_ref: SourceRef = Field(default_factory=SourceRef)
    attributes: Dict[str, str] = Field(default_factory=dict)

class LineageEdge(BaseModel):
    upstream_id: str
    downstream_id: str
    kind: EdgeKind = EdgeKind.DERIVES_FROM
    provenance: Provenance = Provenance.PARSER
    confidence: Confidence = Confidence.HIGH
    evidence: Optional[str] = None

class LineageDiagnostic(BaseModel):
    kind: str
    severity: Severity = Severity.WARN
    message: str
    source_ref: SourceRef = Field(default_factory=SourceRef)
    node_id: Optional[str] = None
    impact: Optional[str] = None
    remediation: Optional[str] = None

class SourceRef(BaseModel):
    path: Optional[str] = None
    locator: Optional[str] = None

class InventoryByKind(BaseModel):
    kind: str; label: str; count: int; layer: str

class EstateMeta(BaseModel):
    name: str = "BI Estate"
    source_platform: Optional[str] = None
    target_platform: str = "Microsoft Fabric"
    run_id: Optional[str] = None
    scanned_at: Optional[str] = None
    illustrative: bool = False
```

`orphan` is set by `builder._mark_orphans`: a `table`/`view` node with no
outgoing edge (nothing downstream reads it) is marked `orphan=True` -- it is
the estate's "unused table" signal.

### `types.py`: local `DataType`

A small closed vocabulary (`STRING`, `INTEGER`, `REAL`, `BOOLEAN`, `DATETIME`,
`DATE`, `UNKNOWN`) that mirrors the shape of a host application's IR
`DataType` without importing one. Used only by `xml_utils.map_data_type` to
classify an RDL field/parameter's `.NET` or RDL type name into one of these
buckets; lineage does not care about a field's precise SQL type, only its
rough shape.

---

## 3. The signature mechanism (`signature.py`)

The entire cross-source join mechanism is one function, re-exporting the
canonical logic that actually lives on `TableRef.signature()` in
`sql_refs.py`:

```python
def table_signature(database: Optional[str], schema: Optional[str], table: str) -> str:
    """``database::schema.table``, case-folded, schema defaulting to ``dbo``."""
    return TableRef(table=table, schema=schema, database=database).signature()
```

And the canonical implementation (`sql_refs.TableRef.signature`):

```python
def signature(self) -> str:
    db = (self.database or "").strip().lower()
    sch = (self.schema or "dbo").strip().lower()
    return f"{db}::{sch}.{self.table.strip().lower()}"
```

Every part is lower-cased and stripped, so `"OrdersDW", "DBO", " Sales "` and
`"ordersdw", "dbo", "sales"` produce the identical string
(`test_signature.py::test_signature_matches_across_case_and_whitespace`
asserts exactly this). A missing database becomes an empty string, not
`None`, so `table_signature(None, "dbo", "Region")` is `"::dbo.region"`
(`test_signature_database_defaults_to_empty_string`) -- a table with no known
database still gets a stable, comparable signature, just one that will only
match another equally-unqualified reference to the same table name.

**Why schema defaults to `dbo`:** `dbo` is SQL Server's default schema. An
RDL dataset's `SELECT * FROM Sales` and an SSIS destination's OLE DB write to
`[dbo].[Sales]` must resolve to the same node even though only one side
spelled out the schema; defaulting the missing side to `dbo` is what makes
that happen. Get this default wrong (or omit it) and every unqualified
reference in the estate would form its own island node, and the "SSIS
package writes X, report reads X" join the whole package exists to build
would silently stop firing for the majority of real-world estates, where
most application code omits `dbo.` because it is already the connection's
default schema.

**Why this is "the whole linking mechanism":** `builder.py` never performs an
explicit join, lookup, or graph-merge pass between sources. Every extractor
independently computes `_table_node_id(ref) = f"sql:{ref.signature()}"` for
every table it touches; `_dedupe_nodes` later collapses any two `LineageNode`
objects that happen to share an `id`. Two nodes from unrelated source files
become one node purely because their signatures matched. There is no other
code path that connects an SSIS package to the report reading its output --
the shared table-node id *is* the join.

---

## 4. `builder.py`: extraction and orchestration

One extractor function per source type, each returning the identical tuple
shape `(nodes, edges, diagnostics, root_id, table_ids)`, so adding a new
source type is additive (this is the exact contract `PORTING.md` documents
for anyone adding `.bim`/`.rsd` support later):

- **`nodes_from_rdl(path, proc_body_lookup=None)`** -- parses one RDL via
  `parse_rdl_lineage`, emits one `ssrs_report` node and one `ssrs_dataset`
  node per dataset (`CONTAINS` edge from report to dataset), resolves each
  dataset's `CommandText` to table references via
  `extract_table_refs(dset.command_text, database=db)`, and emits a
  `DERIVES_FROM` edge from each resolved table to the dataset. Returns
  `(nodes, edges, diags, report_id, reads)` where `reads` is the set of
  table-node ids the report consumes (used later by `link_estate`). If a
  dataset's `command_type == "StoredProcedure"` and a `proc_body_lookup`
  callback is supplied, the callback is invoked with
  `(server, db, command_text)`; if it returns SQL text, that body is also run
  through `extract_table_refs` and its reads/writes/execs are added as extra
  source refs on the dataset -- this is how lineage traces *through* a stored
  procedure instead of stopping at the `EXEC` frontier. When a dataset
  resolves to no table at all, or its SQL is flagged dynamic, the dataset
  node's confidence is downgraded to `Confidence.LOW` and an
  `ssrs_sql_unresolved` diagnostic (`Severity.WARN`) is appended.
- **`nodes_from_dtsx(pkg, proc_body_lookup=None)`** -- takes an already-parsed
  `SsisPackage`, emits one `ssis_package` node, a `DERIVES_FROM` edge from
  each read table to the package, and one from the package to each written
  table. The same `proc_body_lookup` mechanism applies to any `pkg.reads`
  entry of `kind == "stored_procedure"`. Every entry in `pkg.diagnostics`
  (strings collected during DTSX parsing) becomes an `ssis_diagnostic`
  `LineageDiagnostic`. Returns `(nodes, edges, diags, pkg_id, writes)`.
- **`nodes_from_sql(sql_obj, key)`** -- takes the dict `classify_sql_object`
  returns, emits one node for the file's primary object (`table`/`view`/
  `stored_procedure`, defaulting to `NodeKind.VIEW` for an unrecognized
  `sql_obj["kind"]`), plus edges for every entry in `sql_obj["reads"]` and
  `sql_obj["writes"]`. If the object's name cannot be resolved to a qualified
  name at all, the node id falls back to `f"sql:unresolved::{key}"` (`key` is
  the file path) so it still gets a stable, unique id.

### Node placement (`_layer_for`)

A table's layer is inferred from its database and schema name, not
configured: a database name ending in `dw`/`dwh` places it in
`LAYER_DW` unless its schema is one of `{integration, staging, stg, stage}`
(`_STAGING_SCHEMAS`), in which case it is `LAYER_STAGING`; a table with a
known, non-warehouse-looking database is `LAYER_SOURCE`; a table with *no*
known database defaults to `LAYER_DW` ("reports usually read the
warehouse" -- the code comment's own rationale); a stored procedure reference
lands in `LAYER_STAGING` if its database looks like a warehouse, else
`LAYER_SOURCE` if a database is known at all, else `LAYER_STAGING`.

### Linking, dedup, orphans

- **`link_estate(pkg_writes, report_reads)`** adds the explicit
  "package -> consumer" `EdgeKind.REFERENCES` edge: for every package/SQL
  object that writes some set of tables and every report/dataset that reads
  some set of tables, an edge is added wherever the sets intersect, with
  evidence text naming the shared table(s). This is on top of, not instead
  of, the implicit signature-based join -- `link_estate` is what directly
  answers "which reports depend on which ETL packages" without the caller
  having to walk the table nodes in between.
- **`_dedupe_nodes`** merges any two nodes sharing an id via `_merge_node`:
  keeps the higher-ranked confidence (`HIGH > MEDIUM > LOW > INFERRED`) and
  provenance (`PARSER > REFERENCED > SIDECAR > LLM`), unions `attributes`
  (existing node's keys win on conflict), and keeps whichever `source_ref`
  has a non-empty `path`.
- **`_dedupe_edges`** drops exact duplicate `(upstream_id, downstream_id,
  kind)` triples.
- **`_mark_orphans`** flags every `table`/`view` node with no outgoing edge
  (nothing reads it downstream) as `orphan=True`.

### `build_estate(...)`: orchestration

```python
def build_estate(
    rdl_paths=(), dtsx_paths=(), conmgr_paths=(), sql_paths=(),
    meta: Optional[EstateMeta] = None,
    proc_body_lookup: Optional[ProcBodyLookup] = None,
) -> EstateGraph
```

Order of operations: parse every `.conmgr` first (so their `SsisConnection`
objects are available to resolve `.dtsx` connection references), then every
`.dtsx`, then every `.sql`, then every `.rdl`; call `link_estate`; dedupe
nodes and edges; mark orphans; compute rollups (`inventory_by_kind`,
`confidence`, `provenance` -- via `reportlineage.inventory`, imported locally
inside the function specifically to avoid a module-load cycle, since
`inventory.py` imports `models`, not `builder`); compute `totals` (`objects`,
`edges`, `orphans`, `diagnostics`, `datasets` = count of rdl+dtsx+sql paths)
and `kpis` (same plus `orphanPct` rounded to one decimal, `blockers` -- always
`0.0` today since nothing emits `Severity.BLOCKER`); stamp `meta.scanned_at`
with `datetime.now().isoformat()` if not already set. Every per-file parse is
wrapped in its own `try/except`: a bad file appends a `*_parse_error`
diagnostic (`Severity.ERROR`) and the loop continues -- one unreadable file
never aborts the scan (`test_builder.py::test_build_estate_tolerates_a_bad_path_in_the_middle`
asserts exactly this).

**Runner-style projection attachment (`fabric_readiness`, `wave_plan`,
`duplicates`, `report_fingerprints`) does not exist anywhere in this
package.** This was verified by reading `builder.py` in full and grepping
the whole `reportlineage/` tree for those four names -- no match. Those
projections are a feature of the *source* backend's `runner.py` (see
`skills/ASSESSMENT.md` section 2.4), which this package does not reimplement.
`EstateGraph` in this package carries no `duplicates` or `fabric_readiness`
field at all. Instead, `cli.py`'s `duplicates`/`search` subcommands run an
entirely separate pipeline: they re-scan the folder for `.rdl` files,
fingerprint each one directly (`fingerprint_rdl`), and run
`analyze_duplicates`/`SearchIndex` on the fingerprints -- independently of
whatever `EstateGraph` a `scan` subcommand produced. `export.to_html_summary`
does accept an optional `duplicates` argument and will render a "Duplicate
clusters" section if one is passed, but nothing in `cli.py` actually wires a
`DuplicateSummary` into the `scan` command's HTML export -- `_cmd_scan`'s
`to_html_summary(graph)` call passes no `duplicates` argument, so the HTML
output today never includes that section in practice.

One more gap worth noting for anyone extending the CLI: `scanners/filesystem.py`
recognizes `.rds`/`.rsd` files into `ScanResult.rsd_paths`, but `build_estate`
has no `rsd_paths` parameter and `cli.py` never passes them anywhere -- RSD
(shared dataset) files are discovered by the scanner but never fed into a
scan today, consistent with `PORTING.md`'s note that shared-dataset lineage
was not ported.

---

## 5. Parsers (`parsers/*.py`)

All four parsers are namespace-agnostic (they navigate XML by local tag/attribute
name via `xml_utils.py`/`ns_attr`, never by namespace URI) and fail soft: a
malformed file or sub-element yields a best-effort, partially-empty result
object rather than raising.

### `parsers/rdl.py` -- SSRS `.rdl`

`parse_rdl_lineage(path) -> RdlReport`. Reads the file through
`xml_utils.sanitize_rdl_bytes` (strips trailing `\x00` padding that SSRS
Report Server appends to served definitions -- without this, `ElementTree`
fails to parse most definitions pulled live from a server). Handles both the
RDL 2008 shape (`Report/Body` directly) and the RDL 2010/2016 shape
(`Report/ReportSections/ReportSection/Body`) via `_report_section`.

```python
@dataclass
class RdlDataset:
    name: str
    datasource_name: Optional[str] = None
    command_type: str = "Text"          # or "StoredProcedure"
    command_text: Optional[str] = None
    fields: List[str] = field(default_factory=list)

@dataclass
class RdlReport:
    name: str
    path: str
    connections: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    datasets: List[RdlDataset] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    visual_count: int = 0
    visual_kinds: List[str] = field(default_factory=list)
```

`connections` maps each `DataSource` element's `Name` to `(server,
database)` resolved from its `ConnectionProperties/ConnectString` via
`connect_string.parse_connect_string`. Visuals counted: exactly
`{Tablix, Chart, Gauge, Map, Subreport}` (`_VISUAL_KINDS`) -- anything else
(textboxes, images, rectangles) is not counted. This is deliberately narrower
than a full IR-producing parser: no M-query synthesis, no resolution of
visual internals beyond a kind count.

### `parsers/dtsx.py` -- SSIS `.dtsx`

`parse_dtsx_lineage(path, connections=None) -> SsisPackage`. Walks every
`Executable` element; for `ExecutableType="Microsoft.Pipeline"` it descends
into data-flow `component` elements and recognizes exactly two component
class IDs (case-insensitively): `microsoft.oledbsource` and
`microsoft.oledbdestination`. A source's `SqlCommand` property (if present)
is run through `extract_table_refs`; otherwise its `OpenRowset` property is
treated as a direct table reference; a variable-driven rowset
(`OpenRowsetVariable` set, no static rowset/SQL) produces a diagnostic
instead of a guess. A destination's `OpenRowset` becomes a write; a
variable-driven destination likewise produces a diagnostic rather than a
guess. For `ExecutableType="Microsoft.ExecuteSQLTask"`, the task's
`SqlStatementSource` is captured into `pkg.sql_tasks` and run through
`extract_table_refs`, with dynamic SQL flagged via a diagnostic.

Connection resolution: connection managers are looked up by name (matching
the `[NAME]` fragment in a component's `connectionManagerRefId`) or by GUID
(`connectionManagerID`, normalized to bare upper-case hex by `norm_guid`,
matched against a connection's `dtsid`). Both project-level `.conmgr`
connections (passed in via the `connections` argument, from files discovered
alongside the package) and package-embedded `ConnectionManagers` are merged;
embedded ones are parsed by `_parse_embedded_connections`.

```python
@dataclass(frozen=True)
class SsisConnection:
    name: str; server: Optional[str] = None; database: Optional[str] = None
    raw: Optional[str] = None; dtsid: Optional[str] = None

@dataclass
class SsisPackage:
    name: str; path: str
    connections: List[SsisConnection] = field(default_factory=list)
    reads: List[TableRef] = field(default_factory=list)
    writes: List[TableRef] = field(default_factory=list)
    sql_tasks: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
```

A component whose connection manager cannot be resolved at all still
produces a diagnostic string ("connection manager ... could not be resolved;
database is unknown for this component") rather than silently dropping the
table reference -- the table is still recorded, just with `database=None`
(which downstream affects its signature, layer placement, and confidence).

### `parsers/conmgr.py` -- SSIS project `.conmgr`

`parse_conmgr(path) -> Optional[SsisConnection]`. A `.conmgr` file is a
standalone XML document for one connection manager (project-level, as
opposed to the package-embedded kind `dtsx.py` also parses). Reads the root's
`ObjectName`/`DTSID` attributes and the connection string from whichever of
three shapes is present (`ConnectionString` directly on the root, or nested
under a `ConnectionManager`/`ConnectionManagerSettings` descendant), then
resolves `(server, database)` via the same `connect_string.parse_connect_string`
every other parser uses. Returns `None` (never raises) on a parse error or
unreadable file. Reuses `dtsx.norm_guid`/`dtsx.ns_attr` rather than
duplicating them.

### `parsers/sql_file.py` -- standalone `.sql`

`classify_sql_object(sql_text, filename="") -> dict` with keys `kind`
(`"table" | "view" | "stored_procedure" | "unknown"`), `name` (qualified name
if resolvable, else the filename stem), `reads: List[TableRef]`, `writes:
List[TableRef]`. Deliberately shallow: it looks only for a single leading
`CREATE [OR ALTER] TABLE|VIEW|PROCEDURE|PROC` statement to name the file's
primary object and pick its kind (checked in that literal order -- `TABLE`
first, then `VIEW`, then `PROCEDURE`/`PROC`), then defers to
`sql_refs.extract_table_refs` for the read/write surface of the entire file
text. If the object being defined would otherwise appear in its own
reads/writes (a regex false-positive off the `CREATE` statement itself), it
is explicitly filtered out by signature.

---

## 6. Scanners (`scanners/*.py`)

### `scanners/filesystem.py`

`scan_filesystem(root, *, follow_symlinks=False) -> ScanResult`. Purely
extension-based classification via `os.walk` -- no XML sniffing at all,
because every downstream parser already tolerates a misclassified or
malformed file. Extension map:

| Extension | `ScanResult` field |
| --- | --- |
| `.rdl` | `rdl_paths` |
| `.dtsx` | `dtsx_paths` |
| `.conmgr` | `conmgr_paths` |
| `.sql` | `sql_paths` |
| `.rds`, `.rsd` | `rsd_paths` (collected but not consumed by `build_estate` -- see section 4) |

A nonexistent or unreadable root, or a walk error, appends a human-readable
note to `ScanResult.skipped` and returns an otherwise-empty result rather
than raising.

### `scanners/git_repo.py`

`clone_repo(url, dest=None, *, branch=None, depth=1, token_env_var=None) ->
str` shells out to the system `git` binary (`subprocess.run`, always
`--depth`-shallow, since only current file contents are needed, never
history). `dest` defaults to a fresh `tempfile.mkdtemp(prefix="reportlineage-")`.
When `token_env_var` is given, the token is read from the environment (never
accepted as a literal argument) and spliced into the URL's netloc
(`user@host` form) for HTTPS PAT auth -- `_splice_token` raises if the URL
scheme is not `http`/`https`. The token is never logged or written to disk;
any failure message from the `git` subprocess is passed through
`_sanitize`, which replaces the literal token value with `***` and also
regex-redacts any `://user:pass@` userinfo segment, before being raised as a
`RuntimeError`. `scan_git_repo(url, **clone_kwargs) -> ScanResult` clones
then calls `scan_filesystem` on the checkout.

### `scanners/report_server.py`

A minimal SSRS / Power BI Report Server REST v2.0 client,
`ReportServerClient`. Imports `requests` lazily -- `_require_requests()`
raises a clear `RuntimeError` ("pip install requests") only when a method
that actually needs it is called, so importing this module (or the whole
package) never requires the `server` extra.

- **Construction**: `ReportServerClient(base_url, *, auth=None,
  verify_ssl=True, timeout=30, session=None)`. `base_url` is the bare server
  root (`/api/v2.0` is appended unless already present -- detected
  case-insensitively so passing the full API base also works). `auth=(user,
  password)` sets HTTP Basic on an internally created `requests.Session`.
  Windows Integrated / NTLM / Kerberos auth is *not* implemented directly:
  the docstring instructs the caller to build their own `requests.Session`
  with an auth plugin (`requests_negotiate_sspi.HttpNegotiateAuth` on
  Windows, `requests_kerberos.HTTPKerberosAuth` on Linux/Mac) and pass it via
  `session=`; when `session` is supplied, `auth`/`verify_ssl` are not applied
  to it at all.
- **`probe() -> {"product_version", "edition"}`**: `GET /System`, raises on a
  non-2xx status or a non-JSON/non-dict body.
- **`list_catalog(item_type=None) -> List[CatalogItem]`**: `GET
  /CatalogItems` fetched as one flat collection (not filtered server-side via
  OData `$filter`, because `$filter` support drifts across SSRS 2017/2019/2022
  and PBIRS releases, while the flat collection is stable everywhere -- the
  module docstring states this explicitly), then filtered client-side by
  `item.type == item_type` if given. Guards a pathological catalog: more than
  `_MAX_ITEMS = 20000` rows triggers a truncation warning to stderr rather
  than an unbounded in-memory list.
- **`download_definition(item) -> bytes`**: tries the streaming
  `/CatalogItems({id})/Content/$value` endpoint first; on anything other than
  a 200 with a non-empty body, falls back to `GET /CatalogItems({id})` and
  base64-decodes its `Content` property.
- **`scan(item_type="Report", download_dir=None) -> (items, local_paths)`**:
  lists the catalog, and if `download_dir` is given, downloads every item
  into it, mirroring each item's server-side folder path under
  `download_dir` and forcing a `.rdl` extension if the path doesn't already
  have one. A single item's download failure is caught, warned to stderr,
  and skipped -- it does not abort the rest of the scan.

**No SSRF/URL guard exists in this module or anywhere else in the package**
(verified by grep across `skills/report-lineage/` for `url_guard`, `ssrf`,
and related terms -- no matches). This differs from the source backend, whose
`backend/app/core/report_server/url_guard.py` (per `skills/ASSESSMENT.md`
section 2.6) guards the configured base URL; that guard was not ported. Anyone
exposing `scan-server`/`ReportServerClient` to a caller-supplied URL in a
network-reachable service should add an equivalent check before trusting
this module's default behavior in that context.

---

## 7. Duplicate detection: `fingerprint.py`, `duplicate_weights.py`, `duplicates.py`

### `fingerprint.py`: the dimensions

```python
@dataclass(frozen=True)
class ReportFingerprint:
    key: str
    name: str
    folder: str = ""
    tables: FrozenSet[str] = frozenset()        # database::schema.table signatures
    columns: FrozenSet[str] = frozenset()
    parameters: FrozenSet[str] = frozenset()
    visuals: FrozenSet[str] = frozenset()
    sql: FrozenSet[str] = frozenset()
    terms: FrozenSet[str] = frozenset()         # content words: name + dataset names + field names
    datasource_names: FrozenSet[str] = frozenset()
    visual_count: int = 0
    kind: str = "report"
```

`build_fingerprint_from_rdl_report(report, key=None, folder="") ->
ReportFingerprint` builds this directly from an already-parsed `RdlReport`
(not from a full IR workbook -- one consequence, per `PORTING.md`, is that
this package's fingerprint has **no `measures` dimension**, since the narrow
RDL parser does not extract calculated-field formulas the way a full IR
parser would). `fingerprint_rdl(path, key=None, folder="")` parses the file
first. Both functions never raise: any internal failure is swallowed
(`except Exception: pass/continue`) and the caller gets the best-effort
fingerprint built so far, or (for `fingerprint_rdl` on total parse failure) an
otherwise-empty fingerprint keyed and named from the file path/stem.

Normalization helpers, shared between fingerprinting and search:
`normalize_name` lowercases, replaces `_` with a space, strips non-word
characters, and collapses whitespace, so `"Sales Amount"`, `"sales_amount"`,
and `"SalesAmount "` all normalize identically. `tokenize` splits a batch of
strings into lower-cased content words longer than 2 characters, excluding a
fixed `STOPWORDS` set (`report, reports, the, and, for, with, by, of, a, an,
id, key, code, name, date, time, value, total, count, new, copy, final, v1,
v2, v3, old, test, temp, draft`). Every set-valued dimension is capped at
`_MAX_PER_DIMENSION = 500` entries (`_cap`, keeping the lexicographically
first 500 when over) so one pathological report cannot dominate memory or
comparison cost. `ReportFingerprint.is_empty` is true when `tables`,
`columns`, `sql`, and `parameters` are *all* empty -- `analyze_duplicates`
excludes empty fingerprints from comparison entirely.

### `duplicate_weights.py` + `data/duplicate_weights.json`: judgments as data

`load_duplicate_weights(path=None) -> DuplicateWeights` loads the bundled
JSON (located relative to `duplicate_weights.py`'s own `__file__`, so it
works identically from a source checkout or an installed distribution) into
an immutable dataclass. Exact values from `data/duplicate_weights.json`
(`reference_version: "reportlineage-0.1"`):

```json
"dimension_weights": { "sql": 0.28, "tables": 0.24, "columns": 0.24, "parameters": 0.10, "visuals": 0.10, "name": 0.04 }
"verdict_thresholds": { "identical": 0.95, "near_duplicate": 0.82, "overlapping": 0.62, "related": 0.45 }
"cluster_min_similarity": 0.62
"action_min_similarity": 0.82
"min_shared_columns": 3
"reuse_weights": { "terms": 0.5, "tables": 0.3, "columns": 0.2 }
"reuse_thresholds": { "reuse_as_is": 0.72, "extend": 0.45, "reference": 0.22 }
"limits": { "max_pairs_reported": 500, "max_shared_sample": 12, "max_reuse_matches": 10, "max_cluster_unique_columns": 15 }
```

Note the dimension weights sum to `1.00` across six dimensions
(`sql, tables, columns, parameters, visuals, name`) -- there is no `measures`
dimension in this file, matching `fingerprint.py`'s narrower fingerprint. Per
the file's own `_thresholds_comment`: `identical` means the same query and
field list, differing only cosmetically; `near_duplicate` means one is a
variant or copy of the other; `overlapping` means a substantial shared
surface worth consolidating onto one model; `related` means only a shared
subject area, reported for context and never auto-actioned. `limits` is read
defensively (`DuplicateWeights.limit(name, default)` falls back to `default`
on a missing or non-numeric key).

`DuplicateWeights.verdict_for(similarity)` walks `("identical",
"near_duplicate", "overlapping", "related")` strongest-first and returns the
first threshold the score clears, or `None` if it clears none of them
(a `None` result means `compare()` drops the pair entirely -- it is not
reported as a match at all). `reuse_verdict_for(score)` does the same over
`("reuse_as_is", "extend", "reference")`.

### `duplicates.py`: comparison, blocking, clustering

**Pairwise comparison (`compare`)** scores five dimensions
(`sql, tables, columns, parameters, visuals` -- `_DIMENSIONS`) plus `name`
computed separately via `_name_similarity` (Jaccard over each name's
normalized, whitespace-split token set). Each dimension's similarity is a
plain Jaccard index (`_jaccard`): `len(left & right) / len(left | right)`,
returning `None` (not `0.0`) when *neither* side populated the dimension at
all -- `None` dimensions are excluded from both the weighted sum and the
weight-total denominator entirely, which is the renormalize-over-populated-
dimensions design: a report whose SQL could not be resolved is compared only
on the dimensions both sides actually have, so it does not score artificially
low against an identical twin purely because one dimension is empty on both
sides. If the resulting `weight_total` is `<= 0` (nothing comparable at
all), `compare` returns `None`; if the weighted score doesn't clear even the
lowest verdict threshold (`related`, `0.45`), it also returns `None`. A
`ReportPair`'s `rationale` is generated from up to the three
highest-(similarity x weight) dimensions with `shared_count > 0`
(`_pair_rationale`), formatted as e.g.
`"Overlapping, based on 4 shared tables (75% overlap), ..."`.

**Blocking (`_candidate_pairs`)**: rather than scoring every possible pair
(`O(n^2)`), pairs are only nominated as candidates if two fingerprints share
at least one table signature (any shared table nominates the pair
immediately), or if they share at least `min_shared_columns` (3) normalized
column names -- columns alone are treated as far less discriminating than a
shared table, so a single shared column does not nominate a pair; it must
accumulate to the threshold. This keeps a whole-estate sweep near-linear.

**Clustering (`_build_clusters`)**: a union-find structure joins any two
reports whose `ReportPair.similarity >= cluster_min_similarity` (0.62). Each
resulting connected component of size >= 2 becomes a `Cluster`, with:
`mean_similarity` = the mean of that component's *pairwise* similarities
(not a component-wide re-comparison); `verdict` = the strongest verdict among
its member pairs; `keeper` = `_pick_keeper`, the fingerprint with the
broadest `len(tables) + len(columns)` surface (ties broken by
`visual_count`, then `len(parameters)`, then `key` for determinism); `action`
= `ACTION_MERGE` ("merge_into_one") if `mean_similarity >=
action_min_similarity` (0.82) *and* at least one non-keeper member has extra
tables/columns/parameters the keeper lacks (`_has_extra`); `ACTION_RETIRE`
("retire_duplicates") if `mean_similarity >= 0.82` but no member has
anything extra (safe to delete outright); otherwise `ACTION_REVIEW`
("review") below the action threshold. Each member's own
`ClusterMember.recommendation` is `"keep"` for the keeper, `"review"` for
everyone else if the cluster action is `review`, else `"merge"` (has extra
surface) or `"retire"` (fully redundant).

`analyze_duplicates(fingerprints, weights=None) -> DuplicateSummary`
(`pairs`, `clusters`, `unmatched`) is the entry point: drops empty
fingerprints, returns immediately with everything unmatched if fewer than 2
usable fingerprints remain, otherwise runs blocking -> `compare` on every
candidate pair -> sorts pairs by similarity descending -> clusters ->
`unmatched` = every usable fingerprint's key that ended up in no cluster.
`pairs` is truncated to `max_pairs_reported` (500).

**Reuse search (`find_reuse`)** answers "does a report like this already
exist?" from free text: tokenizes the request the same way fingerprint terms
are tokenized, then scores each fingerprint on up to three weighted
components (`terms 0.5, tables 0.3, columns 0.2` from `reuse_weights`) --
`terms`: fraction of the query's own tokens found in the fingerprint's
`terms`; `tables`: fraction of the fingerprint's own table-name-derived terms
(the last dotted segment of each table signature, normalized) found in the
query; `columns`: fraction of query tokens found among the fingerprint's
`columns`. Each present component is weighted and averaged (again,
renormalized over only the components that had something to compare); the
resulting score is classified via `reuse_verdict_for` into `reuse_as_is`
(>=0.72), `extend` (>=0.45), `reference` (>=0.22), or `"none"` below that.
Results are sorted by score descending and truncated to `max_reuse_matches`
(10).

---

## 8. `search.py`: inverted-index search

`SearchIndex(fingerprints: List[ReportFingerprint])` builds two structures at
construction: `_by_key` (a dict from fingerprint key to fingerprint, for
result hydration) and `_table_index` (`Dict[table_signature,
List[ReportFingerprint]]`, an actual inverted index over the `tables`
dimension). Two query methods:

- **`search(query, limit=10)`** does *not* implement its own scoring -- it
  delegates entirely to `duplicates.find_reuse(self.fingerprints, query)`
  (the same natural-language reuse scoring described in section 7: `terms 0.5,
  tables 0.3, columns 0.2`, tokenized free text against `terms`/`tables`/
  `columns`), then re-hydrates each `ReuseMatch.key` back to its
  `ReportFingerprint` via `_by_key` and truncates to `limit`. The `limit`
  parameter is a client-side truncation on top of whatever
  `max_reuse_matches` (10) `find_reuse` already applied -- passing `limit=50`
  will not return more than 10 results, since `find_reuse` itself caps at 10
  before `search` ever sees the list.
- **`by_table(signature)`** is a plain exact lookup into `_table_index`:
  every fingerprint that references the exact `database::schema.table`
  signature given, answering "what reports would be affected if I changed
  this table?"

---

## 9. `export.py`: output formats

Four pure functions, each returning the rendered text and, if `path` is
given, also writing it to disk (no other side effects):

- **`to_json(graph, path=None)`** -- `json.dumps(graph.to_graph_json(),
  indent=2)`.
- **`to_mermaid(graph, max_nodes=200)`** -- a `graph TD` Mermaid flowchart,
  one line per unique `(upstream_id, downstream_id)` edge pair among the
  first `max_nodes` nodes (truncation is by node list order, not by any
  importance ranking). Node names are sanitized (`_sanitize_label` strips
  `"[]{}()` and newlines) so they don't break Mermaid's `["label"]` syntax. If
  no edges survive the node truncation, every included node is still emitted
  standalone so a graph with objects but no relationships doesn't render as
  an empty diagram. A truncation is noted as a trailing Mermaid comment line.
- **`to_csv_nodes(graph, path=None)`** -- one row per node:
  `id, kind, name, qualified_name, layer, schema, domain, confidence,
  provenance, orphan, source_path, source_locator`.
- **`to_csv_edges(graph, path=None)`** -- one row per edge:
  `upstream_id, downstream_id, kind, provenance, confidence, evidence`.
- **`to_html_summary(graph, duplicates=None, path=None)`** -- a single
  self-contained (inline `<style>`, no external assets) HTML page: title and
  meta line (source/target platform, scanned-at), a KPI strip from
  `graph.kpis`, an "Inventory by kind" table from `graph.inventory_by_kind`,
  a "Diagnostics" table (severity, message, source path), and -- only if a
  `DuplicateSummary`-shaped object with non-empty `.clusters` is passed in --
  a "Duplicate clusters" table of keeper/members/verdict/action. As noted in
  section 4, `cli.py`'s `scan` command never actually passes a `duplicates`
  object, so that section is dormant in the CLI's own output today; a caller
  using the library directly can pass one.

---

## 10. `cli.py`: every subcommand

Entry points: `python -m reportlineage <subcommand> ...` (via `__main__.py`,
which just calls `cli.main()`) or, once installed, the `reportlineage`
console script (`project.scripts` in `pyproject.toml`:
`reportlineage = "reportlineage.cli:main"`).

`--version` (top-level, before any subcommand) prints `__version__`
(`"0.1.0"`) and exits `0`. No subcommand prints help and exits `0` (not an
error).

### `scan <path>`

```
python -m reportlineage scan ./my-estate --out ./out --mermaid --html --csv
```

Arguments: `path` (positional); `--out` (default `./reportlineage-out`);
`--mermaid`, `--html`, `--csv` (all boolean flags, no export beyond
`estate.json` unless given). Runs `scan_filesystem(path)`, prints any
`skipped` notes to stderr, and if any RDL/DTSX/conmgr/SQL file was found,
calls `build_estate` and writes `estate.json` always, plus `estate.mmd` /
`estate.html` / `nodes.csv`+`edges.csv` per the flags given. Prints
`nodes=N edges=N diagnostics=N` then `wrote <path to estate.json>`. Returns
`0` even when nothing was found (prints "No RDL/DTSX/conmgr/SQL files found
to scan." instead).

### `scan-git <url>`

```
python -m reportlineage scan-git https://github.com/org/repo.git --branch main --out ./out
python -m reportlineage scan-git https://github.com/org/private-repo.git --token-env GITHUB_PAT
```

Arguments: `url` (positional); `--out` (default `./reportlineage-out`);
`--branch` (default: repo's default branch); `--token-env` (env var name
holding an HTTPS PAT -- never pass a token as a literal CLI argument);
`--mermaid`, `--html`, `--csv`. Shallow-clones via `scanners.git_repo.
scan_git_repo`, then runs the same export pipeline as `scan`. A clone
`RuntimeError` (missing `git`, timeout, or a clone failure) is caught,
printed as `error: ...` to stderr, and returns `1`.

### `scan-server <base-url>`

```
python -m reportlineage scan-server https://myserver/reports \
    --user domain\svc-account --password-env REPORTLINEAGE_PASSWORD \
    --item-type Report --out ./out --mermaid --html --csv
```

Arguments: `base_url` (positional); `--out` (default: a fresh
`tempfile.mkdtemp(prefix="reportlineage-scan-")` if omitted); `--user`;
`--password` (discouraged -- see below); `--password-env` (preferred: reads
the password from the named environment variable at run time so it never
appears in shell history or `ps`/Task Manager); `--item-type` (default
`"Report"` -- the REST v2.0 `Type` filter, e.g. also `"DataSet"` or
`"DataSource"`); `--mermaid`, `--html`, `--csv`. If `--password-env` is given
but the variable is unset, prints an `error:` and returns `1` before
attempting any connection. Requires the `server` extra (`pip install
-e ".[server]"` or `".[all]"`) -- `ReportServerClient` raises a `RuntimeError`
with an actionable install message if `requests` is missing, which `cli.py`
catches and reports the same way as a clone failure. Downloads matching
items into `--out` (each forced to a `.rdl` extension), then runs
`scan_filesystem` + the standard export pipeline over that download
directory. If nothing could be downloaded, prints a message and returns `0`
without attempting a build.

### `duplicates <path>`

```
python -m reportlineage duplicates ./my-estate --out duplicates.json
```

Arguments: `path` (positional); `--out` (optional JSON output path). Scans
`path` for `.rdl` files only (ignores DTSX/conmgr/SQL entirely -- duplicate
detection is an RDL-only concept in this package), fingerprints each one via
`fingerprint_rdl`, runs `analyze_duplicates`, and prints one line per cluster:
`[<verdict>] keeper=<name> action=<action> members=<name, name, ...>`. If
`--out` is given, writes a JSON payload (`{"pairs": [...], "clusters": [...],
"unmatched": [...]}`, each list of `dataclasses.asdict`-serialized objects)
to that path, creating parent directories as needed. Returns `0` with a "No
.rdl files found" message if the folder has none, and `0` with "No duplicate
or overlapping reports found." if fingerprinting found no clusters.

### `search <path> <query>`

```
python -m reportlineage search ./my-estate "monthly sales by region"
```

Arguments: `path`, `query` (both positional; `query` should typically be
quoted as one shell argument). Scans `path` for `.rdl` files, fingerprints
them, builds a `SearchIndex`, and prints each hit as
`<score:.3f>  <name>  (<key>)`, best match first. Returns `0` with a "No .rdl
files found" / "No matches found." message in the empty cases.

### `--version`

```
python -m reportlineage --version
```

---

## 11. Installation and quickstart

### Install

From `skills/report-lineage/`:

```bash
pip install -e .
# with sqlglot (better T-SQL extraction) and requests (scan-server support):
pip install -e ".[all]"
# for running the test suite too:
pip install -e ".[dev]"
```

`pydantic>=2` is the only hard dependency. `sqlglot>=20` (extra `sqlparse`)
and `requests>=2.28` (extra `server`) are both optional and independently
guarded -- the package imports and runs fully without either, with the T-SQL
extraction falling back to the regex heuristics in `sql_refs.py` and
`scan-server` raising a clear, actionable error only if actually invoked
without `requests` installed.

### End-to-end example: scan -> inspect -> search

```bash
# 1. Scan a folder of RDL/DTSX/conmgr/SQL files into ./out
python -m reportlineage scan ./my-estate --out ./out --mermaid --html --csv
#    nodes=42 edges=57 diagnostics=3
#    wrote ./out/estate.json

# 2. Inspect what came out
#    ./out/estate.json      -- the full EstateGraph, machine-readable
#    ./out/estate.mmd       -- paste into https://mermaid.live to see the graph
#    ./out/estate.html      -- open in a browser for a KPI + inventory + diagnostics summary
#    ./out/nodes.csv, ./out/edges.csv  -- open in Excel for ad-hoc filtering

# 3. Find duplicate/overlapping reports in the same estate
python -m reportlineage duplicates ./my-estate --out ./out/duplicates.json
#    [near_duplicate] keeper=SalesByRegion action=retire_duplicates members=SalesByRegion, SalesByRegion_Copy

# 4. Ask whether a report like the one you're about to build already exists
python -m reportlineage search ./my-estate "monthly sales by region"
#    0.812  SalesByRegion  (./my-estate/reports/SalesByRegion.rdl)
#    0.340  RegionalRevenueSummary  (./my-estate/reports/RegionalRevenueSummary.rdl)
```

Or from Python directly, combining a scan with duplicate analysis in one
process:

```python
from reportlineage import build_estate, fingerprint_rdl, analyze_duplicates, SearchIndex
from reportlineage.scanners.filesystem import scan_filesystem
from reportlineage.export import to_json, to_html_summary

result = scan_filesystem("./my-estate")
graph = build_estate(
    rdl_paths=result.rdl_paths,
    dtsx_paths=result.dtsx_paths,
    conmgr_paths=result.conmgr_paths,
    sql_paths=result.sql_paths,
)
print(graph.totals, graph.diagnostics)

fingerprints = [fingerprint_rdl(p, key=p) for p in result.rdl_paths]
summary = analyze_duplicates(fingerprints)
for cluster in summary.clusters:
    print(cluster.verdict, cluster.action, [m.name for m in cluster.members])

# Wire the duplicate summary into the HTML export, unlike the CLI's own scan command:
to_html_summary(graph, duplicates=summary, path="./out/estate.html")

index = SearchIndex(fingerprints)
for fp, score in index.search("monthly sales by region"):
    print(score, fp.name)
```

### Tests

```bash
pip install -e ".[dev]"
python -m pytest tests -q
```

`tests/` covers every parser (`test_rdl_parser.py`, `test_dtsx_parser.py`,
`test_conmgr_parser.py`, `test_sql_file_parser.py`), the signature/connect-
string helpers (`test_signature.py`, `test_connect_string.py`), T-SQL
extraction (`test_sql_refs.py`), the graph builder's cross-source join
(`test_builder.py`), the pydantic models (`test_models.py`), inventory
rollups (`test_inventory.py`), fingerprinting and duplicate clustering
(`test_fingerprint.py`, `test_duplicates.py`), reuse search
(`test_search.py`), every export format (`test_export.py`), the filesystem
scanner (`test_scanners_filesystem.py`), a mocked report-server client
(`test_report_server_client.py`, no live network access), and the CLI
end-to-end (`test_cli.py`). Fixtures live under `tests/fixtures/`
(`sample_report.rdl`, `sample_report_near_dup.rdl`,
`sample_report_unrelated.rdl`, `sample_package.dtsx`,
`sample_connection.conmgr`, `sample_view.sql`) and are shared via
`tests/conftest.py` fixtures (`fixtures_dir`, `sample_rdl_path`,
`sample_dtsx_path`, `sample_conmgr_path`, `sample_sql_path`).
