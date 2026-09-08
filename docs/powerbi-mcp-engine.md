# Report Studio: Power BI Modeling MCP engine (`powerbi_mcp`)

Step-by-step guide to setting up, running, and troubleshooting the `powerbi_mcp`
Report Studio engine, which combines the headless Claude Code CLI with Microsoft's
local **Power BI Modeling MCP server** (`@microsoft/powerbi-modeling-mcp`, public
preview) to generate a PBIP report and then validate/refine its semantic model.

See `docs/report-studio.md` for the feature overview and the other engines.

---

## What the engine does

| Stage | Actor | What happens |
| --- | --- | --- |
| 1. Design | Claude Code CLI (`claude -p`) | Prompt + grounding become a validated `IRWorkbook` (JSON design of tables, measures, visuals). Uses the CLI's own auth — no `ANTHROPIC_API_KEY`. |
| 2. Render | Deterministic pipeline | The IR is rendered to a PBIP project under `jobs/<id>/build/<Project>/`: sample or uploaded data, DAX, TMDL semantic model, PBIR report, theme, compatibility fixer. |
| 3. Refine | Claude Code CLI + Power BI Modeling MCP server | A second `claude -p` run, with the MCP server registered via a job-local config, opens the generated `.SemanticModel` TMDL, reviews DAX, data types, format strings, summarization, and relationships, applies best practices, saves the TMDL, and the output ZIP is rebuilt. |

Stage 3 is **best-effort**: any failure (timeout, MCP error, non-zero exit) is
logged on the job and the un-refined — but fully valid — PBIP remains the output.
The agent is instructed to never rename or remove objects (the report layer binds
by name) and to work only with the local files (no Desktop, no Fabric).

---

## Prerequisites (server host)

These apply to the machine/process running the backend:

1. **Claude Code CLI** installed and authenticated for the account the backend
   runs as. Verify: `claude --version`. The command name is configurable via
   `REPORT_STUDIO_CLAUDE_CMD` (default `claude`).
2. **Node.js 20+** so `npx` is on PATH. Verify: `npx --version`.
3. Optional but recommended for servers — pre-install the MCP server to avoid
   the first-run `npx` download:

   ```powershell
   npm i -g @microsoft/powerbi-modeling-mcp
   ```

Explicit non-requirements for this engine:

- No `ANTHROPIC_API_KEY` or other AI provider key.
- No Power BI Desktop and no Fabric connection or Microsoft sign-in — the MCP
  server works on the PBIP/TMDL files on disk.
- No tenant admin setting. (The "Users can use the Power BI Model Context
  Protocol server endpoint" preview toggle applies to Microsoft's *remote* MCP
  server only, which this engine does not use.)

Readiness check: `GET /api/report-studio/engines` must show
`{"id": "powerbi_mcp", "configured": true}`. `configured` is true only when the
Claude CLI **and** `npx` are both detected on PATH.

---

## Running a generation — UI steps

1. Start the backend (`uvicorn backend.app.main:app ...`) and open the app.
2. Go to **Report Studio**.
3. **Step 1 — prompt**: describe the report (see sample prompts below).
   Optionally set a project name.
4. **Step 2 — grounding (optional)**:
   - Attach `.rdl` / `.dtsx` files so the design targets real tables/columns.
   - Attach **sample data** (`.csv`, `.xlsx`, `.xls`) — files are matched to
     datasets by name (e.g. `Fact Sale.csv` binds to a datasource named
     "Fact Sale"); datasets without a file get generated sample rows.
   - Optionally pick a live Fabric model or paste a schema, or select a prior
     estate scan.
5. **Step 3 — engine**: select **Power BI Modeling MCP (local)**. If it shows
   as not ready, fix the prerequisites above.
6. Generate. Expect roughly 1–4 minutes: design 30–120 s, render a few seconds,
   refinement 30–120 s (plus a one-time `npx` package download on first use).
7. Watch the job log for the stage markers (see "What the job log shows").
8. Download the PBIP ZIP from the job view and open it in Power BI Desktop.

## Running a generation — API steps

