"""
A minimal SSRS / Power BI Report Server REST v2.0 client for estate scanning.

Fetches the catalog as one flat ``GET /CatalogItems`` collection (``$filter``
support drifts between SSRS 2017/2019/2022 and PBIRS releases, whereas the
flat collection is stable everywhere) and filters client-side, then downloads
each definition, trying the streaming ``/Content/$value`` form first and
falling back to the entity's base64 ``Content`` property.

This module imports ``requests`` lazily: the rest of ``reportlineage`` (and
even this module's own import) never requires it. Only calling a function
that actually talks to a server raises, with an actionable message, if
``requests`` is not installed.

Authentication: pass ``auth=(username, password)`` for HTTP Basic. A report
server behind Windows auth (the common on-prem case) needs NTLM or Kerberos,
which this module does not implement directly; instead, build your own
``requests.Session`` with the auth plugin of your choice (for example
``requests_negotiate_sspi.HttpNegotiateAuth`` on Windows, or
``requests_kerberos.HTTPKerberosAuth`` on Linux/Mac) and pass it via the
``session=`` constructor parameter. When ``session`` is given, it is used
exactly as provided: ``auth``/``verify_ssl`` are not applied to it.
"""
from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

#: Guard against a pathological catalog exhausting memory.
_MAX_ITEMS = 20000


def _require_requests():
    """Lazily import ``requests``, raising a clear, actionable error if absent."""
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "The 'requests' package is required for report-server scanning: pip install requests"
        ) from exc
    return requests


@dataclass(frozen=True)
class CatalogItem:
    """One node of the report-server catalog (a folder, report, dataset, ...)."""

    id: str
    path: str
    name: str
    type: str
    description: str = ""
    modified_at: str = ""
    modified_by: str = ""
    size: int = 0
    hidden: bool = False


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _to_catalog_item(row: Dict[str, Any]) -> Optional[CatalogItem]:
    path = _as_str(row.get("Path"))
    if not path:
        return None
    name = _as_str(row.get("Name")) or path.rsplit("/", 1)[-1]
    try:
        size = int(row.get("Size") or 0)
    except (TypeError, ValueError):
        size = 0
    return CatalogItem(
        id=_as_str(row.get("Id")),
        path=path,
        name=name,
        type=_as_str(row.get("Type")) or "Unknown",
        description=_as_str(row.get("Description")),
        modified_at=_as_str(row.get("ModifiedDate")),
        modified_by=_as_str(row.get("ModifiedBy")),
        size=size,
        hidden=bool(row.get("Hidden")),
    )


class ReportServerClient:
    """A small REST v2.0 client for SSRS 2017+ and Power BI Report Server.

    ``base_url`` is the bare server root (e.g. ``https://server/reports``);
    ``/api/v2.0`` is appended internally. Passing the full API base
    (``https://server/reports/api/v2.0``) also works: it is detected and not
    double-appended.
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: Optional[Tuple[str, str]] = None,
        verify_ssl: bool = True,
        timeout: int = 30,
        session: Optional[Any] = None,
    ) -> None:
        self.timeout = timeout
        base = (base_url or "").rstrip("/")
        self.api_base = base if base.lower().endswith("api/v2.0") else f"{base}/api/v2.0"

        if session is not None:
            self._session = session
        else:
            requests = _require_requests()
            self._session = requests.Session()
            if auth is not None:
                self._session.auth = auth
            self._session.verify = verify_ssl

    def probe(self) -> Dict[str, str]:
        """``GET /System``: basic reachability + version info."""
        url = f"{self.api_base}/System"
        resp = self._session.get(url, timeout=self.timeout)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"Report server probe failed: HTTP {resp.status_code} at {url}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Report server probe at {url} returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Report server probe at {url} returned an unexpected shape")
        return {
            "product_version": _as_str(payload.get("ProductVersion")),
            "edition": _as_str(payload.get("ProductType")) or _as_str(payload.get("ProductName")),
        }

    def list_catalog(self, item_type: Optional[str] = None) -> List[CatalogItem]:
        """``GET /CatalogItems`` (flat), optionally filtered client-side by type."""
        url = f"{self.api_base}/CatalogItems"
        resp = self._session.get(url, timeout=self.timeout)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"Failed to list catalog: HTTP {resp.status_code} at {url}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"CatalogItems at {url} returned non-JSON") from exc

        rows = payload.get("value") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError(f"CatalogItems at {url} returned an unexpected shape")
        if len(rows) > _MAX_ITEMS:
            print(
                f"warning: report server catalog has {len(rows)} items, truncating to {_MAX_ITEMS}",
                file=sys.stderr,
            )
            rows = rows[:_MAX_ITEMS]

        items: List[CatalogItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = _to_catalog_item(row)
            if item is None:
                continue
            if item_type is not None and item.type != item_type:
                continue
            items.append(item)
        return items

    def download_definition(self, item: CatalogItem) -> bytes:
        """The raw definition bytes for one catalog item.

        Tries the streaming ``/Content/$value`` form first, then falls back to
        the entity's base64 ``Content`` property.
        """
        if not item.id:
            raise RuntimeError(f"Catalog item '{item.path}' has no id; cannot download.")

        value_url = f"{self.api_base}/CatalogItems({item.id})/Content/$value"
        resp = self._session.get(value_url, timeout=self.timeout)
        if resp.status_code == 200 and resp.content:
            return resp.content

        entity_url = f"{self.api_base}/CatalogItems({item.id})"
        resp = self._session.get(entity_url, timeout=self.timeout)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"Failed to download definition for '{item.path}': HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Definition fetch for '{item.path}' returned non-JSON") from exc

        content = payload.get("Content") if isinstance(payload, dict) else None
        if not content:
            raise RuntimeError(f"Report server returned no definition content for '{item.path}'.")
        try:
            return base64.b64decode(content)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Definition for '{item.path}' was not valid base64") from exc

    def scan(
        self, item_type: str = "Report", download_dir: Optional[str] = None
    ) -> Tuple[List[CatalogItem], List[str]]:
        """List (and optionally download) every catalog item of ``item_type``.

        A single item that fails to download is skipped (a warning is printed
        to stderr) rather than aborting the whole scan.
        """
        items = self.list_catalog(item_type=item_type)
        local_paths: List[str] = []

        if download_dir:
            os.makedirs(download_dir, exist_ok=True)
            for item in items:
                try:
                    content = self.download_definition(item)
                except Exception as exc:  # noqa: BLE001 - one bad item must not abort the scan
                    print(f"warning: failed to download '{item.path}': {exc}", file=sys.stderr)
                    continue
                rel = item.path.lstrip("/")
                local_path = os.path.join(download_dir, rel)
                if not local_path.lower().endswith(".rdl"):
                    local_path = f"{local_path}.rdl"
                parent = os.path.dirname(local_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                try:
                    with open(local_path, "wb") as fh:
                        fh.write(content)
                except OSError as exc:
                    print(f"warning: failed to write '{local_path}': {exc}", file=sys.stderr)
                    continue
                local_paths.append(local_path)

        return items, local_paths
