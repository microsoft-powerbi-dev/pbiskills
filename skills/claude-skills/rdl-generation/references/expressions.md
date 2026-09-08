# RDL Expressions

RDL expressions are VB.NET. They are compiled into an assembly when the report is
published, so a syntax error is a publish-time failure and a runtime error is a
`#Error` in a cell.

## Syntax basics

An expression starts with `=`. Anything that does not start with `=` is a
literal string.

```xml
<Value>Total</Value>                          <!-- literal text -->
<Value>=Fields!Amount.Value</Value>           <!-- expression -->
<Value>="Total: " &amp; Fields!Region.Value</Value>  <!-- concatenation -->
```

- `&` is string concatenation. In XML it must be escaped as `&amp;`.
- `+` also concatenates, but it coerces, so `"1" + 2` is `3` and `"a" + 2` is an
  error. Use `&`.
- Comparison is `=`, `<>`, `<`, `>`, `<=`, `>=`. In XML, `<` must be `&lt;`.
- Logic is `And`, `Or`, `Not`, `AndAlso`, `OrElse`, `Xor`. `AndAlso` and `OrElse`
  short-circuit; `And` and `Or` do not, which matters when the second operand can
  divide by zero.
- `Nothing` is the null literal. Test with `IsNothing(x)`, not `x = Nothing`.
- There is no `if` statement, only the `IIf`, `Switch` and `Choose` functions.
  For anything longer, use a `Code` block.

## Fields

```text
Fields!Amount.Value        the value for the current row / scope
Fields!Amount.IsMissing    True when the dataset did not return this column
Fields!Amount.Count        number of rows in scope (not the number of columns)
Fields!Amount.Value(0)     first value, in a multi-value context
```

Bracketed form for names that are not bare identifiers:

```text
Fields![Sales Amount].Value
```

Both forms are read by this repository's parser. `Fields!X.Value` outside a data
region (in a page header, say) is a validation error because there is no scope.

`IsMissing` guards an optional column:

```xml
<Value>=IIf(Fields!Discount.IsMissing, 0, Fields!Discount.Value)</Value>
```

## Parameters

```text
Parameters!Year.Value              scalar value
Parameters!Year.Label              the display label from ValidValues
Parameters!Region.Value(0)         first selected value of a multi-value param
Parameters!Region.Label(0)         its label
Parameters!Region.Count            number of selected values
Join(Parameters!Region.Value, ", ")   all selected values as one string
```

A multi-value parameter is an array. Rendering it directly gives `#Error`:

```xml
<!-- wrong: array in a textbox -->
<Value>=Parameters!Region.Value</Value>
<!-- right -->
<Value>=Join(Parameters!Region.Value, ", ")</Value>
<!-- right: labels, not codes -->
<Value>=Join(Parameters!Region.Label, ", ")</Value>
```

`Label` falls back to `Value` when the parameter has no `ValidValues`, so it is
safe to prefer `Label` for display.

## Globals

| Reference | Type | Notes |
| --- | --- | --- |
| `Globals!PageNumber` | Integer | page headers and footers only |
| `Globals!TotalPages` | Integer | page headers and footers only |
| `Globals!OverallPageNumber` | Integer | ignores per-group page-number resets |
| `Globals!OverallTotalPages` | Integer | as above |
| `Globals!ExecutionTime` | DateTime | when the report started rendering |
| `Globals!ReportName` | String | catalogue name, not the file name |
| `Globals!ReportServerUrl` | String | base URL of the server |
| `Globals!ReportFolder` | String | server folder path |
| `Globals!PageName` | String | current `PageName`, for Excel tab labels |
| `Globals!RenderFormat.Name` | String | `PDF`, `EXCELOPENXML`, `HTML4.0`, ... |
| `Globals!RenderFormat.IsInteractive` | Boolean | true for HTML |

