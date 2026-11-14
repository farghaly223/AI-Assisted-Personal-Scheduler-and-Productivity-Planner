"""Read-only runtime connection/role check: python -m app.db.check."""

from app.config import database_url
from app.db.session import Database, create_database_engine


def main() -> int:
    database = None
    try:
        database = Database(create_database_engine(database_url("DATABASE_URL")))
        database.check_connection()
        print("Connection OK; restricted role and RLS prerequisites verified; no scoped user identity.")
        print("This check alone does not prove cross-user isolation; run the integration tests.")
        return 0
    except Exception as error:
        # Driver exception text can contain endpoints/credentials. Never print it.
        print(f"Connection/role check failed ({type(error).__name__}); inspect configuration and role grants.")
        return 1
    finally:
        if database:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
