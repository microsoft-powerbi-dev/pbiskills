# Namespaces and Schema Versions

RDL identifies its schema version by the XML namespace on the `Report` root.
There is no version attribute. Changing the namespace changes which elements are
legal and which tools will open the file.

## The namespace URLs

| Schema version | Default namespace URI | Introduced with |
| --- | --- | --- |
| RDL 2003/10 | `http://schemas.microsoft.com/sqlserver/reporting/2003/10/reportdefinition` | SQL Server 2000 Reporting Services |
| RDL 2005/01 | `http://schemas.microsoft.com/sqlserver/reporting/2005/01/reportdefinition` | SQL Server 2005 |
| RDL 2008/01 | `http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition` | SQL Server 2008 |
| RDL 2010/01 | `http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition` | SQL Server 2008 R2 |
| RDL 2016/01 | `http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition` | SQL Server 2016 |

Companion namespaces that appear in the same documents:

| Prefix | URI | Purpose |
| --- | --- | --- |
| `rd` | `http://schemas.microsoft.com/SQLServer/reporting/reportdesigner` | designer extension elements: `rd:TypeName`, `rd:DataSourceID`, `rd:SecurityType`, `rd:DefaultName`, `rd:UseGenericDesigner` |
| `cl` | `http://schemas.microsoft.com/sqlserver/reporting/2010/01/componentdefinition` | report parts and component definitions |
| (shared dataset) | `http://schemas.microsoft.com/sqlserver/reporting/2010/01/shareddatasetdefinition` | the root namespace of a `.rsd` shared dataset file, not of an `.rdl` |

Note the capitalisation of the `rd` URI: `SQLServer` with a capital S and a
capital S, unlike the lower-case `sqlserver` in the report-definition URIs. This
repository hard-codes both:

```python
_NS = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
_RD = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"
```

The `rd` namespace is version-neutral: the same URI is used from 2005 through
2016. Its elements are designer metadata. The server ignores them, so they can be
omitted entirely, but `rd:TypeName` is worth emitting because it drives sorting
and formatting defaults in Report Builder.

### RDLX and Power BI paginated

`.rdlx` was the file extension for Power View report definitions in SharePoint
integrated mode. It is a different, non-interchangeable format and is not
relevant to paginated report generation.

Power BI paginated reports (in the Power BI service and in Microsoft Fabric) use
plain RDL with the 2016/01 namespace. The file extension stays `.rdl`. What
differs is the supported feature set, not the schema, which is why this
repository's paginated path is a re-point and validate transform rather than a
rebuild. `backend/app/core/paginated/transformer.py` opens with exactly that
observation: "Because Power BI paginated reports use the RDL schema, conversion
preserves the original document and mutates it in place via ElementTree rather
than round-tripping through an IR (which would be lossy)."

## Which tool accepts which version

| Tool or server | Reads | Writes |
| --- | --- | --- |
| SSRS 2005 | 2003/10, 2005/01 | 2005/01 |
| SSRS 2008 | 2003/10 through 2008/01 (upgrades on publish) | 2008/01 |
| SSRS 2008 R2 | up to 2010/01 | 2010/01 |
| SSRS 2012, 2014 | up to 2010/01 | 2010/01 |
| SSRS 2016, 2017, 2019, 2022 | up to 2016/01, upgrades older on publish | 2016/01 |
| Power BI Report Server | 2016/01, upgrades older on publish | 2016/01 |
| Power BI service, paginated | 2016/01, upgrades 2008/2010 on upload | 2016/01 |
| Microsoft Fabric, paginated | 2016/01 | 2016/01 |
| Report Builder 3.0 | up to 2010/01 | 2010/01 |
| Report Builder (2016 and later, current) | up to 2016/01 | 2016/01 |
| Visual Studio / SSDT report projects | matches the project's TargetServerVersion | same |

The direction is one-way. An older server will refuse a newer namespace with
"The report definition is not valid. Details: The report definition has an
invalid target namespace 'http://...2016/01/reportdefinition' which cannot be
upgraded." A newer server silently upgrades an older document in memory when it
publishes it, but the stored definition keeps the original namespace unless you
save it back from the designer.

### Target-version decision table

