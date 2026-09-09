# rdl-generation: deep dive

Companion to `skills/claude-skills/rdl-generation/SKILL.md`. That file is the
pitch and the workflow; this file documents the actual implementation: every
public function and its signature, the JSON spec schema, every validation
check with its exact code and severity, what each reference doc and example
covers, and how to run the scripts directly.

All line numbers below refer to the files as of this writing; re-check them
after any edit to the scripts.

## 1. Overview

The skill ships three standalone Python files under
`skills/claude-skills/rdl-generation/scripts/`:

| File | Role |
| --- | --- |
| `rdl_builder.py` | Builds a well-formed RDL 2005/2008/2010/2016 document, either via a fluent `RdlBuilder` API or from a JSON spec (CLI or library call). |
| `validate_rdl.py` | Validates an existing `.rdl` file for structural correctness: well-formedness, namespace, element order, field/dataset reference integrity, measurement units, header/footer placement, body geometry. |
| `mcp_server.py` | Wraps both of the above as MCP tools, plus read access to the skill's `references/*.md` and `examples/*.rdl`, so an agentic IDE can drive the whole workflow over stdio. |

Zero-dependency design: all three import only the Python standard library
(`xml.etree.ElementTree`, `dataclasses`, `typing`, `pathlib`, `json`,
`argparse`, `sys`, `re`, `uuid`, `io`). `mcp_server.py` additionally attempts
`import fastmcp`, but degrades gracefully — every tool function is still
directly callable as plain Python when `fastmcp` is absent; only the stdio
server entry point (`main()`) requires it. Any of the three files can be
copied alone into another repository and it runs unmodified on a stock Python
3.9+ install (`rdl_builder.py` and `validate_rdl.py` docstrings both state
this explicitly).

The builder and validator have no import relationship to this repository's
production backend (`backend/app/core/generator/rdl_generator.py`,
`backend/app/core/parser/rdl_parser.py`). They are a self-contained,
independently distributable reimplementation of the same rules, sized for
copy-paste into any project. Where behavior mirrors the backend (round-trip
expression forms, CLS-compliant field names, `rd:TypeName` handling), the
reference docs call it out explicitly.

## 2. `rdl_builder.py` API reference

### Module-level functions

| Function | Signature | Returns | Notes |
| --- | --- | --- | --- |
| `_safe_id` | `_safe_id(name: str) -> str` | CLS-compliant identifier | Replaces every non-word character with `_`; prefixes with `_` if the result starts with a digit or is empty. Not exported for use outside the module but referenced here because every `Name` attribute and every field token passes through it. |
| `field_ref` | `field_ref(name: str) -> str` | `"=Fields!{safe_name}.Value"` | The round-trip-safe field reference form. |
| `aggregate_ref` | `aggregate_ref(func: str, name: str) -> str` | `"={func}(Fields!{safe_name}.Value)"` | The round-trip-safe aggregate form, e.g. `aggregate_ref("Sum", "Amount")` → `"=Sum(Fields!Amount.Value)"`. `func` is not validated against a known-aggregate set here (that happens in `RdlBuilder.add_table`/`_build_table`, which falls back to `Sum` for anything outside `_AGG_FUNCS`). |
| `_normalize_type_name` | `_normalize_type_name(rdl_type: str) -> str` | a `System.*` CLR type name | Passes through anything already starting with `system.` (case-insensitive); otherwise looks up `_TYPE_ALIASES` (`string`, `int`, `int32`, `integer`, `int64`, `long`, `decimal`, `float`, `double`, `bool`, `boolean`, `datetime`, `date`, `guid`); falls back to `f"System.{rdl_type}"` for anything unrecognized. |
| `build_from_spec` | `build_from_spec(spec: dict, default_report_name: str = "Report") -> RdlBuilder` | a populated, unbuilt `RdlBuilder` | Translates a parsed JSON spec dict into `RdlBuilder` calls. See section 4 for the spec schema. |
| `main` | `main(argv: Optional[Sequence[str]] = None) -> int` | process exit code | CLI entry point: `python rdl_builder.py spec.json output.rdl`. Reads the spec file, calls `build_from_spec`, calls `.save(output)`, prints `Wrote {path}`, returns 0. |

