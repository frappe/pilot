import json
import sys

MISSING_DRIVER = 3


def connect_mariadb(settings):
    import pymysql

    return pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"] or None,
        unix_socket=settings["socket"],
        connect_timeout=settings["connect_timeout"],
    )


def connect_postgres(settings):
    """psycopg2 on Frappe up to v16, psycopg 3 after."""
    try:
        import psycopg2 as driver
    except ImportError:
        import psycopg as driver

    return driver.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        dbname=settings["database"] or settings["user"],
        connect_timeout=settings["connect_timeout"],
    )


def run(settings, query, params=()):
    """The rows of one read-only query."""
    connect = connect_mariadb if settings["engine"] == "mariadb" else connect_postgres
    connection = connect(settings)
    try:
        with connection.cursor() as cursor:
            if settings["engine"] == "mariadb":
                cursor.execute("START TRANSACTION READ ONLY")
            else:
                cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(query, tuple(params) or None)
            rows = [list(row) for row in cursor.fetchall()] if cursor.description else []
        connection.rollback()
        return rows
    finally:
        connection.close()


def main():
    """Run a query sent on stdin with the bench's database driver, for the stdlib-only CLI."""
    request = json.load(sys.stdin)
    try:
        rows = run(request["settings"], request["query"], request.get("params", []))
    except ImportError as exc:
        sys.stderr.write(str(exc))
        sys.exit(MISSING_DRIVER)
    json.dump(rows, sys.stdout, default=str)


if __name__ == "__main__":
    main()
