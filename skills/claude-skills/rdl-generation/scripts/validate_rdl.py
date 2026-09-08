"""
validate_rdl.py - standalone structural validator for SSRS / Power BI
paginated RDL files.

Stdlib only: xml.etree.ElementTree, sys, re, argparse, pathlib, dataclasses,
typing. Copy this single file into any repository and run it on a stock
Python 3.9+ install.

Checks performed
-----------------
1. The file parses as well-formed XML.
2. The root element is ``Report``.
3. The root element declares a recognised RDL namespace (2005 / 2008 / 2010
   / 2016).
4. Every ``DataSet/Query`` presents its children in schema order:
   ``DataSourceName`` before ``CommandType`` before ``CommandText``.
5. Every ``Fields!X.Value`` reference (in a Tablix cell, a Chart data point,
   or any other expression) resolves to a ``Field`` declared in some
   ``DataSet``.
6. Every Tablix/Chart ``DataSetName`` resolves to a declared ``DataSet``.
7. Every element carrying a size-like value (``Top``, ``Left``, ``Width``,
   ``Height``, ``PageWidth``, ``PageHeight``, the four margins) has a unit
   suffix (``in`` / ``cm`` / ``mm`` / ``pt`` / ``px``).
8. ``PageHeader`` / ``PageFooter`` appear only as children of ``Page``, never
   directly under ``Report`` or ``ReportSection``.
9. ``Body`` has a nonzero ``Height``.

Usage
-----
    python validate_rdl.py file.rdl [file2.rdl ...]
    python validate_rdl.py --strict file.rdl

Findings print one per line as ``[SEVERITY] CODE: message``. Exit code is 1
if any file has an error-severity finding (or, with ``--strict``, any
warning-severity finding); 0 otherwise. Multiple files are validated
independently and a summary line is printed for each, followed by a totals
line.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set
from xml.etree.ElementTree import Element, ParseError
import xml.etree.ElementTree as ET

SEV_ERROR = "error"
SEV_WARNING = "warning"

_KNOWN_NAMESPACES = {
    "http://schemas.microsoft.com/sqlserver/reporting/2005/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition",
}

_UNIT_RE = re.compile(r"^\s*[0-9]*\.?[0-9]+\s*(in|cm|mm|pt|px)\s*$", re.IGNORECASE)
_FIELD_NAME = r"(?:\[([^\]]+)\]|([A-Za-z_]\w*))"
_FIELD_REF_RE = re.compile(rf"Fields!\s*{_FIELD_NAME}\s*\.Value", re.IGNORECASE)

#: elements whose text content is a size and must carry a unit.
_SIZE_TAGS = {
    "top", "left", "width", "height", "pagewidth", "pageheight",
    "leftmargin", "rightmargin", "topmargin", "bottommargin",
}


@dataclass
class Finding:
    severity: str
    code: str
    message: str

    def format(self) -> str:
        return f"[{self.severity.upper()}] {self.code}: {self.message}"


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _namespace_of(root: Element) -> str:
    return root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""


def _child(elem: Optional[Element], name: str) -> Optional[Element]:
    if elem is None:
        return None
    lname = name.lower()
    for c in list(elem):
        if _local(c.tag).lower() == lname:
            return c
    return None


def _children(elem: Optional[Element], name: str) -> List[Element]:
    if elem is None:
        return []
    lname = name.lower()
    return [c for c in list(elem) if _local(c.tag).lower() == lname]


def _descendants(elem: Optional[Element], name: str) -> List[Element]:
    if elem is None:
        return []
    lname = name.lower()
    return [d for d in elem.iter() if _local(d.tag).lower() == lname]


def _text_of(elem: Optional[Element], name: str) -> Optional[str]:
    c = _child(elem, name)
    if c is not None and c.text is not None:
        return c.text.strip()
    return None


def _check_well_formed(path: Path) -> "tuple[Optional[Element], List[Finding]]":
    findings: List[Finding] = []
    try:
        raw = path.read_bytes().rstrip(b"\x00")
        tree = ET.parse(_BytesIOCompat(raw))
        return tree.getroot(), findings
    except (ParseError, OSError) as exc:
        findings.append(
            Finding(SEV_ERROR, "not_well_formed", f"File is not well-formed XML: {exc}")
        )
        return None, findings


def _BytesIOCompat(raw: bytes):
    import io

    return io.BytesIO(raw)


def _check_root(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    if _local(root.tag) != "Report":
        findings.append(
            Finding(
                SEV_ERROR, "root_not_report",
                f"Root element is '<{_local(root.tag)}>', expected '<Report>'.",
            )
        )
    return findings


def _check_namespace(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    ns = _namespace_of(root)
    if not ns:
        findings.append(
            Finding(SEV_ERROR, "no_namespace", "Root element has no XML namespace.")
        )
    elif ns not in _KNOWN_NAMESPACES:
        findings.append(
            Finding(
                SEV_WARNING, "unknown_namespace",
                f"Namespace '{ns}' is not a recognised RDL 2005/2008/2010/2016 namespace.",
            )
        )
    return findings


def _check_query_order(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    for dataset in _descendants(root, "DataSet"):
        ds_name = dataset.get("Name") or "(unnamed)"
        query = _child(dataset, "Query")
        if query is None:
            continue
        order = [_local(c.tag) for c in list(query)]
        # Only compare the relative order of elements that are actually present.
        present = [tag for tag in ("DataSourceName", "CommandType", "CommandText") if tag in order]
        indices = [order.index(tag) for tag in present]
        if indices != sorted(indices):
            findings.append(
                Finding(
                    SEV_ERROR, "query_element_order",
                    f"DataSet '{ds_name}': Query children out of schema order "
                    f"(found {order}); expected DataSourceName, then CommandType, "
                    "then CommandText.",
                )
            )
        if "DataSourceName" not in order:
            findings.append(
                Finding(
                    SEV_ERROR, "query_missing_datasourcename",
                    f"DataSet '{ds_name}': Query has no DataSourceName.",
                )
            )
    return findings


def _declared_dataset_names(root: Element) -> Set[str]:
    return {d.get("Name") for d in _descendants(root, "DataSet") if d.get("Name")}


def _declared_field_names(root: Element) -> Set[str]:
    names: Set[str] = set()
    for dataset in _descendants(root, "DataSet"):
        for fld in _descendants(_child(dataset, "Fields"), "Field"):
            name = fld.get("Name")
            if name:
                names.add(name)
    return names


def _referenced_field_names(root: Element) -> Set[str]:
    names: Set[str] = set()
    for elem in root.iter():
        if elem.text and "Fields!" in elem.text:
            for m in _FIELD_REF_RE.finditer(elem.text):
                names.add(m.group(1) or m.group(2))
    return names


def _check_field_references(root: Element) -> List[Finding]:
    declared = _declared_field_names(root)
    referenced = _referenced_field_names(root)
    missing = sorted(referenced - declared)
    if not missing:
        return []
    return [
        Finding(
            SEV_ERROR, "unknown_field_reference",
            "Expression(s) reference field(s) not declared in any DataSet: "
            + ", ".join(missing),
        )
    ]


def _check_datasetname_references(root: Element) -> List[Finding]:
    declared = _declared_dataset_names(root)
    findings: List[Finding] = []
    for tag in ("Tablix", "Chart", "List", "Matrix"):
        for item in _descendants(root, tag):
            name = _text_of(item, "DataSetName")
            if name and name not in declared:
                findings.append(
                    Finding(
                        SEV_ERROR, "unknown_dataset_reference",
                        f"{tag} '{item.get('Name') or '(unnamed)'}' references "
                        f"DataSetName '{name}', which no DataSet declares.",
                    )
                )
    return findings


def _check_units(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    seen: Set[str] = set()
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag.lower() not in _SIZE_TAGS:
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        if not _UNIT_RE.match(text):
            key = f"{tag}:{text}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    SEV_ERROR, "missing_unit_suffix",
                    f"<{tag}>{text}</{tag}> has no recognised unit suffix "
                    "(in/cm/mm/pt/px).",
                )
            )
    return findings


def _check_header_footer_placement(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    # Build a child -> parent index once, then check each PageHeader/PageFooter's
    # direct parent tag.
    parent_map = {child: parent for parent in root.iter() for child in list(parent)}
    for tag in ("PageHeader", "PageFooter"):
        for elem in _descendants(root, tag):
            parent = parent_map.get(elem)
            parent_tag = _local(parent.tag) if parent is not None else None
            if parent_tag != "Page":
                findings.append(
                    Finding(
                        SEV_ERROR, "header_footer_misplaced",
                        f"<{tag}> is a child of <{parent_tag}>, not <Page>. In RDL "
                        "2010/2016 it must be nested inside ReportSection/Page.",
                    )
                )
    return findings


def _check_body_height(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    bodies = _descendants(root, "Body")
    if not bodies:
        findings.append(Finding(SEV_ERROR, "no_body", "Report has no <Body> element."))
        return findings
    for body in bodies:
        height_text = _text_of(body, "Height")
        if not height_text:
            findings.append(
                Finding(SEV_ERROR, "body_no_height", "<Body> has no <Height> element.")
            )
            continue
        m = _UNIT_RE.match(height_text)
        if not m:
            continue  # already reported by _check_units
        value = re.match(r"^\s*([0-9]*\.?[0-9]+)", height_text)
        if value and float(value.group(1)) <= 0:
            findings.append(
                Finding(
                    SEV_ERROR, "body_zero_height",
                    f"<Body><Height>{height_text}</Height></Body> is zero or negative; "
                    "the report will render blank.",
                )
            )
    return findings


def _check_body_width(root: Element) -> List[Finding]:
    findings: List[Finding] = []
    for section in _descendants(root, "ReportSection"):
        width_text = _text_of(section, "Width")
        if not width_text:
            continue
        m = re.match(r"^\s*([0-9]*\.?[0-9]+)", width_text)
        if m and float(m.group(1)) <= 0:
            findings.append(
                Finding(
                    SEV_ERROR, "section_zero_width",
                    f"<ReportSection><Width>{width_text}</Width></ReportSection> is "
                    "zero or negative.",
                )
            )
    return findings


def validate(path: str) -> List[Finding]:
    """Validate one RDL file. Returns a list of Finding, never raises."""
    findings: List[Finding] = []
    root, parse_findings = _check_well_formed(Path(path))
    findings.extend(parse_findings)
    if root is None:
        return findings

    findings.extend(_check_root(root))
    if _local(root.tag) != "Report":
        return findings

    findings.extend(_check_namespace(root))
    findings.extend(_check_query_order(root))
    findings.extend(_check_field_references(root))
    findings.extend(_check_datasetname_references(root))
    findings.extend(_check_units(root))
    findings.extend(_check_header_footer_placement(root))
    findings.extend(_check_body_height(root))
    findings.extend(_check_body_width(root))
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate one or more RDL files for structural correctness."
    )
    parser.add_argument("files", nargs="+", help="Path(s) to .rdl file(s) to validate.")
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if any warning-severity finding exists too.",
    )
    args = parser.parse_args(argv)

    any_error = False
    any_warning = False
    total_error = 0
    total_warning = 0

    for file_arg in args.files:
        print(f"== {file_arg} ==")
        findings = validate(file_arg)
        if not findings:
            print("  (no findings)")
        for finding in findings:
            print(f"  {finding.format()}")
            if finding.severity == SEV_ERROR:
                any_error = True
                total_error += 1
            elif finding.severity == SEV_WARNING:
                any_warning = True
                total_warning += 1
        print()

    print(
        f"Summary: {len(args.files)} file(s), {total_error} error(s), "
        f"{total_warning} warning(s)."
    )

    if any_error or (args.strict and any_warning):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
