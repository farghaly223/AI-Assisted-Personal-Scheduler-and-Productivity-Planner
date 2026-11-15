"""Read-only runtime connection/role check: python -m app.db.check."""

from app.config import ConfigurationError, Settings
from app.db.session import Database, create_database_engine


def main() -> int:
    database = None
    try:
        settings = Settings.from_environment()
        if not settings.runtime_url:
            raise ConfigurationError("Set DATABASE_URL in the environment or backend/.env")
        database = Database(create_database_engine(settings.runtime_url),
                            allow_admin_runtime=settings.allow_admin_database_runtime)
        database.check_connection()
        if settings.allow_admin_database_runtime:
            print("Connection OK; development administrative access; RLS isolation is not enforced for this connection.")
        else:
            print("Connection OK; restricted role and RLS prerequisites verified; no scoped user identity.")
        print("This connection check alone does not prove cross-user isolation.")
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