```powershell
# 1. Authenticate (JWT)
$auth = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/auth/login `
  -ContentType application/json -Body '{"username":"admin","password":"<pw>"}'
$h = @{ Authorization = "Bearer $($auth.access_token)" }

# 2. Confirm the engine is ready
Invoke-RestMethod -Uri http://localhost:8000/api/report-studio/engines -Headers $h

# 3. Launch (multipart form; files/data_files optional)
$form = @{
  prompt       = "Build a sales overview report. ..."
  project_name = "SalesOverview"
  engine       = "powerbi_mcp"
  # data_files = Get-Item .\FactSales.csv   # optional sample data upload
}
$job = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/report-studio/generate `
  -Headers $h -Form $form

# 4. Poll and download
Invoke-RestMethod -Uri "http://localhost:8000/api/jobs/$($job.job_id)" -Headers $h
Invoke-WebRequest -Uri "http://localhost:8000/api/jobs/$($job.job_id)/download" `
  -Headers $h -OutFile report.zip
```

---

## Sample prompts

Prompts that play to the generator's strengths: name the tables and columns,
name the visual types, and ask for measures explicitly.

Minimal smoke test (fastest end-to-end check):

```
A minimal sales report with one page: a KPI card for Total Revenue (SUM of
Sales[Revenue]) and a bar chart of Revenue by Region. One table 'Sales' with
columns Region (text), Revenue (decimal).
```

Ungrounded, fuller model (the MCP refinement has more to review — relationships,
format strings, a measure table):

```
Build a sales overview report. Use a FactSales table (SalesAmount, TotalCost,
OrderQuantity, OrderDate, ProductKey, CustomerKey) and dimensions DimProduct
(ProductKey, ProductName, Category) and DimDate (DateKey, FullDate, Year, Month).
Add KPI cards for Total Sales, Total Profit, and Profit Margin; a line chart of
Total Sales by Month; a bar chart of Total Sales by Category; and a table of the
top products by Total Sales.
```

Grounded on the Wide World Importers warehouse (attach
`SampleContent/wide-world-importers/wwi-ssis/wwi-ssis/DailyETLMain.dtsx`):

```
Build a Wide World Importers sales dashboard against the warehouse. Use Fact.Sale
(Total Excluding Tax, Profit, Quantity, Invoice Date Key, Stock Item Key,
Customer Key, City Key), Dimension.Customer, Dimension.StockItem, Dimension.City,
and Dimension.Date. KPI cards for Total Sales, Total Profit, and Profit Margin;
Total Sales by Calendar Month (line); Total Sales by Sales Territory (bar); and a
top-10 customers table by Total Sales.
```

With uploaded sample data: attach `FactSales.csv`, `DimProduct.csv`,
`DimDate.csv` in the Sample data picker and use the "sales overview" prompt
above — the pipeline binds each file to its same-named dataset, so the PBIP
opens with your real rows instead of synthetic ones.

More scenario prompts (Fabric-schema-grounded, estate-scan-grounded, SSRS
modernization) are in `docs/report-studio.md` under "Demo prompts".

---

## What the job log shows

A successful `powerbi_mcp` run logs, in order:

```
Report Studio engine: powerbi_mcp
[10%] Generating report
Report Studio: requesting IRWorkbook from headless Claude Code CLI
... standard pipeline steps A-F (data model, DAX, TMDL, PBIR, theme, package) ...
Power BI MCP: refining semantic model via powerbi-modeling-mcp
Power BI MCP refinement: <agent's summary of the changes it made>
Power BI MCP: output ZIP rebuilt with refined model
```

Skip/failure lines you may see instead (job still succeeds):

```
Power BI MCP refinement skipped: claude CLI or npx missing
Power BI MCP refinement skipped: no SemanticModel/definition found
Power BI MCP refinement timed out after 300s (report is unaffected)
Power BI MCP refinement failed (report is unaffected): <detail>
```

Artifacts per job (`backend/jobs/<id>/`): `powerbi_mcp_config.json` (the MCP
registration used for the refinement pass), `build/<Project>/` (the PBIP,
including the refined `*.SemanticModel/definition/*.tmdl`), `<Project>.zip`
(the rebuilt download), `ir_workbook.json`, `pipeline.log`.

---

## Verification performed (2026-07-04)

What was tested when this engine was wired, and how:

1. **Automated tests** — `backend/tests/test_report_studio.py` (34 Report Studio
   tests passing), including for this engine:
   - engine registry dispatch and UI status (`configured` requires CLI + npx);
   - clear errors naming the missing prerequisite (Claude CLI vs. `npx`);
   - refinement skips cleanly when no `SemanticModel/definition` exists;
   - a full refinement run against a faked CLI asserting the generated
     `powerbi_mcp_config.json` contents (`npx -y
     @microsoft/powerbi-modeling-mcp@latest --start`, stdio) and that the output
     ZIP is rebuilt containing the refined TMDL.
2. **Live MCP server, protocol level (no mocks)** — the server (v0.5.0-beta.11)
   was driven over stdio JSON-RPC using exactly the launch spec the engine
   writes, against a real backend-generated PBIP (Superstore dashboard):
   - MCP handshake OK; **21 tools** exposed (`table_operations`,
     `measure_operations`, `dax_query_operations`, `relationship_operations`, ...);
   - `connection_operations.ConnectFolder` on the generated `.SemanticModel`
     loaded 5 tables, 4 measures, 2 relationships, the backend's TMDL parses
     cleanly in Microsoft's engine;
   - write path round-trip: `measure_operations.Create` (new measure with DAX +
     format string) followed by `database_operations.ExportToTmdlFolder`, and the
     measure verified present in `_Measures.tmdl` on disk. **PASS.**
3. **Not covered by automation**: the live agent passes (`claude -p`) inside a
   real generation, because they run with `--permission-mode bypassPermissions`
   and are exercised only in a real deployment. The invocation pattern is
   identical to the already-shipping `claude_code_cli` engine. Run the minimal
   smoke-test prompt above through the UI to confirm end-to-end on a new host.

Useful protocol facts confirmed during testing (for future adapter work):

- Transport is newline-delimited JSON-RPC over stdio; protocol version
  `2024-11-05` accepted.
- `ConnectFolder` wants the folder containing the `definition` subfolder (the
  `<Project>.SemanticModel` directory) — connecting does **not** auto-save.
- Persisting edits requires an explicit `database_operations.ExportToTmdlFolder`
  back to the `definition` folder; the refinement prompt therefore instructs the
  agent to save changes to disk.
- Tool requests are wrapped: `{"request": {"operation": ..., ...}}`; every tool
  supports `operation: "Help"` with schemas and examples.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Engine shows "not ready" in the selector | `claude` or `npx` not on PATH for the backend process. Check `claude --version` and `npx --version` in the same shell/user that runs uvicorn, then restart the backend. |
| Job fails immediately: "needs the Claude Code CLI" | Install/authenticate the CLI, or set `REPORT_STUDIO_CLAUDE_CMD` to its full path. |
| Job fails immediately: "needs Node.js 'npx'" | Install Node.js 20+. |
| Refinement times out | First run downloads the npm package; pre-install globally. Raise `REPORT_STUDIO_CLI_TIMEOUT_SECONDS` (default 300) if models are large. |
| "refinement failed (report is unaffected)" | Read the logged detail. The PBIP is still valid; refinement is an enhancement pass. The MCP server is preview software — pin a known-good version by pre-installing globally if `@latest` regresses. |
| Want to see exactly what the agent changed | Diff `build/<Project>/<Project>.SemanticModel/definition/` against a re-render, or read the refinement summary line in the job log. |

## References

- Engine code: `backend/app/core/report_studio/powerbi_mcp.py`,
  `PowerBiMcpGenerator` in `backend/app/core/report_studio/generator.py`.
- Microsoft docs: [Power BI MCP servers overview](https://learn.microsoft.com/power-bi/developer/mcp/mcp-servers-overview),
  [powerbi-modeling-mcp on GitHub](https://github.com/microsoft/powerbi-modeling-mcp).
- Feature overview and other engines: `docs/report-studio.md`.