### `RdlBuilder` class

`RdlBuilder(report_name: str, schema_version: str = "2016")` — raises
`ValueError` if `schema_version` is not one of `"2005"`, `"2008"`, `"2010"`,
`"2016"` (`_SCHEMA_NS` keys). Internally resolves to the matching namespace
URI and constructs an `_NsBuilder` (`self._x`) bound to it plus the constant
`rd` namespace (`http://schemas.microsoft.com/SQLServer/reporting/reportdesigner`).

All `add_*` methods append to internal lists and return `self` (fluent
chaining); nothing is written to an XML tree until `build()`/`save()` runs, so
call order does not matter.

| Method | Signature | Effect |
| --- | --- | --- |
| `add_datasource` | `add_datasource(name, connect_string, provider="SQL") -> RdlBuilder` | Registers an embedded `DataSource` (`ConnectionProperties` form only — no shared-datasource support in this builder). |
| `add_dataset` | `add_dataset(name, datasource_name, command_text, fields: Sequence[Tuple[str,str]], command_type="Text", query_parameters: Optional[Dict[str,str]]=None) -> RdlBuilder` | Registers a `DataSet`. `fields` entries are `(name, rdl_type)`; `rdl_type` is normalized via `_normalize_type_name`. |
| `add_parameter` | `add_parameter(name, data_type, prompt=None, default=None, nullable=False, multi_value=False, valid_values=None) -> RdlBuilder` | Registers a `ReportParameter`. Raises `ValueError` if `data_type` is not one of `Boolean`, `DateTime`, `Integer`, `Float`, `String` (`_RDL_PARAM_TYPES`). A list/tuple `default` implies `multi_value=True` even if the caller passed `multi_value=False`. `valid_values` entries are a bare value or a `(value, label)` pair. |
| `add_table` | `add_table(name, dataset_name, columns: Sequence[ColumnSpec]) -> RdlBuilder` | Registers a flat Tablix. `ColumnSpec = Tuple[str, str, float, Optional[str], Optional[str]]`: `(header_text, field_name, width_in, format_string_or_None, aggregate_or_None)`. |
| `add_page_header` | `add_page_header(text: str) -> RdlBuilder` | Sets a single-line bold page-header title. |
| `add_page_footer_with_page_numbers` | `add_page_footer_with_page_numbers() -> RdlBuilder` | Enables a centered `"Page X of Y"` footer. |
| `set_page_size` | `set_page_size(width_in: float, height_in: float, margins_in: float = 1.0) -> RdlBuilder` | Sets page dimensions and uniform margins (same value on all four sides). Defaults: 8.5 × 11, 1in margins. |
| `build` | `build() -> xml.etree.ElementTree.Element` | Renders every registered piece into one `Report` element. See below for the assembly order. |
| `save` | `save(path: Union[str, Path]) -> Path` | Calls `build()`, creates parent directories, writes with `xml_declaration=True`, `encoding="utf-8"`. Returns the resolved output path. |

`build()` internals worth knowing:

- Registers namespaces (`register_namespace("", self.ns)` and
  `register_namespace("rd", _RD_NS)`) before constructing any element, so the
  default namespace serializes clean (no `ns0:` prefix).
- Emits `DataSources` and `DataSets` only if at least one was registered
  (`if self._datasources:` / `if self._datasets:`).
- Builds tables into `Body/ReportItems` first, stacking each table's `Top` at
  the previous table's bottom edge + 0.25in margin (cursor starts at
  `top_in = 0.25`). `Body/Height` is then set to
  `round(max(top_in, 0.5), 4)` inches — i.e. never less than 0.5in even with
  no tables.
- `ReportParameters` is emitted after `Body`/`ReportSection` internals are
  built but still as a child of `report` before `ReportSections`'s sibling
  `Width`/`Page` are appended — the method body interleaves parameter and
  page-geometry construction; the final serialized child order of `Report`
  still comes out `DataSources`, `DataSets`, `ReportSections`,
  `ReportParameters` (matching the note in `rdl-structure.md` that the
  production generator also puts `ReportParameters` one slot before the
  strict XSD order, which tested tools still accept).