| Situation | Target |
| --- | --- |
| Power BI paginated, Fabric, or Power BI Report Server | 2016/01 |
| SSRS 2016 or later, on premises | 2016/01 |
| SSRS 2012 or 2014 estate you cannot upgrade | 2010/01 |
| Report must open in Report Builder 3.0 | 2010/01 |
| Unknown estate, mixed servers, maximum reach | 2008/01 |
| You are editing an existing file | keep its current namespace |

Default to 2016/01 unless something forces you lower. It is what this repository
generates, and what every currently supported Microsoft target accepts.

## What is 2016-only versus 2008-safe

The differences that actually matter are structural, not feature flags.

### RDL 2005 and earlier

- Separate `<Table>`, `<Matrix>` and `<List>` data regions. No `<Tablix>`.
- No `<Page>` element. `PageWidth`, `PageHeight` and the four margins are direct
  children of `<Report>`.
- `<PageHeader>` and `<PageFooter>` are direct children of `<Report>`.
- `ReportParameter` has a distinct `<Internal>` element alongside `<Hidden>`.

### RDL 2008/01

- Introduces `<Tablix>`, which subsumes table, matrix and list. `Table`, `Matrix`
  and `List` are still legal for backward compatibility.
- Introduces `<Page>`, which now contains `PageHeader`, `PageFooter`,
  `PageHeight`, `PageWidth`, the margins, `Columns` and `ColumnSpacing`.
- Introduces the current `<Chart>` and `<Gauge>` elements.
- Introduces rich text in a textbox: `Paragraphs / Paragraph / TextRuns / TextRun`
  replaces the old single `<Value>` on the textbox.
- Drops `<Internal>` on `ReportParameter`; internal is expressed as
  `<Hidden>true</Hidden>` with no `<Prompt>`.
- `Body`, `Width` and `Page` are direct children of `<Report>`.

### RDL 2010/01

- Introduces `<ReportSections>` wrapping one or more `<ReportSection>`, each with
  its own `Body`, `Width` and `Page`. This is the big structural break.
- Introduces `Lookup`, `LookupSet` and `MultiLookup` expression functions.
- Introduces shared datasets: `<SharedDataSet><SharedDataSetReference>`.
- Introduces `<Map>` and report parts.
- Introduces `Group/DomainScope` and `Group/PageName`.

### RDL 2016/01

- Structurally a superset of 2010/01 with no changes to `ReportSections`,
  `Tablix` or `Chart` shape. A 2010 document is almost always a valid 2016
  document with only the namespace URI changed.
- It is the namespace SSRS 2016 and later write, and the only namespace Power BI
  paginated targets. That, rather than any single new element, is the reason to
  use it.

Practical takeaway: the "2016-only" thing you must get right is
`ReportSections/ReportSection`, and that came in with 2010. Everything you write
inside a section is 2008-safe. So a generator that emits 2016 and a generator
that emits 2008 differ by exactly three moved elements.

## Reading namespace-agnostically

Never hard-code a namespace when parsing. A production estate contains 2005,
2008, 2010 and 2016 documents side by side. Navigate by *local* tag name instead.

The helpers in `backend/app/core/parser/rdl_xml.py` are the pattern. Their
docstring states the rationale: "RDL files are namespaced against
version-specific schema URLs. To stay version-tolerant we navigate by *local* tag
name and ignore namespaces."

```python
def local_name(tag: str) -> str:
    """Return the local part of a possibly-namespaced ElementTree tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def child(elem: Optional[Element], name: str) -> Optional[Element]:
    """First *direct* child with the given local name (case-insensitive)."""
    if elem is None:
        return None
    lname = name.lower()
    for c in list(elem):
        if local_name(c.tag).lower() == lname:
            return c
    return None


def children(elem: Optional[Element], name: str) -> List[Element]:
    """All direct children with the given local name."""
    if elem is None:
        return []
    lname = name.lower()
    return [c for c in list(elem) if local_name(c.tag).lower() == lname]


def descendants(elem: Optional[Element], name: str) -> List[Element]:
    """All descendants (any depth) with the given local name."""
    if elem is None:
        return []
    lname = name.lower()
    return [d for d in elem.iter() if local_name(d.tag).lower() == lname]


def text_of(elem: Optional[Element], name: str) -> Optional[str]:
    """Text of the first direct child with the given local name."""
    c = child(elem, name)
    if c is not None and c.text is not None:
        return c.text.strip()
    return None
```

Four properties of these helpers worth relying on.

- All four accept `None` and return `None` or `[]`, so
  `text_of(child(dataset, "Query"), "CommandText")` is safe even when there is no
  `Query`.
