"""Create a separate restricted login; does not grant access to Auth user data.

Run with MIGRATION_DATABASE_URL, AFTER migration. Password is read without echo.
Remote role provisioning requires a separate explicit --allow-remote switch.
"""

import argparse
import getpass
import re

import psycopg
from psycopg import sql

from app.core.config import LOCAL_HOSTS, database_url


def connect_admin(url, *, autocommit=False):
    return psycopg.connect(
        host=url.host, port=url.port or 5432, dbname=url.database,
        user=url.username, password=url.password, **dict(url.query),
        connect_timeout=10, autocommit=autocommit,
    )


def create_restricted_role(connection, role: str, password: str) -> None:
    if not re.fullmatch(r"scheduler_[a-z0-9_]{1,45}", role):
        raise ValueError("Use a dedicated scheduler_ role name")
    if not password:
        raise ValueError("A nonempty runtime password is required")
    # Identifier/Literal quoting, never string interpolation or logged passwords.
    connection.execute(sql.SQL(
        "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Identifier(role), sql.Literal(password)))
    connection.execute(sql.SQL("GRANT authenticated TO {}").format(sql.Identifier(role)))
    database = connection.info.dbname
    connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="scheduler_runtime")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    try:
        url = database_url("MIGRATION_DATABASE_URL")
        if url.host not in LOCAL_HOSTS and not args.allow_remote:
            raise ValueError("Remote role provisioning requires explicit approval and --allow-remote")
        password = getpass.getpass("New restricted runtime password: ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
        with connect_admin(url) as connection:
            create_restricted_role(connection, args.role, password)
        print("Restricted login created. Configure DATABASE_URL separately and run the runtime connection check.")
        return 0
    except Exception as error:
        print(f"Role provisioning failed ({type(error).__name__}); no privileged-runtime fallback is permitted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