- `body_width_in = max(page_width_in - 2 * margins_in, 1.0)` is used for
  `ReportSection/Width` and for the page header/footer textbox widths — the
  builder does not let the caller set body width independently of page
  width and margins.
- Only ever emits a single `ReportSection` — there is no multi-section
  support in this builder.

Private builder methods (`_build_datasources`, `_build_datasets`,
`_build_parameters`, `_build_table`, `_build_page_header`,
`_build_page_footer`, `_header_cell`, `_value_cell`, `_label_cell`) implement
the schema-order rules described in `references/rdl-structure.md` and
`references/datasets-parameters.md`: `Query` children in order
`DataSourceName`, `CommandType` (only emitted when not `"Text"`),
`CommandText`, `QueryParameters`, then `rd:UseGenericDesigner`; `Field`
children `DataField` then `rd:TypeName`; `ReportParameter` children
`DataType`, `Nullable`, `DefaultValue`, `AllowBlank` (String type only),
`Prompt`, `MultiValue`, `ValidValues`.

`_build_table` emits a flat (ungrouped) Tablix only: one static header
member, one static detail member, and — if any column carries an
`aggregate` — one more static member for a totals row. No `Group` element is
ever emitted, so grouped/matrix tables must be hand-written or built by
extending this class; see `references/tablix-and-charts.md` for the XML
shape a grouped Tablix needs.

### Main entry point example

```python
from rdl_builder import RdlBuilder

b = RdlBuilder("SalesByRegion")                       # schema_version defaults to "2016"
b.add_datasource("SalesDB", "Data Source=SQL01;Initial Catalog=Sales")
b.add_dataset(
    "SalesData", "SalesDB",
    "SELECT Region, Product, Amount FROM dbo.Sales",
    fields=[("Region", "String"), ("Product", "String"), ("Amount", "Decimal")],
)
b.add_table(
    "SalesTable", "SalesData",
    columns=[
        ("Region", "Region", 1.5, None, None),
        ("Product", "Product", 1.5, None, None),
        ("Amount", "Amount", 1.2, "C2", "Sum"),
    ],
)
b.set_page_size(8.5, 11.0, margins_in=1.0)
b.save("SalesByRegion.rdl")
```

Or from the CLI, over a JSON spec (see section 4):

```bash
python rdl_builder.py spec.json output.rdl
```

## 3. `validate_rdl.py` API reference

### Checks performed (module docstring, verified against the check functions)