```xml
<!-- standard footer -->
<Value>="Page " &amp; Globals!PageNumber &amp; " of " &amp; Globals!TotalPages</Value>
<Value>=Globals!ExecutionTime</Value>

<!-- hide a section when exporting to Excel -->
<Visibility><Hidden>=Globals!RenderFormat.Name = "EXCELOPENXML"</Hidden></Visibility>
```

`PageNumber` and `TotalPages` are only valid in a page header or footer. Using
them in the body is a publish-time error.

## User

| Reference | Notes |
| --- | --- |
| `User!UserID` | `DOMAIN\user` on Windows auth, the effective identity otherwise |
| `User!Language` | the client's culture, e.g. `en-GB` |

`User!UserID` is the standard hook for row-level security in the query:

```xml
<QueryParameter Name="@User">
  <Value>=User!UserID</Value>
</QueryParameter>
```

A report that uses `User!UserID` cannot be cached or snapshotted, because the
output depends on the caller.

## ReportItems

`ReportItems!TextboxName.Value` reads another textbox's rendered value. It is the
only way to reference a body value from a page header or footer.

```xml
<!-- in the page header, showing the first customer on the page -->
<Value>=ReportItems!CustomerName.Value</Value>
```

Rules.

- The name is the textbox `Name` attribute, and it must be unique in the report.
- In a page header or footer the reference resolves to the first (or last)
  instance on that page.
- In the body it only resolves inside the same containing scope, which makes it
  fragile. Prefer recomputing the expression.
- `ReportItems!` cannot be aggregated: `Sum(ReportItems!X.Value)` is invalid.

## Aggregate functions

| Function | Returns | Ignores nulls |
| --- | --- | --- |
| `Sum(expr, scope)` | numeric total | yes |
| `Avg(expr, scope)` | mean | yes |
| `Min(expr, scope)` / `Max(expr, scope)` | extremes, works on dates and strings | yes |
| `Count(expr, scope)` | number of non-null values | yes |
| `CountDistinct(expr, scope)` | number of distinct non-null values | yes |
| `CountRows(scope)` | number of rows, including all-null rows | n/a |
| `First(expr, scope)` / `Last(expr, scope)` | first / last value in scope order | no |
| `StDev(expr, scope)` / `StDevP(expr, scope)` | sample / population standard deviation | yes |
| `Var(expr, scope)` / `VarP(expr, scope)` | sample / population variance | yes |
| `Aggregate(expr, scope)` | server-side aggregate, OLAP sources only | n/a |

### Scope

The second argument names the scope to aggregate over. Omit it and the aggregate
uses the innermost enclosing scope, which is what you want most of the time.

```xml
<!-- inside a region group: total for this region -->
<Value>=Sum(Fields!Amount.Value)</Value>

<!-- explicit group scope -->
<Value>=Sum(Fields!Amount.Value, "RegionGroup")</Value>

<!-- whole dataset, regardless of position -->
<Value>=Sum(Fields!Amount.Value, "SalesData")</Value>

<!-- percentage of the grand total -->
<Value>=Sum(Fields!Amount.Value) / Sum(Fields!Amount.Value, "SalesData")</Value>
```

A scope name is a dataset name, a `Group/@Name`, or a data-region `Name`. Scope
names are case sensitive and must exist, or you get "The Value expression for the
textbox 'X' has a scope parameter that is not valid for an aggregate function.
The scope parameter must be set to a string constant that is equal to either the
name of a containing group, the name of a containing data region, or the name of
a dataset."

Note that the scope must be a string *literal*. An expression there does not
compile.

### RunningValue

```text
RunningValue(expression, aggregate_function, scope)
```

```xml
<!-- cumulative total within the region group -->
<Value>=RunningValue(Fields!Amount.Value, Sum, "RegionGroup")</Value>

<!-- row number within the dataset -->
<Value>=RunningValue(Fields!Amount.Value, Count, "SalesData")</Value>
```

The second argument is the bare function name, not a string and not a call.

### Previous

```text
Previous(expression, scope)
```

