import os
from sqlalchemy import event


def configure_sqlalchemy(app):
    """
    Configure SQLAlchemy URI with optional SQLCipher support.

    - Default: sqlite:///chat.db
    - Full DB encryption: set SQLCIPHER_KEY and install pysqlcipher3.
    """
    default_uri = "sqlite:///chat.db"
    raw_db = os.getenv("DATABASE_URL")
    base_uri = (raw_db or "").strip() or default_uri
    # Railway/Heroku sometimes provide postgres://; SQLAlchemy expects postgresql://
    if isinstance(base_uri, str) and base_uri.startswith("postgres://"):
        base_uri = "postgresql://" + base_uri[len("postgres://") :]
    sqlcipher_key = os.getenv("SQLCIPHER_KEY")

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLCIPHER_KEY"] = sqlcipher_key
    app.config["SQLALCHEMY_DATABASE_URI"] = base_uri

    if not sqlcipher_key:
        return

    if base_uri.startswith("sqlite:///"):
        try:
            __import__("pysqlcipher3")
        except ImportError as exc:
            raise RuntimeError(
                "SQLCIPHER_KEY is set but pysqlcipher3 is not installed. "
                "Install it or unset SQLCIPHER_KEY."
            ) from exc

        db_path = base_uri[len("sqlite:///"):]
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite+pysqlcipher:///{db_path}"
    else:
        # Non-sqlite URLs can still use DATABASE_URL directly.
        pass


def enable_sqlcipher_pragmas(app, db):
    """
    Attach SQLCipher PRAGMA keying for new DB connections.
    """
    key = app.config.get("SQLCIPHER_KEY")
    if not key:
        return

    if app.extensions.get("sqlcipher_pragmas_enabled"):
        return

    escaped_key = str(key).replace("'", "''")
    engine = db.engine

    @event.listens_for(engine, "connect")
    def _set_sqlcipher_key(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA key = '{escaped_key}'")
        cursor.execute("PRAGMA cipher_compatibility = 4")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    app.extensions["sqlcipher_pragmas_enabled"] = True
