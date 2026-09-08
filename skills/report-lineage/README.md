# reportlineage

A standalone Python package that scans an SSRS / SSIS reporting estate,
builds a lineage graph from source tables through ETL packages and reports,
finds duplicate or overlapping reports, and lets you search an estate in
natural language. It has zero dependency on any host application: it imports
nothing from this repository's `app.*`, `backend.*`, or `shared.*` packages,
so it can be copied into another repository and used immediately. See
`PORTING.md` for that move.

This package is independent from, and does not import, the rest of the
source repository it was extracted from. It reimplements a narrower version
of the same ideas (see `PORTING.md` for the provenance of each module) so
that it can travel without the backend.

## What it does

Given a folder, a git repository, or a live SSRS / Power BI Report Server
catalog, `reportlineage`:

1. Parses every `.rdl` report, `.dtsx` SSIS package, `.conmgr` connection
   manager, and standalone `.sql` file it finds.
2. Extracts every table a report's datasets query and every table an SSIS
   package reads from or writes to.
3. Joins everything on a `database::schema.table` signature, so a table
   written by an ETL package and read by a report becomes one node with an
   explicit edge between the package and the report.
4. Produces an `EstateGraph`: nodes, edges, per-node confidence and
   provenance, and diagnostics for anything it could not resolve (dynamic
   SQL, an unresolved connection manager, and so on). A single bad file never
   aborts a scan; it produces a diagnostic and the scan continues.
5. Fingerprints every report on six-plus independent dimensions (SQL text,
   tables, columns, parameters, visual types, name) and scores every pair for
   overlap, so you can find near-duplicate reports and get a recommended
   keeper before a migration.
6. Answers "does a report like this already exist?" from a natural-language
   description, ranked against the fingerprinted estate.

## Install

From this folder:

```bash
pip install -e .
# or, with the optional extras:
pip install -e ".[all]"
```

`sqlglot` (extra `sqlparse`) improves T-SQL table-reference extraction; the
package works without it, falling back to a regex-based heuristic pass.
`requests` (extra `server`) is required only for `scan-server` / the
`ReportServerClient`; every other feature works without it.

## Command line

```bash
# Scan a local folder of RDL/DTSX/conmgr/SQL files
python -m reportlineage scan ./my-estate --out ./out --mermaid --html --csv

# Clone a git repository (shallow) and scan it
python -m reportlineage scan-git https://github.com/org/repo.git --branch main

# Scan a live SSRS / Power BI Report Server catalog (needs the 'server' extra)
python -m reportlineage scan-server https://myserver/reports \
    --user domain\\svc-account --password-env REPORTLINEAGE_PASSWORD

# Find duplicate / overlapping reports
python -m reportlineage duplicates ./my-estate --out duplicates.json

# Search an estate in natural language
python -m reportlineage search ./my-estate "monthly sales by region"

python -m reportlineage --version
```

`scan` and its siblings write `estate.json` (the full graph, via
`EstateGraph.to_graph_json()`) plus, when requested, `estate.mmd` (a Mermaid
flowchart), `estate.html` (a self-contained static summary page), and
`nodes.csv` / `edges.csv`.

Prefer `--password-env` over `--password` for `scan-server`: an environment
variable does not end up in shell history or a process list the way a
literal command-line argument can.

## Library

```python
from reportlineage import build_estate, fingerprint_rdl, analyze_duplicates, SearchIndex

graph = build_estate(
    rdl_paths=["reports/Sales.rdl"],
    dtsx_paths=["etl/LoadSales.dtsx"],
    conmgr_paths=["etl/OrdersDW.conmgr"],
)
print(graph.totals, graph.diagnostics)

fingerprints = [fingerprint_rdl(p, key=p) for p in ["a.rdl", "b.rdl", "c.rdl"]]
summary = analyze_duplicates(fingerprints)
for cluster in summary.clusters:
    print(cluster.verdict, cluster.action, [m.name for m in cluster.members])

index = SearchIndex(fingerprints)
for fp, score in index.search("monthly sales by region"):
    print(score, fp.name)
```

## Package layout

```
reportlineage/
  models.py             EstateGraph, LineageNode/Edge/Diagnostic, enums
  signature.py           table_signature(): the cross-source join key
  connect_string.py       OLE DB / ADO.NET connection string parsing
  xml_utils.py            namespace-agnostic ElementTree helpers
  sql_refs.py             T-SQL table-reference extraction (sqlglot + regex fallback)
  types.py                a small local DataType enum (no host dependency)
  parsers/
    rdl.py                narrow, lineage-only RDL parser
    dtsx.py                narrow, lineage-only SSIS package parser
    conmgr.py              SSIS connection manager parser
    sql_file.py            standalone .sql DDL classification
  builder.py              nodes_from_*, link_estate, build_estate
  inventory.py            rollups over a node list
  fingerprint.py           ReportFingerprint, tokenize, normalize helpers
  duplicate_weights.py + data/duplicate_weights.json   judgments as data
  duplicates.py           compare, analyze_duplicates, find_reuse
  search.py               SearchIndex over fingerprints
  export.py               to_json / to_mermaid / to_csv_* / to_html_summary
  scanners/
    filesystem.py          scan a local folder
    git_repo.py             shallow-clone then scan
    report_server.py        SSRS / PBIRS REST v2.0 client + scan
  cli.py, __main__.py      python -m reportlineage ...
```

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests -q
```

103 tests, all passing at time of writing, covering every parser, the graph
builder's cross-source join, fingerprinting, duplicate clustering, reuse
search, every export format, the filesystem scanner, a mocked report-server
client, and the CLI end to end.

## Known limitations

T-SQL table-reference extraction is heuristic. `sqlglot` improves accuracy
materially; without it, the regex fallback handles the common statement
shapes (`SELECT ... FROM/JOIN`, `INSERT INTO`, `UPDATE`, `DELETE FROM`,
`MERGE`, `CREATE TABLE/VIEW`, `EXEC`) but not everything a hand-written T-SQL
grammar would. Dynamic SQL (`EXEC(@sql)`, `sp_executesql`) is never guessed;
it is flagged as a diagnostic instead.

The report-server scanner targets the REST v2.0 API (`/reports/api/v2.0`),
available on SSRS 2017 and later and on Power BI Report Server. SSRS 2008 R2
through 2016 need the older SOAP `ReportService2010.asmx` endpoint, which
this package does not implement; see `PORTING.md` for where to find a
reference SOAP client if that is needed later.

Duplicate-detection thresholds in `data/duplicate_weights.json` are
judgments calibrated for a typical SSRS estate. Treat `retire` and `merge`
recommendations as a starting point for review, not an automatic action, and
recalibrate the weights against a labelled sample from your own estate before
trusting them at scale.