| # | Check | Function | Code(s) | Severity |
| --- | --- | --- | --- | --- |
| 1 | File parses as well-formed XML | `_check_well_formed` | `not_well_formed` | error |
| 2 | Root element is `Report` | `_check_root` | `root_not_report` | error |
| 3 | Root declares a recognized RDL namespace (2005/2008/2010/2016) | `_check_namespace` | `no_namespace` (missing) / `unknown_namespace` (present but unrecognized) | error / warning |
| 4 | Every `DataSet/Query` presents children in schema order (`DataSourceName` before `CommandType` before `CommandText`) and has a `DataSourceName` | `_check_query_order` | `query_element_order`, `query_missing_datasourcename` | error |
| 5 | Every `Fields!X.Value` reference resolves to a declared `Field` | `_check_field_references` | `unknown_field_reference` | error |
| 6 | Every Tablix/Chart/List/Matrix `DataSetName` resolves to a declared `DataSet` | `_check_datasetname_references` | `unknown_dataset_reference` | error |
| 7 | Every size-like element (`Top`, `Left`, `Width`, `Height`, `PageWidth`, `PageHeight`, the four margins) carries a unit (`in`/`cm`/`mm`/`pt`/`px`) | `_check_units` | `missing_unit_suffix` | error |
| 8 | `PageHeader`/`PageFooter` appear only as direct children of `Page` | `_check_header_footer_placement` | `header_footer_misplaced` | error |
| 9 | `Body` exists and has a nonzero `Height` | `_check_body_height` | `no_body`, `body_no_height`, `body_zero_height` | error |
| 10 | `ReportSection/Width` is not zero or negative (not in the module docstring's numbered list, but implemented) | `_check_body_width` | `section_zero_width` | error |

Note: every finding this validator raises is `error`-severity except
`unknown_namespace`, which is `warning`. There is no `info` severity in this
script (unlike the production validator described in
`references/validation-checklist.md` stage 7, which also has `blocker` and
`info`).

### Helper functions

| Function | Signature | Purpose |
| --- | --- | --- |
| `_local` | `_local(tag: str) -> str` | Strips the `{namespace}` prefix from an ElementTree tag. |
| `_namespace_of` | `_namespace_of(root: Element) -> str` | Extracts the namespace URI from the root tag, or `""`. |
| `_child` / `_children` / `_descendants` | as named | Namespace-agnostic child/descendant lookup by local tag name, case-insensitive — the same pattern documented in `references/namespaces-versions.md`. |
| `_text_of` | `_text_of(elem, name) -> Optional[str]` | Stripped text of the first matching direct child, or `None`. |
| `_check_well_formed` | `_check_well_formed(path: Path) -> tuple[Optional[Element], List[Finding]]` | Reads the file as bytes, strips trailing `\x00` (`raw.rstrip(b"\x00")`) before parsing — the same fix documented in `references/namespaces-versions.md` for SSRS's padded downloads. |
| `_declared_dataset_names` / `_declared_field_names` / `_referenced_field_names` | as named | Sets used by checks 5 and 6. Field references are found via `_FIELD_REF_RE = re.compile(rf"Fields!\s*{_FIELD_NAME}\s*\.Value", re.IGNORECASE)` applied to the `.text` of every element containing the substring `"Fields!"`. |

### `Finding` dataclass

```python
@dataclass
class Finding:
    severity: str   # "error" | "warning"
    code: str
    message: str
    def format(self) -> str: ...  # "[SEVERITY] CODE: message"
```

### `validate(path: str) -> List[Finding]`

Runs every check in sequence and returns the combined list; **never raises**.
If the file does not parse, returns immediately after check 1 with only the
`not_well_formed` finding. If the root is not `Report`, returns immediately
after check 2 (namespace/query/field/etc. checks are skipped, since they
assume a `Report` root). Otherwise runs checks 3 through 10 unconditionally
and returns the full list — an empty list means no findings at all, not
"validation not run."

This function, not an exception, is the contract for both the CLI and the MCP
tool: a "validation failure" is represented as one or more `Finding` objects
with `severity == "error"` in the returned list, never as a raised exception.
The only way `validate()` raises is via a bug (e.g., `_check_well_formed`
catches `ParseError`/`OSError` specifically — any other exception type from a
malformed input would propagate).

### `main(argv=None) -> int` (CLI)

```
python validate_rdl.py file.rdl [file2.rdl ...]
python validate_rdl.py --strict file.rdl
```

Prints `== {file} ==`, then each finding as `  [SEVERITY] CODE: message` (or
`  (no findings)`), then a per-run summary line:
`Summary: N file(s), E error(s), W warning(s).`. Exit code is `1` if any file
produced an error-severity finding, or (with `--strict`) any warning-severity
finding; `0` otherwise.

## 4. The JSON spec format

`build_from_spec(spec, default_report_name)` (in `rdl_builder.py`) accepts
this shape — reconstructed from the module docstring and cross-checked
against the field access in the function body:

| Top-level key | Type | Required | Notes |
| --- | --- | --- | --- |
| `report_name` | string | no | Defaults to the output file's stem. |
| `schema_version` | string | no | `"2005"` \| `"2008"` \| `"2010"` \| `"2016"`; default `"2016"`. |
| `datasources` | array of object | no | Each: `name` (required), `connect_string` (required), `provider` (optional, default `"SQL"`). |
| `datasets` | array of object | no | Each: `name` (required), `datasource_name` (required, must match a `datasources[].name`), `command_text` (required), `command_type` (optional, `"Text"` \| `"StoredProcedure"` \| `"TableDirect"`, default `"Text"`), `fields` (array of `[name, type]` 2-tuples), `query_parameters` (optional object mapping `"@Name"` → expression string). |
| `parameters` | array of object | no | Each: `name` (required), `data_type` (required: `Boolean`\|`DateTime`\|`Integer`\|`Float`\|`String`), `prompt` (optional, defaults to `name`), `default` (optional; a JSON array means multi-value), `nullable` (optional, default `false`), `multi_value` (optional, default `false`), `valid_values` (optional array of bare values or `[value, label]` pairs). |
| `tables` | array of object | no | Each: `name` (required), `dataset_name` (required, must match a `datasets[].name`), `columns` (array of 5-element arrays: `[header_text, field_name, width_in, format_string_or_null, aggregate_or_null]`). |
| `page_header_text` | string | no | Enables a page header with this title. |
| `page_footer_page_numbers` | boolean | no | Enables the `"Page X of Y"` footer. |
| `page` | object | no | `{width_in, height_in, margins_in}`, all optional, defaulting to `8.5`, `11.0`, `1.0`. |

Everything not required falls back to a safe default; `build_from_spec`
raises `KeyError` if a required key is absent from an entry in `datasources`,
`datasets`, `parameters`, or `tables` (the `mcp_server.build_rdl` tool catches
this and returns `{"ok": false, "error": "..."}` rather than propagating it).

### Complete worked example

A datasource, a dataset with a query parameter, a report parameter, and one
table:

```json
{
  "report_name": "RegionSales",
  "schema_version": "2016",
  "datasources": [
    {
      "name": "SalesDB",
      "connect_string": "Data Source=SQL01;Initial Catalog=Sales",
      "provider": "SQL"
    }
  ],
  "datasets": [
    {
      "name": "SalesData",
      "datasource_name": "SalesDB",
      "command_text": "SELECT Region, Product, Amount FROM dbo.Sales WHERE Region = @Region",
      "command_type": "Text",
      "fields": [
        ["Region", "String"],
        ["Product", "String"],
        ["Amount", "Decimal"]
      ],
      "query_parameters": { "@Region": "=Parameters!Region.Value" }
    }
  ],
  "parameters": [
    {
      "name": "Region",
      "data_type": "String",
      "prompt": "Region",
      "default": "East",
      "nullable": false,
      "multi_value": false,
      "valid_values": [
        ["East", "East Region"],
        ["West", "West Region"]
      ]
    }
  ],
  "tables": [
    {
      "name": "SalesTable",
      "dataset_name": "SalesData",
      "columns": [
        ["Region", "Region", 1.5, null, null],
        ["Product", "Product", 1.5, null, null],
        ["Amount", "Amount", 1.2, "C2", "Sum"]
      ]
    }
  ],
  "page_header_text": "Sales by Region",
  "page_footer_page_numbers": true,
  "page": { "width_in": 8.5, "height_in": 11.0, "margins_in": 1.0 }
}
```

Build it: `python rdl_builder.py spec.json RegionSales.rdl`, or in-process
via `mcp_server.build_rdl(spec_json, "RegionSales.rdl")`.

`mcp_server.get_json_spec_schema()` returns this same schema at runtime by
slicing `rdl_builder.__doc__` from the substring `"JSON spec schema"` onward
— it is not a separately maintained copy, so it always matches the docstring
above verbatim.

## 5. Reference docs (`references/*.md`)

| File | Covers |
| --- | --- |
| `rdl-structure.md` | The full `Report` element tree from root to leaf, the required-vs-optional element list, per-element child-order tables (`Report`, `DataSource`, `ConnectionProperties`, `DataSet`, `Query`, `Field`, `ReportParameter`, `ReportSection`, `Body`, `Page`, `Tablix`, `Textbox`, `Chart`), the RDL-2008-vs-2010/2016 structural split (where `Body`/`Width`/`Page` live), the `PageHeader`/`PageFooter`-inside-`Page` rule with a wrong/right example pair, and a complete minimal valid RDL 2016 document with every element annotated. |
| `datasets-parameters.md` | Everything under `DataSources`, `DataSets`, and `ReportParameters`: embedded vs. shared data sources, the `DataProvider` table (SQL/OLEDB/ODBC/Oracle/Teradata/PBIDATASET/etc.), `ConnectString` forms for SQL Server/Azure SQL/Fabric Warehouse/Fabric Lakehouse/Analysis Services/Power BI semantic models, `CommandType` values, `DataField` vs. calculated `Value` fields and the CLS-compliant-identifier rule, the full `rd:TypeName` ↔ .NET type ↔ T-SQL column ↔ RDL parameter `DataType` mapping table, dataset `Filters`, shared datasets, the full `ReportParameter` shape (`DataType`, `Nullable`, `AllowBlank`, `DefaultValue`, `ValidValues`, `MultiValue`, `Hidden`/internal), and cascading-parameter mechanics (declaration order, `DataSetReference`, `QueryParameter` binding). |
| `tablix-and-charts.md` | The Tablix data model (`TablixBody` as physical grid vs. `TablixColumnHierarchy`/`TablixRowHierarchy` as logical structure), the arity rule that most hand-written Tablix XML breaks, static vs. dynamic `TablixMember`, `TablixHeader`, `KeepWithGroup`/`RepeatOnNewPage`, sorting, page breaks per group, three complete worked Tablix fragments (flat table, grouped table with subtotal/grand total, matrix with row+column groups), and the Chart object model (three mandatory children, series arity rule, `ChartMember` label requirement, chart type table, a complete column-chart example, multi-series-by-grouping, scatter/bubble). |
| `expressions.md` | VB.NET expression syntax basics, `Fields!`/`Parameters!`/`Globals!`/`User!`/`ReportItems!` references, the full aggregate-function table and scope rules, `RunningValue`/`Previous`, `Lookup`/`LookupSet`/`MultiLookup`, `IIf`/`Switch`/`Choose`, string/date/math function lists, format-string tables (numeric, date, custom), conditional-formatting patterns, custom `Code` blocks and their constraints, and — the section the skill exists for — the exact two regexes (`_FIELD_RE`, `_AGG_RE`) this repository's parser uses to read expressions back, with a table of forms that do and do not round-trip. |
| `layout-units.md` | The five valid measurement units and their pixel/inch equivalents, why a unit-less value is invalid (and how this repo's own pixel-conversion code treats one if it slips through), absolute positioning rules (`Top`/`Left` relative to container, no reflow, overlap behavior per renderer), the `Body`/`Width` vs. page-canvas distinction, the full `Page` element table, the blank-page arithmetic (`Width + LeftMargin + RightMargin <= PageWidth`) with a worked example and a checklist, standard page sizes with precomputed max body widths, the full `Style` element and its child value tables, growth behavior (`CanGrow`/`CanShrink`/`KeepTogether`/`HideDuplicates`/`RepeatWith`), `ZIndex`, and one fully worked, arithmetic-verified page layout. |
| `namespaces-versions.md` | The namespace URI table for RDL 2003/10 through 2016/01 plus the version-neutral `rd` namespace, what `.rdlx` is and why it's irrelevant, a reads/writes matrix per SSRS/Power BI/Fabric version, a target-version decision table, what changed structurally at each schema version (2005/2008/2010/2016), the namespace-agnostic-parsing pattern (local-tag-name navigation) with the four helper functions this repo's parser uses, the namespace-preserving-write pattern for both "editing an existing file" and "creating a new one", the trailing-null-byte problem and its fix, and a mechanical step-by-step for upgrading a 2008 document to 2016. |
| `validation-checklist.md` | A seven-stage pre-flight checklist: (1) structural well-formedness, (2) reference integrity (dataset/field/parameter/scope names), (3) element order, (4) units, (5) layout arithmetic and Tablix/Chart arity, (6) this repository's Power-BI-paginated compatibility rules and query-quality lint rules (with codes, severities, and trigger patterns), and (7) the production generated-RDL validator's own finding codes. Ends with a symptom-to-root-cause table and the exact PowerShell command to invoke the production validator. This is the deepest and most code-grounded of the six references. |

## 6. Example `.rdl` files (`examples/*.rdl`)

Each file's own `<Description>` element states its intent; summarized here:

| File | Lines | Demonstrates |
| --- | --- | --- |
| `01-minimal-table.rdl` | 231 | The smallest realistic RDL 2016 document: one embedded `DataSource`, one `DataSet`, and a single flat `Tablix` (3 columns) with a bold header row, a plain detail row bound to `=Fields!X.Value`, and a `Sum` totals row with a top border. No parameters, no page header/footer beyond bare page geometry. Good starting skeleton for any new report. |
| `02-grouped-matrix.rdl` | 178 | Row grouping with subtotals: a `Region` group wrapping a nested `Category` detail level, a per-group subtotal row, and a grand-total row scoped to the whole dataset (`Sum(Fields!Amount.Value, "SalesDetail")`). Shows the dynamic-`TablixMember`-plus-sibling-static-member pattern described in `tablix-and-charts.md`. |
| `03-chart-report.rdl` | 168 | A `Chart` (column type) with a `ChartCategoryHierarchy` grouped by region and a static series, paired with a flat table of the same underlying data beneath it — the description calls this "a common real-world pairing." Demonstrates the three mandatory chart children and the category/series/data split. |
| `04-parameterised-proc.rdl` | 150 | A `StoredProcedure`-type dataset (`CommandType>StoredProcedure`, bare procedure name `dbo.usp_SalesByDateAndRegion`) driven by a single-value `DateTime` parameter and a multi-value `String` parameter. The multi-value parameter is flattened with `=Join(Parameters!Region.Value, ",")` into a `QueryParameter`, with the file's own description noting the target procedure is expected to `STRING_SPLIT` it back apart — exactly the pattern `datasets-parameters.md` documents for procedures (as opposed to inline `Text` queries, which SSRS expands into `IN (...)` automatically). |
| `05-header-footer-subreport.rdl` | 170 | A `PageHeader` with a title textbox and a `PageFooter` with a `Globals!PageNumber`/`Globals!TotalPages` expression, plus a `Subreport` element shape that passes one parameter through to a child report. The file's description notes the referenced child report (`OrderLineItems`) is not itself included in the examples folder — this file demonstrates the `Subreport` element's shape only. |

## 7. Running the scripts directly

### Validator, CLI

```bash
python skills/claude-skills/rdl-generation/scripts/validate_rdl.py path/to/report.rdl
python skills/claude-skills/rdl-generation/scripts/validate_rdl.py --strict path/to/report.rdl
python skills/claude-skills/rdl-generation/scripts/validate_rdl.py a.rdl b.rdl c.rdl
```

Exit code `0` means no error-severity finding (and, under `--strict`, no
warning either); `1` otherwise. Findings print to stdout, one per line, in
`[SEVERITY] CODE: message` form, grouped under a `== {file} ==` header per
input file, with a final `Summary: N file(s), E error(s), W warning(s).` line.

### Builder, CLI

```bash
python skills/claude-skills/rdl-generation/scripts/rdl_builder.py spec.json output.rdl
```

Prints `Wrote {resolved_output_path}` and exits 0. Parent directories of
`output.rdl` are created automatically.

### MCP server, standalone

Without `fastmcp` installed, every tool (`get_json_spec_schema`, `build_rdl`,
`validate_rdl_file`, `field_expression`, `list_reference_topics`,
`get_reference`, `list_examples`, `get_example`) is still importable and
directly callable as a plain function:

```python
import sys
sys.path.insert(0, "skills/claude-skills/rdl-generation/scripts")
from mcp_server import build_rdl, validate_rdl_file

result = build_rdl(spec_json, "out/SalesByRegion.rdl")
```

To serve it over stdio for an MCP client (Claude Code, Claude Desktop, or any
other MCP-speaking IDE):

```bash
pip install fastmcp
python skills/claude-skills/rdl-generation/scripts/mcp_server.py
```

`main()` raises `SystemExit` with an explanatory message if `fastmcp` is not
installed at that point — it never silently no-ops.

Point an MCP client's config directly at the file (no launcher wrapper
required, since the script inserts its own directory onto `sys.path` at
import time so `rdl_builder`/`validate_rdl` always resolve as sibling
modules regardless of the caller's working directory):

```json
{
  "mcpServers": {
    "rdl-generation": {
      "command": "python",
      "args": ["/absolute/path/to/skills/claude-skills/rdl-generation/scripts/mcp_server.py"]
    }
  }
}
```

This repository also ships a thin launcher at `mcp/rdl_generation_server.py`
that re-points to the same file (see `mcp/README.md`); either path runs the
identical server.
