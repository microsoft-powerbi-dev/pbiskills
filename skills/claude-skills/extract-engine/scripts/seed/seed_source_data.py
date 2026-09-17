"""
seed_source_data.py - synthetic src.* tables matching examples/sample-workbook.xlsx.

Creates ONLY new objects under a `src` schema (never touches anything that
might already exist in the target database) and inserts entirely fictional
data. This is what examples/sample-workbook.xlsx's Datasets/Lookups sheets
describe, and what proves the three dataset modes end to end:

- src.claim        primary dataset, windowed on service_date
- src.member       dependent dataset, pulled in via Claim.member_id
- src.claim_calc   a "calculator table" joined into Claim for PaidAmount
- src.status_lookup a reference dataset, shipped in full every run

Member 100 is deliberately created in 1999 - decades outside any realistic
window - and referenced only by an in-window claim, while Member 200 is
recently created but referenced only by an OUT-of-window claim. This is the
acceptance-check-#5 proof (see docs/extract-engine-mvp-prompt-v2-polars.md):
after seeding, running the feed's Member dataset must include Member 100 and
exclude Member 200 - the opposite of what row-count or creation-date
intuition would suggest.

Run: python scripts/seed/seed_source_data.py [--server <server>] [--database <db>]

Never drops or deletes anything outside the four tables this script itself
creates, and only clears rows it itself inserted (by known synthetic key),
never a bare DELETE.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extract_engine import connection as conn_mod

DDL = """
IF SCHEMA_ID('src') IS NULL EXEC('CREATE SCHEMA src');
"""

TABLES = {
    "src.member": """
        IF OBJECT_ID('src.member') IS NULL
        CREATE TABLE src.member (
            member_id INT PRIMARY KEY,
            member_name NVARCHAR(100) NOT NULL,
            created_date DATE NOT NULL
        );
    """,
    "src.claim": """
        IF OBJECT_ID('src.claim') IS NULL
        CREATE TABLE src.claim (
            claim_id VARCHAR(20) PRIMARY KEY,
            member_id INT NOT NULL,
            service_date DATE NOT NULL,
            status_code VARCHAR(10) NOT NULL,
            notes NVARCHAR(200) NULL
        );
    """,
    "src.claim_calc": """
        IF OBJECT_ID('src.claim_calc') IS NULL
        CREATE TABLE src.claim_calc (
            claim_id VARCHAR(20) PRIMARY KEY,
            amount DECIMAL(18,3) NOT NULL
        );
    """,
    "src.status_lookup": """
        IF OBJECT_ID('src.status_lookup') IS NULL
        CREATE TABLE src.status_lookup (
            status_code VARCHAR(10) PRIMARY KEY,
            description NVARCHAR(50) NOT NULL
        );
    """,
}

# Deliberately fictional. member_id 100 is the dependent-mode proof: created
# 1999, referenced only by an in-window claim, so the Member extract must
# include it despite its own date being decades outside any window. member_id
# 200 is the inverse control: recently created, referenced only by an
# out-of-window claim, so it must NOT appear.
MEMBER_ROWS = [
    (100, "  Alice Old-Timer  ", "1999-03-01"),
    (200, "Bob Recent", "2026-01-01"),
]
CLAIM_ROWS = [
    ("C-IN-WINDOW", 100, "2026-06-15", "open", None),
    ("C-OUT-WINDOW", 200, "2020-01-01", "shut", "has|pipe|to sanitize"),
]
CLAIM_CALC_ROWS = [
    ("C-IN-WINDOW", "199.995"),   # exercises the half-away-from-zero rounding fix
    ("C-OUT-WINDOW", "50.00"),
]
STATUS_ROWS = [("OPEN", "Open"), ("SHUT", "Shut")]


def seed(server: str = None, database: str = None) -> None:
    with conn_mod.write_connection(server=server, database=database) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(DDL)
            for ddl in TABLES.values():
                cursor.execute(ddl)

            # Known synthetic keys only, never a bare DELETE.
            cursor.execute("DELETE FROM src.member WHERE member_id IN (?, ?)", 100, 200)
            cursor.executemany(
                "INSERT INTO src.member (member_id, member_name, created_date) VALUES (?, ?, ?)",
                MEMBER_ROWS,
            )
            cursor.execute("DELETE FROM src.claim WHERE claim_id IN (?, ?)", "C-IN-WINDOW", "C-OUT-WINDOW")
            cursor.executemany(
                "INSERT INTO src.claim (claim_id, member_id, service_date, status_code, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                CLAIM_ROWS,
            )
            cursor.execute("DELETE FROM src.claim_calc WHERE claim_id IN (?, ?)", "C-IN-WINDOW", "C-OUT-WINDOW")
            cursor.executemany(
                "INSERT INTO src.claim_calc (claim_id, amount) VALUES (?, ?)", CLAIM_CALC_ROWS
            )
            cursor.execute("DELETE FROM src.status_lookup WHERE status_code IN (?, ?)", "OPEN", "SHUT")
            cursor.executemany(
                "INSERT INTO src.status_lookup (status_code, description) VALUES (?, ?)", STATUS_ROWS
            )
        finally:
            cursor.close()
    print("Synthetic src.* data seeded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=None)
    parser.add_argument("--database", default=None)
    args = parser.parse_args()
    seed(args.server, args.database)