- Matching is case-insensitive, which absorbs the occasional third-party
  generator that writes `DataSetName` as `Datasetname`.
- `child` and `children` look only at *direct* children, so
  `child(report, "DataSources")` will not accidentally reach into a subreport.
- `descendants` uses `elem.iter()`, which includes `elem` itself. Calling
  `descendants(tablix, "Tablix")` returns the tablix.

The same idea is what detects the namespace when you do need it:

```python
def _namespace_of(root: ET.Element) -> str:
    tag = root.tag
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
```

And the recognised set, newest first, from
`backend/app/core/paginated/validate.py`:

```python
_KNOWN_RDL_NAMESPACES = (
    "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition",
)
```

Anything outside that set yields the `unknown_namespace` finding at severity
`warn`.

## Writing namespace-preservingly

`xml.etree.ElementTree` does not remember the prefixes it read. Serialise a
parsed tree without preparation and you get `ns0:Report`, `ns0:DataSources` and a
document no RDL tool will open. There are two correct patterns depending on
whether you are editing an existing file or creating a new one.

### Editing an existing file: harvest and re-register

`backend/app/core/paginated/transformer.py` harvests the prefix map with a
`start-ns` pass before parsing, then re-registers every prefix before writing:

```python
def _detect_namespaces(path: str) -> Dict[str, str]:
    """Collect xmlns prefix -> uri so they can be re-registered before write."""
    nsmap: Dict[str, str] = {}
    try:
        for event, (prefix, uri) in ET.iterparse(sanitize_rdl_bytes(path), events=["start-ns"]):
            nsmap.setdefault(prefix, uri)
    except (ParseError, OSError):
        pass
    return nsmap
```

and in `transform_paginated`:

```python
    nsmap = _detect_namespaces(rdl_path)
    report.namespace = nsmap.get("", "")
    for prefix, uri in nsmap.items():
        ET.register_namespace(prefix, uri)

    try:
        tree = ET.parse(sanitize_rdl_bytes(rdl_path))
    ...
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
```

Points to note.

- The default namespace comes back as the prefix `""`, so `nsmap[""]` is the
  RDL version of the source document. That is how the transform reports which
  schema it preserved.
- `setdefault` keeps the *first* binding for each prefix, which is the root
  declaration, and ignores any re-binding deeper in the document.
- `ET.register_namespace` is process-global state. In a long-running service,
  register before every write rather than once at import.
- Both the harvest and the parse read through `sanitize_rdl_bytes`, so a padded
  server download does not break either pass.

The result is an output file whose namespace declarations and prefixes match the
input byte for byte, whatever version the input was.

### Creating a new file: register at import

`backend/app/core/generator/rdl_generator.py` registers once, at module level,
then builds every element with a fully-qualified tag:

```python
_NS = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
_RD = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"
register_namespace("", _NS)
register_namespace("rd", _RD)


def _el(parent: Element, tag: str, text: Optional[str] = None) -> Element:
    e = SubElement(parent, f"{{{_NS}}}{tag}")
    if text is not None:
        e.text = text
    return e


def _rd_el(parent: Element, tag: str, text: Optional[str] = None) -> Element:
    """An element in the ReportDesigner namespace (rd:SecurityType, rd:DataSourceID)."""
    e = SubElement(parent, f"{{{_RD}}}{tag}")
    if text is not None:
        e.text = text
    return e
```

The root is created the same way, then written with a declaration:

```python
    report = Element(f"{{{_NS}}}Report")
    ...
    ElementTree(report).write(str(out_path), encoding="utf-8", xml_declaration=True)
```

Register `""` to `_NS` and the serialiser emits `xmlns="..."` on the root with no
prefix on any element. Forget it and every tag comes out as `ns0:`.

If you are not using ElementTree, the equivalent requirements are: declare the
RDL namespace as the default on `Report`, declare `rd` only if you use it, and do
not put a prefix on RDL elements.

## The trailing null-byte problem

SSRS Report Server stores report definitions in a fixed-size column and pads the
tail with `\x00`. A definition downloaded through the SOAP `GetItemDefinition`
call, through `rs.exe`, or through some PowerShell wrappers comes back padded.
`xml.etree.ElementTree` then fails with:

```text
xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 1, column 0
```

or with a column number one past the end of the real document. The XML is fine;
the trailing bytes are not.

The fix in `backend/app/core/parser/rdl_xml.py`:

