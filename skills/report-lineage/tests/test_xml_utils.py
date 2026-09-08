"""Tests for reportlineage.xml_utils."""
from __future__ import annotations

from xml.etree.ElementTree import fromstring

from reportlineage.types import DataType
from reportlineage.xml_utils import (
    child,
    children,
    descendants,
    local_name,
    map_data_type,
    sanitize_rdl_bytes,
    size_to_px,
    text_of,
)

_SAMPLE_XML = """
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition">
  <DataSets>
    <DataSet Name="A"><Fields><Field Name="X"/></Fields></DataSet>
    <DataSet Name="B"><Fields><Field Name="Y"/></Fields></DataSet>
  </DataSets>
  <Body><Tablix Name="T1"/><Nested><Tablix Name="T2"/></Nested></Body>
</Report>
"""


def test_local_name_strips_namespace():
    assert local_name("{http://example.com/ns}Foo") == "Foo"
    assert local_name("Foo") == "Foo"


def test_child_is_case_insensitive_and_direct_only():
    root = fromstring(_SAMPLE_XML)
    datasets_el = child(root, "datasets")
    assert datasets_el is not None
    assert local_name(datasets_el.tag) == "DataSets"
    # "DataSet" is not a direct child of Report, so child() must not find it there.
    assert child(root, "DataSet") is None


def test_children_returns_all_direct_matches():
    root = fromstring(_SAMPLE_XML)
    datasets_el = child(root, "DataSets")
    dsets = children(datasets_el, "DataSet")
    assert [d.get("Name") for d in dsets] == ["A", "B"]


def test_descendants_finds_nested_matches():
    root = fromstring(_SAMPLE_XML)
    tablixes = descendants(root, "Tablix")
    assert sorted(t.get("Name") for t in tablixes) == ["T1", "T2"]


def test_text_of_first_direct_child():
    xml = "<Query><CommandText> SELECT 1 </CommandText></Query>"
    el = fromstring(xml)
    assert text_of(el, "CommandText") == "SELECT 1"
    assert text_of(el, "Missing") is None


def test_size_to_px_units():
    assert size_to_px("1in") == 96.0
    assert size_to_px("2.54cm") == 96.0
    assert size_to_px("72pt") == 96.0
    assert size_to_px("100px") == 100.0
    assert size_to_px(None, default=5.0) == 5.0
    assert size_to_px("not-a-size", default=1.5) == 1.5


def test_size_to_px_unitless_defaults_to_pixels():
    assert size_to_px("42") == 42.0


def test_map_data_type():
    assert map_data_type("System.Int32") == DataType.INTEGER
    assert map_data_type("System.String") == DataType.STRING
    assert map_data_type("System.Decimal") == DataType.REAL
    assert map_data_type("Boolean") == DataType.BOOLEAN
    assert map_data_type(None) == DataType.UNKNOWN
    assert map_data_type("Nonsense") == DataType.UNKNOWN


def test_sanitize_rdl_bytes_strips_null_padding(tmp_path):
    p = tmp_path / "padded.rdl"
    p.write_bytes(b"<Report></Report>" + b"\x00" * 8)
    buf = sanitize_rdl_bytes(str(p))
    assert buf.read() == b"<Report></Report>"