```xml
<!-- change versus the previous row -->
<Value>=Fields!Amount.Value - Previous(Fields!Amount.Value)</Value>

<!-- change versus the previous group -->
<Value>=Sum(Fields!Amount.Value) - Previous(Sum(Fields!Amount.Value), "RegionGroup")</Value>
```

`Previous` returns `Nothing` on the first instance, so guard the division:

```xml
<Value>=IIf(IsNothing(Previous(Sum(Fields!Amount.Value))), Nothing,
  (Sum(Fields!Amount.Value) - Previous(Sum(Fields!Amount.Value)))
  / Previous(Sum(Fields!Amount.Value)))</Value>
```

## Lookup functions

Introduced in RDL 2010 (SQL Server 2008 R2). They join two datasets in the
report, without a database join.

```text
Lookup(source_expression, destination_expression, result_expression, dataset)
LookupSet(source_expression, destination_expression, result_expression, dataset)
MultiLookup(source_expression_array, destination_expression, result_expression, dataset)
```

```xml
<!-- one-to-one: pull the product name for the current row's ProductId -->
<Value>=Lookup(Fields!ProductId.Value, Fields!Id.Value, Fields!Name.Value, "ProductLookup")</Value>

<!-- one-to-many: every order id for the current customer, joined into a string -->
<Value>=Join(LookupSet(Fields!CustomerId.Value, Fields!CustomerId.Value,
  Fields!OrderId.Value, "Orders"), ", ")</Value>

<!-- array in, array out -->
<Value>=Join(MultiLookup(Split(Fields!ProductIds.Value, ","), Fields!Id.Value,
  Fields!Name.Value, "ProductLookup"), ", ")</Value>
```

Constraints.

- The destination dataset name is a string literal.
- `Lookup` returns `Nothing` when there is no match.
- `LookupSet` returns an array, so it always needs `Join` or an aggregate to
  render.
- Lookups run per row and are not indexed. On large datasets they are the usual
  cause of a report that renders in minutes rather than seconds. Prefer a join
  in the query.
- Lookups cannot be nested inside an aggregate: `Sum(LookupSet(...))` is invalid.
  Use `Sum(LookupSet(...))` replaced by a custom-code loop, or fix the query.

## Conditional functions

### IIf

```text
IIf(condition, true_value, false_value)
```

`IIf` is a *function*, so both branches are evaluated before the choice is made.
This is the source of the classic divide-by-zero:

```xml
<!-- still errors when Units is 0: the false branch is evaluated anyway -->
<Value>=IIf(Fields!Units.Value = 0, 0, Fields!Amount.Value / Fields!Units.Value)</Value>

<!-- safe: make the denominator harmless -->
<Value>=IIf(Fields!Units.Value = 0, 0,
  Fields!Amount.Value / IIf(Fields!Units.Value = 0, 1, Fields!Units.Value))</Value>
```

### Switch

Evaluates pairs left to right and returns the value of the first true condition.
Provide a final `True` catch-all or an unmatched row returns `Nothing`.

```xml
<Value>=Switch(
  Fields!Amount.Value &gt;= 100000, "Platinum",
  Fields!Amount.Value &gt;= 50000,  "Gold",
  Fields!Amount.Value &gt;= 10000,  "Silver",
  True, "Standard")</Value>
```

`Switch` is preferred over three or more nested `IIf` calls. This repository
flags deep nesting: `compatibility._detect_review_findings` matches
`IIF\s*\([^)]*IIF\s*\([^)]*IIF\s*\(` and emits `nested_iif` at severity `warn`.

### Choose

Selects by 1-based index.

```xml
<Value>=Choose(Fields!Quarter.Value, "Q1", "Q2", "Q3", "Q4")</Value>
```

Returns `Nothing` when the index is out of range.

## String, date and math functions

String:

```text
Len(s)            Left(s, n)       Right(s, n)      Mid(s, start, len)
Trim(s)           LTrim(s)         RTrim(s)
UCase(s)          LCase(s)         StrConv(s, VbStrConv.ProperCase)
Replace(s, find, repl)             InStr(s, find)   InStrRev(s, find)
Split(s, delim)   Join(array, delim)                Format(value, fmt)
String(n, ch)     Space(n)         StrDup(n, s)
```

