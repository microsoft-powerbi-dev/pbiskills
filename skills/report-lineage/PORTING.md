# Porting `reportlineage` to another repository

This package was built to leave. It has no import of `app.*`, `backend.*`,
or `shared.*` anywhere in `reportlineage/`. Moving it is copying a directory.

## Steps

1. Copy the whole `skills/report-lineage/` folder to the destination
   repository, at whatever path makes sense there (it does not need to stay
   under a folder named `skills`).
2. `pip install -e .` (or `-e ".[all]"` for `sqlglot` and `requests`) inside
   the destination project's environment.
3. Run `python -m pytest tests -q` there to confirm the move did not break
   anything: it should not, since the package has no path assumptions beyond
   its own directory (the bundled `data/duplicate_weights.json` is loaded via
   a path relative to `duplicate_weights.py`'s own `__file__`, not the
   original repository's root).
4. Delete `PORTING.md` and this section of `README.md` once the move is
   final, or leave them, they do no harm sitting in a new home.

Nothing else is required. There is no repo-root `PYTHONPATH` dependency, no
shared settings module, and no database.

## Provenance: which backend module inspired which package module

This package is an independent reimplementation, not a copy. Each row names
the backend file that established the pattern being reproduced, in case the
backend's version changes and this package should be revisited.

| `reportlineage` module | Reimplements the approach in | What changed |
| --- | --- | --- |
| `models.py` | `shared/lineage_models.py` | Same shape, pydantic-only, no other repository import |
| `signature.py` | `backend/app/core/lineage/signature.py` | Unchanged semantics |
| `connect_string.py` | `backend/app/core/parser/connect_string.py` | Unchanged semantics |
| `xml_utils.py` | `backend/app/core/parser/rdl_xml.py` | Local `DataType` in `types.py` instead of `shared.ir_models.DataType` |
| `sql_refs.py` | `backend/app/core/parser/sql_tables.py` | Same dual-strategy approach (sqlglot first, regex fallback) |
| `parsers/rdl.py` | `backend/app/core/parser/rdl_parser.py` | Narrowed to what lineage needs: datasets, connections, parameters, visual counts. Does not build a full `IRWorkbook`, does not do M-query synthesis, does not resolve visuals beyond a kind count |
| `parsers/dtsx.py` | `backend/app/core/parser/dtsx_parser.py` | Narrowed similarly: OLE DB source/destination tables and Execute SQL task text. Does not model every task type |
| `parsers/conmgr.py` | (same file, `load_connections`/`parse_conmgr` helpers) | Standalone, same namespace-agnostic approach |
| `parsers/sql_file.py` | `backend/app/core/parser/sql_file_parser.py` | A single classification function rather than the fuller DDL-surface model |
| `builder.py` | `backend/app/core/lineage/builder.py` | Same signature-based join and per-file fail-soft design. Does not implement SSAS `.bim` or shared-dataset `.rsd` extraction; add `parsers/bim.py` / `parsers/rsd.py` following the same pattern if needed |
| `inventory.py` | `backend/app/core/lineage/inventory.py` | Unchanged semantics |
| `fingerprint.py` | `backend/app/core/complexity/duplicate_fingerprint.py` | Built directly from `RdlReport` rather than an `IRWorkbook` dict; one fewer dimension (no `measures`, since the narrow RDL parser does not extract calculated-field formulas) |
| `duplicate_weights.py` + `data/duplicate_weights.json` | `backend/app/core/complexity/duplicate_weights.py` + its JSON | Weights renormalised for the dimensions this package actually populates |
| `duplicates.py` | `backend/app/core/complexity/duplicates.py` | Same blocking, clustering, and keeper-selection design |
| `search.py` | (no direct backend equivalent; `find_reuse` in `duplicates.py` is the closest) | New: a small inverted-index search convenience over fingerprints |
| `export.py` | (no direct backend equivalent; the frontend renders the graph JSON) | New: JSON, Mermaid, CSV, and a static HTML summary, so the graph is usable without a UI |
| `scanners/filesystem.py` | `backend/app/core/lineage/runner.py`'s file-bucketing step | Standalone, no `AnalysisJobState` |
| `scanners/git_repo.py` | `backend/app/core/git_repos/clone.py` | Simplified: shallow clone only, token via an environment variable, no credential-store integration |
| `scanners/report_server.py` | `backend/app/core/report_server/rest_api.py` | REST v2.0 only. The backend's `soap_api.py` (SSRS 2008 R2 through 2016) is not reimplemented; port it the same way if an older server needs support |
| `cli.py` | (no backend equivalent; the backend is a web API, not a CLI) | New |

## If you need SSAS (`.bim`) or shared-dataset (`.rsd`) lineage

Not included. Follow the same pattern as `parsers/rdl.py`: a narrow
dataclass capturing only what lineage needs, a namespace-agnostic parse via
`xml_utils`, a `nodes_from_*` function in `builder.py` returning the same
`(nodes, edges, diagnostics, root_id, table_ids)` tuple shape, and a new
optional argument on `build_estate`. `backend/app/core/parser/bim_parser.py`
in the source repository is the reference implementation to narrow down,
the same way `rdl_parser.py` was narrowed into `parsers/rdl.py` here.

## If you need the SOAP report-server endpoint

`backend/app/core/report_server/soap_api.py` in the source repository
targets `ReportService2010.asmx` for SSRS 2008 R2 through 2016. Add a
`scanners/report_server_soap.py` module with the same `ReportServerClient`
shape (`probe`, `list_catalog`, `download_definition`, `scan`) so `cli.py`'s
`scan-server` command can select a transport by server version without any
other change.
