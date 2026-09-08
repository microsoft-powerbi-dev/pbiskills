"""
rdl_builder.py - a standalone, dependency-free builder for SSRS / Power BI
paginated RDL 2016 documents.

Only the Python standard library is used: xml.etree.ElementTree, dataclasses,
typing, pathlib, json, argparse, sys, re, uuid. Copy this single file into any
repository and it works on a stock Python 3.9+ install.

Overview
--------
``RdlBuilder`` is a small fluent wrapper around ``xml.etree.ElementTree`` that
emits a well-formed RDL 2016 (or 2010 / 2008) document in the schema's
required child order (RDL is validated as ``xsd:sequence``, so order matters),
with every measurement carrying a unit and every expression in the exact form
the companion parser (``backend/app/core/parser/rdl_parser.py``) reads back:
``=Fields!Name.Value`` and ``=Sum(Fields!Name.Value)``.

Typical use, from Python::

    from rdl_builder import RdlBuilder

    b = RdlBuilder("SalesByRegion")
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

Or from the command line, over a JSON spec::

    python rdl_builder.py spec.json output.rdl

JSON spec schema
-----------------
The top-level JSON object accepts these keys (all optional unless noted):

    {
      "report_name": "SalesByRegion",           // optional, defaults to the output stem
      "schema_version": "2016",                 // optional: "2005" | "2008" | "2010" | "2016"
      "datasources": [
        {
          "name": "SalesDB",                    // required
          "connect_string": "Data Source=SQL01;Initial Catalog=Sales",  // required
          "provider": "SQL"                     // optional, default "SQL"
        }
      ],
      "datasets": [
        {
          "name": "SalesData",                  // required
          "datasource_name": "SalesDB",         // required, must match a datasources[].name
          "command_text": "SELECT Region, Product, Amount FROM dbo.Sales",  // required
          "command_type": "Text",               // optional: "Text" | "StoredProcedure" | "TableDirect"
          "fields": [["Region", "String"], ["Product", "String"], ["Amount", "Decimal"]],
          "query_parameters": {"@Region": "=Parameters!Region.Value"}  // optional
        }
      ],
      "parameters": [
        {
          "name": "Region",                     // required
          "data_type": "String",                // required: Boolean|DateTime|Integer|Float|String
          "prompt": "Region",                   // optional, defaults to name
          "default": "East",                     // optional; a list means MultiValue
          "nullable": false,                     // optional
          "multi_value": false,                  // optional
          "valid_values": [["East", "East Region"], ["West", "West Region"]]  // optional
        }
      ],
      "tables": [
        {
          "name": "SalesTable",                 // required
          "dataset_name": "SalesData",          // required, must match a datasets[].name
          "columns": [
            // [header_text, field_name, width_in, format_string_or_null, aggregate_or_null]
            ["Region", "Region", 1.5, null, null],
            ["Amount", "Amount", 1.2, "C2", "Sum"]
          ]
        }
      ],
      "page_header_text": "Sales by Region",    // optional
      "page_footer_page_numbers": true,          // optional
      "page": {"width_in": 8.5, "height_in": 11.0, "margins_in": 1.0}  // optional
    }

Design notes
------------
- Every ``Name`` attribute is sanitised into a CLS-compliant identifier
  (letters, digits, underscore, not starting with a digit) because SSRS
  rejects a report otherwise ("Field names must be CLS-compliant
  identifiers"). The original spelling is kept in ``<DataField>`` so the
  query's actual column name is preserved.
- Namespaces are registered with ``register_namespace`` before the tree is
  serialised, exactly as ``rdl_generator.py`` does, so the document comes out
  with a clean default namespace and an ``rd:`` prefix instead of ``ns0:``.
- Child element order inside ``Query`` (``DataSourceName``, then
  ``CommandType``, then ``CommandText``, then ``QueryParameters``) and inside
  ``ReportParameter`` (``DataType``, ``Nullable``, ``DefaultValue``,
  ``AllowBlank``, ``Prompt``, ``MultiValue``, ``ValidValues``) follows the RDL
  schema, not alphabetical or arbitrary order.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
from xml.etree.ElementTree import Element, ElementTree, SubElement, register_namespace

# ---------------------------------------------------------------------------
# Schema versions
# ---------------------------------------------------------------------------

_SCHEMA_NS = {
    "2005": "http://schemas.microsoft.com/sqlserver/reporting/2005/01/reportdefinition",
    "2008": "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition",
    "2010": "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition",
    "2016": "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition",
}
_RD_NS = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"

# RDL ReportParameter DataType values this builder accepts.
_RDL_PARAM_TYPES = {"Boolean", "DateTime", "Integer", "Float", "String"}

_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def _safe_id(name: str) -> str:
    """Sanitise a string into a CLS-compliant RDL identifier."""
    s = re.sub(r"\W", "_", name or "")
    if not s:
        return "_"
    return s if not s[0].isdigit() else f"_{s}"


def field_ref(name: str) -> str:
    """Round-trip-safe field reference: ``=Fields!Name.Value``."""
    return f"=Fields!{_safe_id(name)}.Value"


def aggregate_ref(func: str, name: str) -> str:
    """Round-trip-safe aggregate reference: ``=Sum(Fields!Name.Value)``."""
    return f"={func}(Fields!{_safe_id(name)}.Value)"


# ---------------------------------------------------------------------------
# Low-level element helpers
# ---------------------------------------------------------------------------


class _NsBuilder:
    """Element factory bound to one namespace pair, mirroring rdl_generator.py."""

    def __init__(self, ns: str, rd_ns: str = _RD_NS) -> None:
        self.ns = ns
        self.rd_ns = rd_ns

    def el(self, parent: Element, tag: str, text: Optional[str] = None) -> Element:
        e = SubElement(parent, f"{{{self.ns}}}{tag}")
        if text is not None:
            e.text = text
        return e

    def rd_el(self, parent: Element, tag: str, text: Optional[str] = None) -> Element:
        e = SubElement(parent, f"{{{self.rd_ns}}}{tag}")
        if text is not None:
            e.text = text
        return e


# ---------------------------------------------------------------------------
# RdlBuilder
# ---------------------------------------------------------------------------

#: (header_text, field_name, width_in, format_string_or_None, aggregate_or_None)
ColumnSpec = Tuple[str, str, float, Optional[str], Optional[str]]

_AGG_FUNCS = {"Sum", "Avg", "Min", "Max", "Count", "CountDistinct", "First", "Last"}


class RdlBuilder:
    """Fluent builder for a single-section RDL 2016 (or earlier) document.

    Call the ``add_*`` methods in any order (they append to internal lists,
    not directly to the tree), then ``build()`` or ``save(path)`` to render
    the final XML. The internal element order is fixed by ``build()``
    regardless of call order, so ``add_parameter`` after ``add_dataset`` is
    fine.
    """

    def __init__(self, report_name: str, schema_version: str = "2016") -> None:
        if schema_version not in _SCHEMA_NS:
            raise ValueError(
                f"Unknown schema_version {schema_version!r}; expected one of "
                f"{sorted(_SCHEMA_NS)}"
            )
        self.report_name = report_name
        self.schema_version = schema_version
        self.ns = _SCHEMA_NS[schema_version]
        self._x = _NsBuilder(self.ns)

        self._datasources: List[dict] = []
        self._datasets: List[dict] = []
        self._parameters: List[dict] = []
        self._tables: List[dict] = []
        self._page_header_text: Optional[str] = None
        self._page_footer_page_numbers = False
        self._page_width_in = 8.5
        self._page_height_in = 11.0
        self._margins_in = 1.0

    # -- data ----------------------------------------------------------

    def add_datasource(
        self, name: str, connect_string: str, provider: str = "SQL"
    ) -> "RdlBuilder":
        """Register an embedded DataSource (ConnectionProperties form)."""
        self._datasources.append(
            {"name": name, "connect_string": connect_string, "provider": provider}
        )
        return self

    def add_dataset(
        self,
        name: str,
        datasource_name: str,
        command_text: str,
        fields: Sequence[Tuple[str, str]],
        command_type: str = "Text",
        query_parameters: Optional[Dict[str, str]] = None,
    ) -> "RdlBuilder":
        """Register a DataSet. ``fields`` is a list of (name, rdl_type) tuples,
        where ``rdl_type`` is a ``System.*`` CLR type name (e.g. ``"System.String"``,
        ``"String"`` is also accepted and normalised to ``System.String``).
        """
        norm_fields = []
        for fname, ftype in fields:
            norm_fields.append((fname, _normalize_type_name(ftype)))
        self._datasets.append(
            {
                "name": name,
                "datasource_name": datasource_name,
                "command_text": command_text,
                "command_type": command_type,
                "fields": norm_fields,
                "query_parameters": dict(query_parameters or {}),
            }
        )
        return self

    def add_parameter(
        self,
        name: str,
        data_type: str,
        prompt: Optional[str] = None,
        default: Optional[Union[str, List[str]]] = None,
        nullable: bool = False,
        multi_value: bool = False,
        valid_values: Optional[Sequence[Union[str, Tuple[str, str]]]] = None,
    ) -> "RdlBuilder":
        """Register a ReportParameter.

        ``valid_values`` entries are either a bare value or a
        ``(value, label)`` pair. A list/tuple ``default`` implies
        ``multi_value=True``.
        """
        if data_type not in _RDL_PARAM_TYPES:
            raise ValueError(
                f"Unknown RDL parameter DataType {data_type!r}; expected one of "
                f"{sorted(_RDL_PARAM_TYPES)}"
            )
        is_list_default = isinstance(default, (list, tuple))
        self._parameters.append(
            {
                "name": name,
                "data_type": data_type,
                "prompt": prompt or name,
                "default": list(default) if is_list_default else default,
                "nullable": nullable,
                "multi_value": multi_value or is_list_default,
                "valid_values": list(valid_values) if valid_values else None,
            }
        )
        return self

    def add_table(
        self, name: str, dataset_name: str, columns: Sequence[ColumnSpec]
    ) -> "RdlBuilder":
        """Register a flat Tablix: a bold header row, one detail row bound to
        the dataset fields, and a totals row for any column carrying an
        ``aggregate`` (e.g. ``"Sum"``). Pass ``aggregate=None`` for a column
        that should not appear in the totals row (its totals cell is blank).
        """
        self._tables.append(
            {"name": name, "dataset_name": dataset_name, "columns": list(columns)}
        )
        return self

    def add_page_header(self, text: str) -> "RdlBuilder":
        self._page_header_text = text
        return self

    def add_page_footer_with_page_numbers(self) -> "RdlBuilder":
        self._page_footer_page_numbers = True
        return self

    def set_page_size(
        self, width_in: float, height_in: float, margins_in: float = 1.0
    ) -> "RdlBuilder":
        self._page_width_in = width_in
        self._page_height_in = height_in
        self._margins_in = margins_in
        return self

    # -- build -----------------------------------------------------------

    def build(self) -> Element:
        """Render every registered piece into a single ``Report`` element tree."""
        register_namespace("", self.ns)
        register_namespace("rd", _RD_NS)
        x = self._x

        report = Element(f"{{{self.ns}}}Report")

        if self._datasources:
            self._build_datasources(report)
        if self._datasets:
            self._build_datasets(report)

        section = x.el(x.el(report, "ReportSections"), "ReportSection")
        body = x.el(section, "Body")
        items = x.el(body, "ReportItems")

        top_in = 0.25
        for table in self._tables:
            top_in = self._build_table(items, table, top_in)

        body_width_in = max(self._page_width_in - 2 * self._margins_in, 1.0)
        x.el(body, "Height", f"{round(max(top_in, 0.5), 4)}in")

        if self._parameters:
            self._build_parameters(report)

        x.el(section, "Width", f"{body_width_in}in")
        page = x.el(section, "Page")
        if self._page_header_text:
            self._build_page_header(page)
        if self._page_footer_page_numbers:
            self._build_page_footer(page)
        x.el(page, "PageHeight", f"{self._page_height_in}in")
        x.el(page, "PageWidth", f"{self._page_width_in}in")
        x.el(page, "LeftMargin", f"{self._margins_in}in")
        x.el(page, "RightMargin", f"{self._margins_in}in")
        x.el(page, "TopMargin", f"{self._margins_in}in")
        x.el(page, "BottomMargin", f"{self._margins_in}in")

        return report

    def save(self, path: Union[str, Path]) -> Path:
        """Render and write the document, with the XML declaration, UTF-8."""
        report = self.build()
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ElementTree(report).write(str(out_path), encoding="utf-8", xml_declaration=True)
        return out_path

    # -- private builders --------------------------------------------------

    def _build_datasources(self, report: Element) -> None:
        x = self._x
        container = x.el(report, "DataSources")
        for ds in self._datasources:
            node = x.el(container, "DataSource")
            node.set("Name", _safe_id(ds["name"]))
            props = x.el(node, "ConnectionProperties")
            x.el(props, "DataProvider", ds["provider"])
            x.el(props, "ConnectString", ds["connect_string"])
            x.el(props, "IntegratedSecurity", "true")
            x.rd_el(node, "SecurityType", "Integrated")
            x.rd_el(node, "DataSourceID", str(uuid.uuid4()))

    def _build_datasets(self, report: Element) -> None:
        x = self._x
        container = x.el(report, "DataSets")
        for ds in self._datasets:
            node = x.el(container, "DataSet")
            node.set("Name", _safe_id(ds["name"]))
            query = x.el(node, "Query")
            # Schema order: DataSourceName, CommandType, CommandText, QueryParameters.
            x.el(query, "DataSourceName", _safe_id(ds["datasource_name"]))
            command_type = ds["command_type"]
            if command_type and command_type != "Text":
                x.el(query, "CommandType", command_type)
            x.el(query, "CommandText", ds["command_text"])
            if ds["query_parameters"]:
                qparams = x.el(query, "QueryParameters")
                for pname, expr in ds["query_parameters"].items():
                    qp = x.el(qparams, "QueryParameter")
                    qp.set("Name", pname if pname.startswith("@") else f"@{pname}")
                    x.el(qp, "Value", expr)
            x.rd_el(query, "UseGenericDesigner", "true")

            fields = x.el(node, "Fields")
            for fname, ftype in ds["fields"]:
                f = x.el(fields, "Field")
                f.set("Name", _safe_id(fname))
                x.el(f, "DataField", fname)
                x.rd_el(f, "TypeName", ftype)

    def _build_parameters(self, report: Element) -> None:
        x = self._x
        container = x.el(report, "ReportParameters")
        for p in self._parameters:
            rp = x.el(container, "ReportParameter")
            rp.set("Name", _safe_id(p["name"]))
            x.el(rp, "DataType", p["data_type"])
            x.el(rp, "Nullable", "true" if p["nullable"] else "false")
            default = p["default"]
            if default is not None:
                dv = x.el(rp, "DefaultValue")
                values_el = x.el(dv, "Values")
                if isinstance(default, list):
                    for item in default:
                        x.el(values_el, "Value", str(item))
                else:
                    x.el(values_el, "Value", str(default))
            if p["data_type"] == "String":
                x.el(rp, "AllowBlank", "true")
            x.el(rp, "Prompt", p["prompt"])
            if p["multi_value"]:
                x.el(rp, "MultiValue", "true")
            if p["valid_values"]:
                pvals = x.el(x.el(rp, "ValidValues"), "ParameterValues")
                for v in p["valid_values"]:
                    pv = x.el(pvals, "ParameterValue")
                    if isinstance(v, (tuple, list)) and len(v) == 2:
                        x.el(pv, "Value", str(v[0]))
                        x.el(pv, "Label", str(v[1]))
                    else:
                        x.el(pv, "Value", str(v))

    def _build_table(self, items: Element, table: dict, top_in: float) -> float:
        """Emit one flat Tablix: header row, detail row, optional totals row.

        Returns the new ``top_in`` cursor for the next report item.
        """
        x = self._x
        columns: List[ColumnSpec] = table["columns"]
        has_totals = any(col[4] for col in columns)
        n = len(columns)

        tablix = x.el(items, "Tablix")
        tablix.set("Name", _safe_id(table["name"]))
        x.el(tablix, "DataSetName", _safe_id(table["dataset_name"]))

        body = x.el(tablix, "TablixBody")
        tcols = x.el(body, "TablixColumns")
        for _, _, width_in, _, _ in columns:
            x.el(x.el(tcols, "TablixColumn"), "Width", f"{width_in}in")

        trows = x.el(body, "TablixRows")

        # Header row.
        header_row = x.el(trows, "TablixRow")
        x.el(header_row, "Height", "0.25in")
        header_cells = x.el(header_row, "TablixCells")
        for header_text, _, _, _, _ in columns:
            self._header_cell(header_cells, header_text)

        # Detail row.
        detail_row = x.el(trows, "TablixRow")
        x.el(detail_row, "Height", "0.25in")
        detail_cells = x.el(detail_row, "TablixCells")
        for _, field_name, _, fmt, _ in columns:
            self._value_cell(detail_cells, field_ref(field_name), fmt)

        # Optional totals row.
        if has_totals:
            totals_row = x.el(trows, "TablixRow")
            x.el(totals_row, "Height", "0.25in")
            totals_cells = x.el(totals_row, "TablixCells")
            for i, (_, field_name, _, fmt, agg) in enumerate(columns):
                if agg:
                    func = agg if agg in _AGG_FUNCS else "Sum"
                    self._value_cell(
                        totals_cells, aggregate_ref(func, field_name), fmt, bold=True
                    )
                elif i == 0:
                    self._label_cell(totals_cells, "Total")
                else:
                    self._value_cell(totals_cells, "=\"\"", None)

        # Column hierarchy: one static member per column.
        ch = x.el(tablix, "TablixColumnHierarchy")
        chm = x.el(ch, "TablixMembers")
        for _ in range(n):
            x.el(chm, "TablixMember")

        # Row hierarchy: one static member for the header, one for the detail
        # row, and (when there is a totals row) one more static member after
        # it. No Group is emitted, so this is a flat (ungrouped) table -- see
        # tablix-and-charts.md for the grouped form.
        rh = x.el(tablix, "TablixRowHierarchy")
        rhm = x.el(rh, "TablixMembers")
        x.el(rhm, "TablixMember")
        x.el(rhm, "TablixMember")
        if has_totals:
            x.el(rhm, "TablixMember")

        rows = 2 + (1 if has_totals else 0)
        height_in = 0.25 * rows
        x.el(tablix, "Top", f"{top_in}in")
        x.el(tablix, "Left", "0.25in")
        x.el(tablix, "Height", f"{height_in}in")
        width_in = sum(col[2] for col in columns)
        x.el(tablix, "Width", f"{width_in}in")

        return top_in + height_in + 0.25

    def _build_page_header(self, page: Element) -> None:
        x = self._x
        ph = x.el(page, "PageHeader")
        x.el(ph, "Height", "0.4in")
        x.el(ph, "PrintOnFirstPage", "true")
        x.el(ph, "PrintOnLastPage", "true")
        items = x.el(ph, "ReportItems")
        tb = x.el(items, "Textbox")
        tb.set("Name", "PageHeaderTitle")
        run = x.el(x.el(x.el(x.el(tb, "Paragraphs"), "Paragraph"), "TextRuns"), "TextRun")
        x.el(run, "Value", self._page_header_text or self.report_name)
        x.el(x.el(run, "Style"), "FontWeight", "Bold")
        x.el(tb, "Top", "0in")
        x.el(tb, "Left", "0in")
        x.el(tb, "Height", "0.3in")
        width_in = max(self._page_width_in - 2 * self._margins_in, 1.0)
        x.el(tb, "Width", f"{width_in}in")

    def _build_page_footer(self, page: Element) -> None:
        x = self._x
        pf = x.el(page, "PageFooter")
        x.el(pf, "Height", "0.25in")
        x.el(pf, "PrintOnFirstPage", "true")
        x.el(pf, "PrintOnLastPage", "true")
        items = x.el(pf, "ReportItems")
        tb = x.el(items, "Textbox")
        tb.set("Name", "PageFooterPager")
        run = x.el(x.el(x.el(x.el(tb, "Paragraphs"), "Paragraph"), "TextRuns"), "TextRun")
        x.el(run, "Value", '="Page " & Globals!PageNumber & " of " & Globals!TotalPages')
        x.el(x.el(run, "Style"), "TextAlign", "Center")
        x.el(tb, "Top", "0in")
        x.el(tb, "Left", "0in")
        x.el(tb, "Height", "0.25in")
        width_in = max(self._page_width_in - 2 * self._margins_in, 1.0)
        x.el(tb, "Width", f"{width_in}in")

    def _header_cell(self, cells: Element, label: str) -> None:
        x = self._x
        cell = x.el(cells, "TablixCell")
        contents = x.el(cell, "CellContents")
        tb = x.el(contents, "Textbox")
        tb.set("Name", f"hdr_{_safe_id(label)}")
        run = x.el(x.el(x.el(x.el(tb, "Paragraphs"), "Paragraph"), "TextRuns"), "TextRun")
        x.el(run, "Value", label)
        x.el(x.el(run, "Style"), "FontWeight", "Bold")

    def _value_cell(
        self, cells: Element, expr: str, fmt: Optional[str], bold: bool = False
    ) -> None:
        x = self._x
        cell = x.el(cells, "TablixCell")
        contents = x.el(cell, "CellContents")
        tb = x.el(contents, "Textbox")
        tb.set("Name", f"val_{uuid.uuid4().hex[:8]}")
        run = x.el(x.el(x.el(x.el(tb, "Paragraphs"), "Paragraph"), "TextRuns"), "TextRun")
        x.el(run, "Value", expr)
        if fmt or bold:
            style = x.el(run, "Style")
            if fmt:
                x.el(style, "Format", fmt)
            if bold:
                x.el(style, "FontWeight", "Bold")

    def _label_cell(self, cells: Element, label: str) -> None:
        x = self._x
        cell = x.el(cells, "TablixCell")
        contents = x.el(cell, "CellContents")
        tb = x.el(contents, "Textbox")
        tb.set("Name", f"lbl_{_safe_id(label)}")
        run = x.el(x.el(x.el(x.el(tb, "Paragraphs"), "Paragraph"), "TextRuns"), "TextRun")
        x.el(run, "Value", label)
        x.el(x.el(run, "Style"), "FontWeight", "Bold")


_TYPE_ALIASES = {
    "string": "System.String",
    "int": "System.Int32",
    "int32": "System.Int32",
    "integer": "System.Int32",
    "int64": "System.Int64",
    "long": "System.Int64",
    "decimal": "System.Decimal",
    "float": "System.Double",
    "double": "System.Double",
    "bool": "System.Boolean",
    "boolean": "System.Boolean",
    "datetime": "System.DateTime",
    "date": "System.DateTime",
    "guid": "System.Guid",
}


def _normalize_type_name(rdl_type: str) -> str:
    """Normalise a short type alias (``"String"``) or a full ``System.*`` name."""
    if rdl_type.lower().startswith("system."):
        return rdl_type
    return _TYPE_ALIASES.get(rdl_type.lower(), f"System.{rdl_type}")


# ---------------------------------------------------------------------------
# JSON spec -> RdlBuilder
# ---------------------------------------------------------------------------


def build_from_spec(spec: dict, default_report_name: str = "Report") -> RdlBuilder:
    """Construct an ``RdlBuilder`` from a parsed JSON spec (see module docstring)."""
    report_name = spec.get("report_name") or default_report_name
    schema_version = str(spec.get("schema_version") or "2016")
    builder = RdlBuilder(report_name, schema_version=schema_version)

    for ds in spec.get("datasources", []):
        builder.add_datasource(
            ds["name"], ds["connect_string"], provider=ds.get("provider", "SQL")
        )

    for dset in spec.get("datasets", []):
        fields = [(f[0], f[1]) for f in dset.get("fields", [])]
        builder.add_dataset(
            dset["name"],
            dset["datasource_name"],
            dset["command_text"],
            fields=fields,
            command_type=dset.get("command_type", "Text"),
            query_parameters=dset.get("query_parameters"),
        )

    for p in spec.get("parameters", []):
        valid_values = p.get("valid_values")
        norm_valid_values = None
        if valid_values:
            norm_valid_values = [tuple(v) if isinstance(v, list) else v for v in valid_values]
        builder.add_parameter(
            p["name"],
            p["data_type"],
            prompt=p.get("prompt"),
            default=p.get("default"),
            nullable=p.get("nullable", False),
            multi_value=p.get("multi_value", False),
            valid_values=norm_valid_values,
        )

    for t in spec.get("tables", []):
        columns: List[ColumnSpec] = [
            (c[0], c[1], float(c[2]), c[3], c[4]) for c in t.get("columns", [])
        ]
        builder.add_table(t["name"], t["dataset_name"], columns)

    if spec.get("page_header_text"):
        builder.add_page_header(spec["page_header_text"])
    if spec.get("page_footer_page_numbers"):
        builder.add_page_footer_with_page_numbers()

    page = spec.get("page") or {}
    builder.set_page_size(
        float(page.get("width_in", 8.5)),
        float(page.get("height_in", 11.0)),
        margins_in=float(page.get("margins_in", 1.0)),
    )

    return builder


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an RDL file from a JSON spec (see module docstring for schema)."
    )
    parser.add_argument("spec", type=Path, help="Path to the JSON spec file.")
    parser.add_argument("output", type=Path, help="Path to write the .rdl file to.")
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    builder = build_from_spec(spec, default_report_name=args.output.stem)
    out_path = builder.save(args.output)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
