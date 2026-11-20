"""Export the existing Alembic chain for manual SQL Editor use; never connect."""

import argparse
import io

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_ROOT


HEAD = "0003_dayli_foundation"
START = "0002_energy"
FILENAME = "upgrade_from_0002_energy.sql"


def export_sql() -> str:
    stream = io.StringIO()
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"), output_buffer=stream)
    command.upgrade(cfg, f"{START}:{HEAD}", sql=True)
    guard = f"""
DO $deployment_path$
BEGIN
    IF to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'Missing Alembic tracking; inspect the existing installation before upgrading';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version)<>1
       OR (SELECT version_num FROM public.alembic_version)<>'{START}' THEN
        RAISE EXCEPTION 'This script requires revision {START}; do not run it on another starting revision';
    END IF;
END
$deployment_path$;
"""
    header = f"""-- DAYLI / Tempo: {START} -> {HEAD}
-- Generated from the one frozen Alembic history; UTF-8 without BOM.
-- MANUAL deployment only. Review/backup the existing project before running once.
-- Requires existing Supabase Auth and migration privileges; no tokens or passwords.
-- All statements belong to one transaction. Any error rolls back the entire upgrade.

"""
    sql_text = stream.getvalue().replace("BEGIN;", "BEGIN;\n" + guard, 1)
    return header + sql_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if checked-in exports differ from the frozen history")
    args = parser.parse_args()
    path = BACKEND_ROOT / "review" / FILENAME
    expected = export_sql()
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            print(f"Outdated SQL export: review/{FILENAME}")
            return 1
    else:
        path.write_text(expected,encoding="utf-8",newline="\n")
    print(f"{'Verified' if args.check else 'Exported'} review/{FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