Chained `Replace` calls are flagged by this repository as `chained_replace`
(severity `info`) once there are three or more in one expression.

Date:

```text
Today()                     Now()
Year(d)  Month(d)  Day(d)   Hour(d)  Minute(d)  Second(d)
Weekday(d, firstday)        MonthName(n)  WeekdayName(n)
DateAdd("d", -30, Today())  DateDiff("d", a, b)   DatePart("q", d)
DateSerial(y, m, d)         TimeSerial(h, m, s)   CDate(s)
FormatDateTime(d, DateFormat.ShortDate)
```

`DateAdd` and `DateDiff` interval codes: `yyyy`, `q`, `m`, `y` (day of year),
`d`, `w` (weekday), `ww` (week), `h`, `n` (minute), `s`.

First and last day of the current month:

```xml
<Value>=DateSerial(Year(Today()), Month(Today()), 1)</Value>
<Value>=DateAdd("d", -1, DateAdd("m", 1, DateSerial(Year(Today()), Month(Today()), 1)))</Value>
```

Math and conversion:

```text
Abs(n)  Sign(n)  Sqrt(n)  Round(n, digits)  Ceiling(n)  Floor(n)  Int(n)  Fix(n)
Exp(n)  Log(n)   Log10(n) Power via ^        Rnd()
CInt(x) CDbl(x)  CDec(x)  CStr(x)  CBool(x)  CDate(x)
IsNothing(x)  IsNumeric(x)  IsDate(x)  IsError(x)
```

`Round` uses banker's rounding (2.5 rounds to 2). For the arithmetic
convention use `Math.Round(n, digits, MidpointRounding.AwayFromZero)`.

## Format strings

`Format` is a `Style` element, and it is the right place for number and date
formatting. Formatting in the expression with `Format()` produces a string, which
then sorts and exports as text.

```xml
<TextRun>
  <Value>=Sum(Fields!Amount.Value)</Value>
  <Style><Format>#,##0.00;(#,##0.00)</Format></Style>
</TextRun>
```

Standard numeric formats (the digit is the precision):

| Format | Meaning | 1234.5678 renders as |
| --- | --- | --- |
| `C2` | currency, 2 decimals | `$1,234.57` |
| `C0` | currency, no decimals | `$1,235` |
| `N0` | number with separators, no decimals | `1,235` |
| `N2` | number with separators, 2 decimals | `1,234.57` |
| `F2` | fixed point, no separators | `1234.57` |
| `P1` | percent, 1 decimal (multiplies by 100) | `123456.8 %` |
| `E2` | scientific | `1.23E+003` |
| `D5` | integer padded to 5 digits | `01235` (integers only) |
| `X` | hexadecimal | `4D2` (integers only) |

Standard date formats:

| Format | Meaning | Example (en-US) |
| --- | --- | --- |
| `d` | short date | `8/24/2026` |
| `D` | long date | `Monday, August 24, 2026` |
| `t` | short time | `2:05 PM` |
| `T` | long time | `2:05:33 PM` |
| `g` | short date and short time | `8/24/2026 2:05 PM` |
| `G` | short date and long time | `8/24/2026 2:05:33 PM` |
| `M` | month and day | `August 24` |
| `Y` | month and year | `August 2026` |
| `s` | sortable ISO | `2026-08-24T14:05:33` |

Custom patterns:

| Pattern | Renders |
| --- | --- |
| `yyyy-MM-dd` | `2026-08-24` |
| `dd/MM/yyyy HH:mm` | `24/08/2026 14:05` |
| `MMM yyyy` | `Aug 2026` |
| `#,##0` | `1,235` |
| `#,##0.00` | `1,234.57` |
| `#,##0.00;(#,##0.00)` | negatives in parentheses |
| `#,##0.00;(#,##0.00);"-"` | positive; negative; zero |
| `0.0%` | `123456.8%` |
| `$#,##0.00_);[Red]($#,##0.00)` | Excel-style accounting with red negatives |

