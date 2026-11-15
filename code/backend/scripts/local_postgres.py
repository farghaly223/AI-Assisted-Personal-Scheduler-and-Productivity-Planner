"""Manage a project-local test server. Never installs a service or contacts Supabase.

Windows binaries: extract EnterpriseDB's PostgreSQL binary zip under .local/pgsql.
Alternatively set PG_BIN to an existing local PostgreSQL bin directory.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

from app.core.config import BACKEND_ROOT

ROOT = BACKEND_ROOT / ".local"
DATA = ROOT / "pgdata"
CONFIG = ROOT / "postgres.json"
PORT = 55432


def binary(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    pg_bin = Path(os.environ.get("PG_BIN", ROOT / "pgsql" / "bin"))
    path = pg_bin / (name + suffix)
    if not path.is_file():
        raise RuntimeError("PostgreSQL binaries missing; extract the vendor archive or set PG_BIN")
    return str(path)


def run(*args: str) -> None:
    subprocess.run(args, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "start", "stop", "status"))
    action = parser.parse_args().action
    ROOT.mkdir(exist_ok=True)
    if action == "init":
        if DATA.exists() or CONFIG.exists():
            raise SystemExit("Local cluster/config already exists; refusing to overwrite it")
        password = secrets.token_urlsafe(32)
        password_file = ROOT / "init-password"
        password_file.write_text(password, encoding="utf-8")
        try:
            run(binary("initdb"), "-D", str(DATA), "-U", "postgres", "-A", "scram-sha-256", "--pwfile", str(password_file), "--encoding=UTF8", "--locale=C")
        finally:
            password_file.unlink(missing_ok=True)
        CONFIG.write_text(json.dumps({"host": "127.0.0.1", "port": PORT, "username": "postgres", "password": password}), encoding="utf-8")
        print("Initialized disposable local PostgreSQL; credentials saved only in ignored .local/postgres.json")
    elif action == "start":
        run(binary("pg_ctl"), "-D", str(DATA), "-l", str(ROOT / "postgres.log"), "-o", f"-h 127.0.0.1 -p {PORT}", "-w", "-t", "30", "start")
    elif action == "stop":
        run(binary("pg_ctl"), "-D", str(DATA), "-m", "fast", "-w", "-t", "30", "stop")
    else:
        run(binary("pg_ctl"), "-D", str(DATA), "status")


if __name__ == "__main__":
    main()
