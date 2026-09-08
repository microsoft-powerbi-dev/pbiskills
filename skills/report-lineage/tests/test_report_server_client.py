"""Tests for reportlineage.scanners.report_server (mocked, no live server)."""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

import reportlineage.scanners.report_server as report_server_module
from reportlineage.scanners.report_server import CatalogItem, ReportServerClient


def _mock_response(status_code=200, json_data=None, content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_probe_calls_system_endpoint_and_parses_response():
    session = MagicMock()
    session.get.return_value = _mock_response(
        json_data={"ProductVersion": "1.0.0", "ProductType": "PBIRS"}
    )
    client = ReportServerClient("https://server/reports", session=session)

    info = client.probe()

    called_url = session.get.call_args[0][0]
    assert called_url == "https://server/reports/api/v2.0/System"
    assert info == {"product_version": "1.0.0", "edition": "PBIRS"}


def test_base_url_already_ending_in_api_v2_is_not_double_appended():
    session = MagicMock()
    session.get.return_value = _mock_response(json_data={"value": []})
    client = ReportServerClient("https://server/reports/api/v2.0", session=session)

    client.list_catalog()

    called_url = session.get.call_args[0][0]
    assert called_url == "https://server/reports/api/v2.0/CatalogItems"


def test_list_catalog_fetches_flat_collection_and_filters_client_side():
    session = MagicMock()
    rows = [
        {"Id": "1", "Path": "/Sales/Report1", "Name": "Report1", "Type": "Report"},
        {"Id": "2", "Path": "/Sales", "Name": "Sales", "Type": "Folder"},
    ]
    session.get.return_value = _mock_response(json_data={"value": rows})
    client = ReportServerClient("https://server/reports", session=session)

    items = client.list_catalog(item_type="Report")

    assert len(items) == 1
    assert items[0].path == "/Sales/Report1"
    assert items[0].id == "1"
    called_url = session.get.call_args[0][0]
    assert called_url.endswith("/api/v2.0/CatalogItems")


def test_download_definition_tries_content_value_then_base64_fallback():
    session = MagicMock()
    value_resp = _mock_response(status_code=404, content=b"")
    entity_resp = _mock_response(json_data={"Content": base64.b64encode(b"<Report/>").decode("ascii")})
    session.get.side_effect = [value_resp, entity_resp]

    client = ReportServerClient("https://server/reports", session=session)
    item = CatalogItem(id="1", path="/r", name="r", type="Report")

    data = client.download_definition(item)

    assert data == b"<Report/>"
    first_url = session.get.call_args_list[0][0][0]
    second_url = session.get.call_args_list[1][0][0]
    assert first_url == "https://server/reports/api/v2.0/CatalogItems(1)/Content/$value"
    assert second_url == "https://server/reports/api/v2.0/CatalogItems(1)"


def test_download_definition_returns_content_value_directly_when_available():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"<Report/>")
    client = ReportServerClient("https://server/reports", session=session)
    item = CatalogItem(id="1", path="/r", name="r", type="Report")

    data = client.download_definition(item)

    assert data == b"<Report/>"
    assert session.get.call_count == 1


def test_scan_downloads_each_item_and_skips_failures(tmp_path):
    session = MagicMock()
    client = ReportServerClient("https://server/reports", session=session)

    items = [
        CatalogItem(id="1", path="/Sales/Good", name="Good", type="Report"),
        CatalogItem(id="2", path="/Sales/Bad", name="Bad", type="Report"),
    ]
    client.list_catalog = MagicMock(return_value=items)

    def _download(item):
        if item.name == "Bad":
            raise RuntimeError("boom")
        return b"<Report/>"

    client.download_definition = MagicMock(side_effect=_download)

    result_items, paths = client.scan(item_type="Report", download_dir=str(tmp_path))

    assert result_items == items
    assert len(paths) == 1
    assert paths[0].endswith("Good.rdl")


def test_report_server_functions_require_requests_when_not_installed(monkeypatch):
    def _raise():
        raise RuntimeError(
            "The 'requests' package is required for report-server scanning: pip install requests"
        )

    monkeypatch.setattr(report_server_module, "_require_requests", _raise)

    with pytest.raises(RuntimeError, match="requests"):
        report_server_module.ReportServerClient("https://server/reports")


def test_require_requests_helper_raises_actionable_error_on_import_failure(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("no module named requests")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(RuntimeError, match="pip install requests"):
        report_server_module._require_requests()