Note the case rules: `MM` is month, `mm` is minute, `HH` is 24-hour, `hh` is
12-hour, `dd` is day, `DD` is not valid.

Format strings are culture-sensitive. `C2` renders `$` or `£` depending on
`Report/Language`. To pin the currency regardless of the viewer, set
`<Language>en-GB</Language>` on the report or use an explicit literal in the
pattern.

## Conditional formatting patterns

Alternating row shading, in `Style/BackgroundColor`:

```xml
<Style>
  <BackgroundColor>=IIf(RowNumber(Nothing) Mod 2 = 0, "WhiteSmoke", "White")</BackgroundColor>
</Style>
```

Red negatives:

```xml
<Style>
  <Color>=IIf(Sum(Fields!Margin.Value) &lt; 0, "Firebrick", "Black")</Color>
</Style>
```

Traffic lights with `Switch`:

```xml
<Style>
  <BackgroundColor>=Switch(
    Fields!Score.Value &gt;= 90, "#2E7D32",
    Fields!Score.Value &gt;= 70, "#F9A825",
    True, "#C62828")</BackgroundColor>
  <Color>White</Color>
</Style>
```

Hide a row when a value is zero:

```xml
<Visibility>
  <Hidden>=Sum(Fields!Amount.Value) = 0</Hidden>
</Visibility>
```

Note `Hidden` is inverted relative to intuition: `true` hides.

Drill-down toggle:

```xml
<Visibility>
  <Hidden>true</Hidden>
  <ToggleItem>RegionHeader</ToggleItem>
</Visibility>
```

## Custom Code blocks

`Report/Code` holds VB.NET member declarations. Functions defined there are
called through the `Code.` prefix.

```xml
<Code>
  Public Function Safe(ByVal numerator As Object, ByVal denominator As Object) As Object
    If IsNothing(denominator) OrElse Convert.ToDouble(denominator) = 0 Then
      Return Nothing
    End If
    Return Convert.ToDouble(numerator) / Convert.ToDouble(denominator)
  End Function

  Public Shared Total As Double = 0

  Public Function Accumulate(ByVal amount As Double) As Double
    Total = Total + amount
    Return Total
  End Function
</Code>
```

Called from an expression:

```xml
<Value>=Code.Safe(Sum(Fields!Amount.Value), Sum(Fields!Units.Value))</Value>
```

Rules and constraints.

- The block is VB.NET only. Method bodies only, no `Imports`, no `Class`.
- The block is XML text, so `<` and `&` must be escaped, or wrapped in
  `<![CDATA[ ... ]]>`.
- One instance per report execution. Module-level state accumulates across every
  row in render order, which is not the same as data order.
- `Code` members cannot be used in a `GroupExpression` that also feeds a
  `SortExpression`, because evaluation order is not guaranteed.
- Power BI paginated reports do not support custom code. This repository flags
  it: `compatibility.detect` emits `custom_code` at severity `warn` for any
  `Code` or `CodeModules` element.
- External assemblies via `CodeModules` and `Classes` require deployment to the
  report server's bin folder and a `rssrvpolicy.config` grant. Not supported in
  Power BI paginated at all.

## Round-trip safe forms

This section is the reason the skill exists. This repository parses RDL back into
an IR, and its parser recognises exactly two expression shapes. Emit anything
else and the generate then parse round trip loses the binding.

The two regular expressions in `backend/app/core/parser/rdl_parser.py`:

```python
_FIELD_NAME = r"(?:\[([^\]]+)\]|([A-Za-z_]\w*))"
_FIELD_RE = re.compile(rf"Fields!\s*{_FIELD_NAME}\s*\.Value", re.IGNORECASE)
_AGG_RE = re.compile(
    rf"\b(Sum|Avg|Min|Max|CountDistinct|Count)\s*\(\s*Fields!\s*{_FIELD_NAME}\s*\.Value",
    re.IGNORECASE,
)
```