```python
def sanitize_rdl_bytes(source: Union[str, Path]) -> io.BytesIO:
    """Read an RDL file and strip trailing null bytes that SSRS Report Server pads."""
    raw = Path(source).read_bytes()
    return io.BytesIO(raw.rstrip(b"\x00"))
```

Every entry point in this repository that reads an RDL goes through it:
`rdl_parser.parse_rdl`, `paginated.transformer._detect_namespaces`,
`paginated.transformer.transform_paginated`, and `paginated.validate.validate_rdl`.

Two related gotchas from the same source.

- The download may also carry a UTF-8 byte order mark. `ET.parse` handles a BOM
  on a byte stream, but a `str` read with `read_text()` keeps the BOM as a
  leading `U+FEFF` character and then fails with "XML or text declaration not at
  start of entity". Read bytes, not text.
- Some downloads are UTF-16 with a declaration that says `utf-8`. Trust the BOM
  over the declaration, or re-encode before parsing.

## Upgrading a 2008 RDL to 2016

The edits are mechanical. From this:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition"
        xmlns:rd="http://schemas.microsoft.com/SQLServer/reporting/reportdesigner">
  <DataSources>...</DataSources>
  <DataSets>...</DataSets>
  <Body>
    <ReportItems>...</ReportItems>
    <Height>6in</Height>
  </Body>
  <ReportParameters>...</ReportParameters>
  <Width>6.5in</Width>
  <Page>
    <PageHeader>...</PageHeader>
    <PageFooter>...</PageFooter>
    <PageHeight>11in</PageHeight>
    <PageWidth>8.5in</PageWidth>
    <LeftMargin>1in</LeftMargin>
    <RightMargin>1in</RightMargin>
  </Page>
</Report>
```

to this:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
        xmlns:rd="http://schemas.microsoft.com/SQLServer/reporting/reportdesigner">
  <DataSources>...</DataSources>
  <DataSets>...</DataSets>
  <ReportSections>
    <ReportSection>
      <Body>
        <ReportItems>...</ReportItems>
        <Height>6in</Height>
      </Body>
      <Width>6.5in</Width>
      <Page>
        <PageHeader>...</PageHeader>
        <PageFooter>...</PageFooter>
        <PageHeight>11in</PageHeight>
        <PageWidth>8.5in</PageWidth>
        <LeftMargin>1in</LeftMargin>
        <RightMargin>1in</RightMargin>
      </Page>
    </ReportSection>
  </ReportSections>
  <ReportParameters>...</ReportParameters>
</Report>
```

The exact structural edits, in order:

1. Change the default namespace on `Report` from `.../2008/01/reportdefinition`
   to `.../2016/01/reportdefinition`. Leave the `rd` namespace alone; it is
   unchanged across versions.
2. Insert `<ReportSections><ReportSection>` as a child of `Report`, positioned
   after `DataSets`.
3. Move `<Body>` into the `ReportSection`, unchanged.
4. Move `<Width>` into the `ReportSection`, after `Body`.
5. Move `<Page>` into the `ReportSection`, after `Width`.
6. Leave `PageHeader` and `PageFooter` where they are, inside `Page`. They were
   already there in 2008.
7. Leave `DataSources`, `DataSets`, `ReportParameters`, `Code`,
   `EmbeddedImages`, `Language`, `Variables` as direct children of `Report`.

Nothing inside `Body` changes. No Tablix, Chart, Textbox, expression, style or
measurement needs editing.

Upgrading from 2005 requires all of the above plus two content rewrites, which
are not mechanical:

- Convert every `<Table>`, `<Matrix>` and `<List>` into a `<Tablix>` with the
  equivalent hierarchies. Report Designer does this automatically on open; doing
  it by hand is substantial work.
- Convert `<Textbox><Value>` into
  `<Textbox><Paragraphs><Paragraph><TextRuns><TextRun><Value>`.
- Convert `<Report><PageWidth>` and friends into a `<Page>` block.
- Replace `<Internal>true</Internal>` on parameters with `<Hidden>true</Hidden>`
  and remove the `<Prompt>`.

Downgrading from 2016 to 2008 reverses steps 1 through 5, but only succeeds if
the document uses a single `ReportSection` and no 2010-or-later feature (`Map`,
`Lookup`, `LookupSet`, `MultiLookup`, shared datasets, `DomainScope`,
`PageName`). Check for those before attempting it.
