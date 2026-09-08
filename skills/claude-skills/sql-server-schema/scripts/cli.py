"""
cli.py - drive this skill from a terminal, with no MCP host involved.

Four commands:

  drivers          which ODBC drivers this machine has, and which would be used
  test-connection  prove Windows Integrated Auth reaches the server
  digest           read a database's schema and write a digest JSON
  generate         turn a digest into a skill pack
  example          regenerate the committed, fictional example digest

``drivers``, ``generate`` and ``example`` need no database and no ODBC driver.
Only ``test-connection`` and ``digest`` touch a server.

Start with ``test-connection``: if the auth_scheme it reports is not KERBEROS
or NTLM, nothing else here will work, and the problem is Kerberos, a firewall,
or the SQL Browser service rather than anything in this code. Before blaming
Python at all, confirm the same thing at the driver level::

    sqlcmd -S <server> -d <database> -E -Q "SELECT @@VERSION, SUSER_SNAME()"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import analyze  # noqa: E402
import connection as conn_mod  # noqa: E402
import guardrails  # noqa: E402
import skillgen  # noqa: E402


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_drivers(args: argparse.Namespace) -> int:
    try:
        installed = conn_mod.available_drivers()
    except conn_mod.ConnectionError_ as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    try:
        chosen = conn_mod.pick_driver(drivers=installed)
    except conn_mod.ConnectionError_ as exc:
        _print({"ok": False, "installed": installed, "error": str(exc)})
        return 1
    _print({"ok": True, "installed": installed, "would_use": chosen})
    return 0


def cmd_test_connection(args: argparse.Namespace) -> int:
    import catalog

    try:
        settings = conn_mod.connection_settings(args.server, args.database)
        with conn_mod.connection(args.server, args.database) as conn:
            info = catalog.get_server_info(conn)
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 1

    scheme = info.get("auth_scheme")
    payload = {
        "ok": True,
        "driver": settings["driver"],
        "encrypt_mode": settings["encrypt_mode"],
        "database": info.get("current_database"),
        "product_version": info.get("product_version"),
        "edition": info.get("edition"),
        "auth_scheme": scheme,
        "integrated_auth_confirmed": scheme in ("KERBEROS", "NTLM"),
    }
    if args.show_login:
        payload["login_name"] = info.get("login_name")
    if info.get("missing_permissions"):
        payload["missing_permissions"] = info["missing_permissions"]
        payload["hint"] = info.get("hint")
    _print(payload)
    return 0


def _collect_tables(conn, schema: Optional[str], include_views: bool) -> List[Dict[str, Any]]:
    import catalog

    tables: List[Dict[str, Any]] = []
    for row in catalog.list_tables(conn, schema=schema, include_views=include_views):
        qualified = "{}.{}".format(row["schema_name"], row["object_name"])
        detail = catalog.describe_table(conn, qualified)
        if not detail.get("found"):
            continue
        tables.append(
            {
                "schema": row["schema_name"],
                "name": row["object_name"],
                "object_type": row.get("type_desc"),
                "row_count": row.get("row_count") or 0,
                "size_mb": row.get("size_mb"),
                "description": row.get("description"),
                "columns": detail["columns"],
                "primary_key": detail["primary_key"],
                "indexes": detail["indexes"],
            }
        )
    return tables


def cmd_digest(args: argparse.Namespace) -> int:
    import catalog

    try:
        with conn_mod.connection(args.server, args.database) as conn:
            server_info = catalog.get_server_info(conn)
            schemas = catalog.list_schemas(conn)
            tables = _collect_tables(conn, args.schema, args.include_views)
            foreign_keys = catalog.get_foreign_keys(conn)
            routines = (
                catalog.list_programmability(conn, schema=args.schema)
                if args.include_routines
                else []
            )
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 1

    digest = analyze.build_digest(
        database=conn_mod.resolve_database(args.database),
        tables=tables,
        foreign_keys=foreign_keys,
        schemas=schemas,
        server_info=server_info,
        routines=routines,
        generated_at=args.generated_at or "",
        server_alias=args.server_alias,
        infer=not args.no_infer,
    )

    try:
        target = guardrails.resolve_artifact_path(
            args.out,
            "digests/{}.schema-digest.json".format(skillgen.slugify(digest["source"]["database"])),
            allow_in_repo=args.allow_in_repo,
        )
    except PermissionError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(digest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    _print(
        {
            "ok": True,
            "path": str(target),
            "tables": len(digest["tables"]),
            "relationships": digest["coverage"]["relationship_count"],
            "note": "This file names internal database objects. It is written "
            "outside the repository by default.",
        }
    )
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        digest = skillgen.load_digest(args.digest)
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    try:
        result = skillgen.generate_skill_pack(
            digest,
            args.out,
            include_samples=False,
            max_columns_per_table=args.max_columns,
            max_tables_per_file=args.max_tables_per_file,
            allow_in_repo=args.allow_in_repo,
            dry_run=args.dry_run,
        )
    except PermissionError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print(result)
    return 0 if result.get("ok") else 1


def cmd_example(args: argparse.Namespace) -> int:
    """Regenerate the committed example digest from the invented star schema.

    Safe to commit precisely because the schema is fictional: it exists so the
    generator has a regression fixture and the docs have a worked example.
    """
    sys.path.insert(0, str(_HERE.parent / "tests"))
    from test_analyze import _star  # type: ignore

    tables, foreign_keys = _star()
    digest = analyze.build_digest(
        database="SalesDW",
        tables=tables,
        foreign_keys=foreign_keys,
        generated_at="2026-01-01T00:00:00Z",
        server_alias="demo-sql",
        server_info={
            "product_version": "15.0.4345.5",
            "edition": "Developer Edition (64-bit)",
            "server_collation": "SQL_Latin1_General_CP1_CI_AS",
        },
    )
    target = _HERE.parent / "examples" / "schema-digest.example.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(digest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    _print({"ok": True, "path": str(target), "tables": len(digest["tables"])})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sql-server-schema",
        description="Read an on-premises SQL Server schema and turn it into a skill pack.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("drivers", help="list installed ODBC drivers").set_defaults(
        func=cmd_drivers
    )

    test = sub.add_parser("test-connection", help="prove Windows auth reaches the server")
    test.add_argument("--server", default=None)
    test.add_argument("--database", default=None)
    test.add_argument(
        "--show-login",
        action="store_true",
        help="include the AD login name in the output (withheld by default)",
    )
    test.set_defaults(func=cmd_test_connection)

    digest = sub.add_parser("digest", help="read a schema and write a digest JSON")
    digest.add_argument("--server", default=None)
    digest.add_argument("--database", default=None)
    digest.add_argument("--schema", default=None, help="limit to one schema")
    digest.add_argument("--out", default=None, help="output path (default: outside the repo)")
    digest.add_argument("--server-alias", default=None)
    digest.add_argument("--generated-at", default=None)
    digest.add_argument("--include-views", action="store_true")
    digest.add_argument("--include-routines", action="store_true")
    digest.add_argument("--no-infer", action="store_true", help="declared foreign keys only")
    digest.add_argument("--allow-in-repo", action="store_true")
    digest.set_defaults(func=cmd_digest)

    generate = sub.add_parser("generate", help="turn a digest into a skill pack")
    generate.add_argument("digest", help="path to a schema digest JSON")
    generate.add_argument("--out", default=None)
    generate.add_argument("--max-columns", type=int, default=60)
    generate.add_argument("--max-tables-per-file", type=int, default=40)
    generate.add_argument("--allow-in-repo", action="store_true")
    generate.add_argument(
        "--dry-run",
        action="store_true",
        help="render and measure everything, write nothing",
    )
    generate.set_defaults(func=cmd_generate)

    sub.add_parser(
        "example", help="regenerate the committed fictional example digest"
    ).set_defaults(func=cmd_example)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