What that means concretely.

- **Field reference.** `=Fields!Name.Value` and `=Fields![Sales Amount].Value`
  are both read. Whitespace after `Fields!` and before `.Value` is tolerated. The
  match is case insensitive.
- **Aggregate.** Exactly `Sum`, `Avg`, `Min`, `Max`, `Count` and `CountDistinct`
  wrapped directly around a field reference:
  `=Sum(Fields!Amount.Value)`. Whitespace between the function name, the
  parenthesis and `Fields!` is tolerated.
- **Where the parser looks.** `_group_field_names` scans every
  `GroupExpression` element for `_FIELD_RE`. `_aggregate_shelf_fields` scans
  every element whose local tag name is `Value`, `Y` or `X` for `_AGG_RE`.
  `validate._referenced_field_names` scans the text of *any* element containing
  the substring `Fields!`.

Emit these forms:

```xml
<GroupExpression>=Fields!Region.Value</GroupExpression>
<Value>=Fields!Region.Value</Value>
<Value>=Sum(Fields!Amount.Value)</Value>
<Y>=Sum(Fields!Amount.Value)</Y>
<Label>=Fields!Region.Value</Label>
```

Do not emit these if you want the round trip to survive:

| Form | Why it does not round-trip |
| --- | --- |
| `=CountRows()` or `=CountRows("SalesData")` | no `Fields!` inside, so `_AGG_RE` finds nothing |
| `=First(Fields!Region.Value)` | `First`, `Last`, `StDev`, `Var` are not in `_AGG_RE` |
| `=Sum(IIf(Fields!Ok.Value, Fields!Amount.Value, 0))` | `Fields!` is not the first token inside the parenthesis |
| `=Sum(Fields!Amount.Value * Fields!Rate.Value)` | matches the field, but the multiplication is lost |
| `=Code.Total(Fields!Amount.Value)` | invisible to both patterns |
| `=ReportItems!Total.Value` | no `Fields!` reference at all |
| `=Fields("Region").Value` | not the `Fields!` syntax; valid VB, unmatched by the regex |

Two further rules from the generator side that keep round trips clean.

- The `Field/@Name` and the token inside `Fields!...` must be the same
  identifier. `rdl_generator._rdl_field_name` sanitises both through the same
  function, so `Total Sales` becomes `Total_Sales` in the `Name` attribute and in
  every expression, while `DataField` keeps `Total Sales`.
- An aggregate must never appear inside a `Field/Value` calculated field. SSRS
  rejects it, and the parser would classify it as a calculated field rather than
  a measure. Put the aggregate at the point of use.

If you need an expression more complex than the canonical forms, put the
complexity in the SQL and expose the result as a plain column. That keeps the
report simple, the round trip lossless, and the work on the database.

## Common causes of #Error

| Symptom | Cause |
| --- | --- |
| Every cell in a column shows `#Error` | The field name in the expression does not match a `Field/@Name`. Field names are case sensitive in expressions. |
| One cell shows `#Error`, the rest are fine | Divide by zero, or a null reaching an arithmetic operator. Guard with `IIf` plus a safe denominator. |
| `#Error` after adding a parameter | A multi-value parameter rendered directly. Use `Join(...)`. |
| `#Error` only on the first row of a group | `Previous(...)` returned `Nothing`. Guard with `IsNothing`. |
| `#Error` in a total row | Aggregate scope names a group that does not enclose the cell. |
| `#Error` in a page header | `Fields!` used outside a data region, or `Globals!PageNumber` used in the body. |
| `#Error` on a date expression | A string reached a date function. Wrap with `CDate`, or fix `rd:TypeName`. |
| `#Error` on concatenation | `+` used with mixed types. Use `&`. |
| Publish fails with a compile error | VB syntax error in an expression or in the `Code` block; the message names the textbox. |
| `#Error` only when exporting to PDF | A `Globals!RenderFormat` branch that does not handle the non-interactive case. |
