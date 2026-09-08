"""
Command-line entry point for ``reportlineage``.

Subcommands:

``scan <path>``
    Scan a local folder, build the estate graph, print a one-line summary.
``scan-git <url>``
    Shallow-clone a git repository, then do the same as ``scan``.
``scan-server <base-url>``
    Download definitions from an SSRS / Power BI Report Server REST catalog,
    then do the same as ``scan``.
``duplicates <path>``
    Fingerprint every ``.rdl`` under a folder and report overlap clusters.
``search <path> <query>``
    Fingerprint every ``.rdl`` under a folder and rank them against a query.

Run with ``python -m reportlineage <subcommand> ...``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict
from typing import List, Optional

from reportlineage import __version__
from reportlineage.builder import build_estate
from reportlineage.duplicates import analyze_duplicates
from reportlineage.export import to_csv_edges, to_csv_nodes, to_html_summary, to_json, to_mermaid
from reportlineage.fingerprint import fingerprint_rdl
from reportlineage.scanners.filesystem import ScanResult, scan_filesystem
from reportlineage.search import SearchIndex


def _has_any_artifacts(result: ScanResult) -> bool:
    return bool(result.rdl_paths or result.dtsx_paths or result.conmgr_paths or result.sql_paths)


def _print_graph_summary(graph) -> None:
    print(f"nodes={len(graph.nodes)} edges={len(graph.edges)} diagnostics={len(graph.diagnostics)}")


def _write_exports(graph, out_dir: str, *, mermaid: bool, html: bool, csv_export: bool) -> str:
    os.makedirs(out_dir, exist_ok=True)
    estate_path = os.path.join(out_dir, "estate.json")
    to_json(graph, path=estate_path)
    if mermaid:
        with open(os.path.join(out_dir, "estate.mmd"), "w", encoding="utf-8") as fh:
            fh.write(to_mermaid(graph))
    if html:
        with open(os.path.join(out_dir, "estate.html"), "w", encoding="utf-8") as fh:
            fh.write(to_html_summary(graph))
    if csv_export:
        with open(os.path.join(out_dir, "nodes.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write(to_csv_nodes(graph))
        with open(os.path.join(out_dir, "edges.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write(to_csv_edges(graph))
    return estate_path


def _scan_and_export(result: ScanResult, out_dir: str, *, mermaid: bool, html: bool, csv_export: bool) -> int:
    if not _has_any_artifacts(result):
        print("No RDL/DTSX/conmgr/SQL files found to scan.")
        return 0
    graph = build_estate(
        rdl_paths=result.rdl_paths,
        dtsx_paths=result.dtsx_paths,
        conmgr_paths=result.conmgr_paths,
        sql_paths=result.sql_paths,
    )
    _print_graph_summary(graph)
    estate_path = _write_exports(graph, out_dir, mermaid=mermaid, html=html, csv_export=csv_export)
    print(f"wrote {estate_path}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    result = scan_filesystem(args.path)
    for note in result.skipped:
        print(f"note: {note}", file=sys.stderr)
    return _scan_and_export(result, args.out, mermaid=args.mermaid, html=args.html, csv_export=args.csv)


def _cmd_scan_git(args: argparse.Namespace) -> int:
    from reportlineage.scanners.git_repo import scan_git_repo

    try:
        result = scan_git_repo(args.url, branch=args.branch, token_env_var=args.token_env)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _scan_and_export(result, args.out, mermaid=args.mermaid, html=args.html, csv_export=args.csv)


def _cmd_scan_server(args: argparse.Namespace) -> int:
    from reportlineage.scanners.report_server import ReportServerClient

    password: Optional[str] = args.password
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            print(f"error: environment variable '{args.password_env}' is not set.", file=sys.stderr)
            return 1

    auth = (args.user, password) if args.user and password else None
    out_dir = args.out or tempfile.mkdtemp(prefix="reportlineage-scan-")

    try:
        client = ReportServerClient(args.base_url, auth=auth)
        items, paths = client.scan(item_type=args.item_type, download_dir=out_dir)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not paths:
        print(f"No '{args.item_type}' definitions could be downloaded from {args.base_url}.")
        return 0

    result = scan_filesystem(out_dir)
    return _scan_and_export(result, out_dir, mermaid=args.mermaid, html=args.html, csv_export=args.csv)


def _cmd_duplicates(args: argparse.Namespace) -> int:
    result = scan_filesystem(args.path)
    if not result.rdl_paths:
        print(f"No .rdl files found under {args.path}.")
        return 0

    fingerprints = [fingerprint_rdl(p, key=p) for p in result.rdl_paths]
    summary = analyze_duplicates(fingerprints)

    if not summary.clusters:
        print("No duplicate or overlapping reports found.")
    for cluster in summary.clusters:
        keeper_name = next((m.name for m in cluster.members if m.key == cluster.keeper), cluster.keeper)
        member_names = ", ".join(m.name for m in cluster.members)
        print(f"[{cluster.verdict}] keeper={keeper_name} action={cluster.action} members={member_names}")

    if args.out:
        parent = os.path.dirname(args.out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "pairs": [asdict(p) for p in summary.pairs],
            "clusters": [asdict(c) for c in summary.clusters],
            "unmatched": summary.unmatched,
        }
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"wrote {args.out}")

    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    result = scan_filesystem(args.path)
    if not result.rdl_paths:
        print(f"No .rdl files found under {args.path}.")
        return 0

    fingerprints = [fingerprint_rdl(p, key=p) for p in result.rdl_paths]
    index = SearchIndex(fingerprints)
    hits = index.search(args.query)

    if not hits:
        print("No matches found.")
        return 0

    for fp, score in hits:
        print(f"{score:.3f}  {fp.name}  ({fp.key})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reportlineage")
    parser.add_argument("--version", action="store_true", help="print the package version and exit")

    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="scan a local folder of SSRS/SSIS artifacts")
    p_scan.add_argument("path")
    p_scan.add_argument("--out", default="./reportlineage-out")
    p_scan.add_argument("--mermaid", action="store_true")
    p_scan.add_argument("--html", action="store_true")
    p_scan.add_argument("--csv", action="store_true")

    p_git = sub.add_parser("scan-git", help="clone a git repository and scan it")
    p_git.add_argument("url")
    p_git.add_argument("--out", default="./reportlineage-out")
    p_git.add_argument("--branch", default=None)
    p_git.add_argument("--token-env", default=None, help="env var holding a PAT for HTTPS auth")
    p_git.add_argument("--mermaid", action="store_true")
    p_git.add_argument("--html", action="store_true")
    p_git.add_argument("--csv", action="store_true")

    p_srv = sub.add_parser("scan-server", help="download from an SSRS/PBIRS REST catalog and scan")
    p_srv.add_argument("base_url")
    p_srv.add_argument("--out", default=None)
    p_srv.add_argument("--user", default=None)
    p_srv.add_argument("--password", default=None, help="prefer --password-env over this")
    p_srv.add_argument("--password-env", default=None, help="env var holding the password (preferred)")
    p_srv.add_argument("--item-type", default="Report")
    p_srv.add_argument("--mermaid", action="store_true")
    p_srv.add_argument("--html", action="store_true")
    p_srv.add_argument("--csv", action="store_true")

    p_dup = sub.add_parser("duplicates", help="find duplicate/overlapping RDL reports under a folder")
    p_dup.add_argument("path")
    p_dup.add_argument("--out", default=None)

    p_search = sub.add_parser("search", help="rank RDL reports under a folder against a query")
    p_search.add_argument("path")
    p_search.add_argument("query")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "scan": _cmd_scan,
        "scan-git": _cmd_scan_git,
        "scan-server": _cmd_scan_server,
        "duplicates": _cmd_duplicates,
        "search": _cmd_search,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
