"""
seed_three_dataset_feed.py - one-command demo setup.

Runs, in order: seed_schema.sql (the meta/src/gen schemas), build the
committed sample workbook if it isn't already there, seed_source_data.py
(synthetic Claim/Member/StatusLookup data), and loads that workbook into
meta.* via config_loader. After this, `extract dry-run --feed ClaimsExtract`
and `extract run --feed ClaimsExtract --mode polars` are ready to use.

Run: python scripts/seed/seed_three_dataset_feed.py [--server <server>] [--database <db>]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_SKILL_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from extract_engine import config_loader as cl  # noqa: E402
from extract_engine import connection as conn_mod  # noqa: E402

from build_sample_workbook import build as build_sample_workbook  # noqa: E402
from seed_source_data import seed as seed_source_data  # noqa: E402


def run(server: str = None, database: str = None) -> None:
    schema_sql = Path(__file__).resolve().parent / "seed_schema.sql"
    print("Deploying meta/src/gen schemas...")
    resolved_server = server or conn_mod.resolve_server(server)
    resolved_db = database or conn_mod.resolve_database(database)
    # seed_schema.sql uses IF OBJECT_ID(...) IS NULL guards throughout, so
    # sqlcmd is safe to run repeatedly against the same database.
    subprocess.run(
        ["sqlcmd", "-S", resolved_server, "-d", resolved_db, "-E", "-i", str(schema_sql)],
        check=True,
    )

    workbook_path = _SKILL_ROOT / "examples" / "sample-workbook.xlsx"
    if not workbook_path.exists():
        print("Building the sample workbook...")
        build_sample_workbook()

    print("Seeding synthetic source data...")
    seed_source_data(server, database)

    print("Loading the sample workbook into meta.*...")
    config = cl.load_workbook(str(workbook_path))
    report = cl.validate(config)
    if not report.ok:
        raise SystemExit("Sample workbook failed validation:\n{}".format(report))
    with conn_mod.write_connection(server=server, database=database) as conn:
        version_id = cl.upsert(config, conn, loaded_by="seed_three_dataset_feed")
    print("Loaded feed_config_version_id={}. Ready: extract dry-run --feed ClaimsExtract".format(version_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=None)
    parser.add_argument("--database", default=None)
    args = parser.parse_args()
    run(args.server, args.database)
