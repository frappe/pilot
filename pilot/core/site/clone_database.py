from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from pilot.exceptions import BenchError
from pilot.utils import run_command


def stream_database(dump: list[str], restore: list[str], source_env: dict, target_env: dict) -> None:
    """Stream SQL without a dump file; neither side may fail silently."""
    processes = []
    try:
        producer = subprocess.Popen(dump, stdout=subprocess.PIPE, env=source_env)
        processes.append(producer)
        assert producer.stdout is not None
        try:
            consumer = subprocess.Popen(restore, stdin=producer.stdout, env=target_env)
            processes.append(consumer)
        finally:
            producer.stdout.close()
        imported = consumer.wait()
        dumped = producer.wait()
        if dumped or imported:
            raise BenchError(f"Database clone failed (dump exit {dumped}, import exit {imported}).")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class SiteDatabaseClone:
    def __init__(self, source, destination, config: dict) -> None:
        self.source = source
        self.destination = destination
        self.config = config

    def run(self) -> None:
        import json

        original = json.loads((self.source.path / "site_config.json").read_text())
        if self.config["db_type"] == "postgres":
            self.postgres(original)
        else:
            self.mariadb(original)

    def cleanup(self) -> None:
        """Drop only the generated destination database and its login."""
        name = self.config["db_name"]
        if not re.fullmatch(r"_[0-9a-f]{16}", name):
            raise BenchError("Refusing to clean up a database not generated for this clone.")
        if self.config["db_type"] == "postgres":
            self.postgres_admin_sql(f'DROP DATABASE IF EXISTS "{name}";\nDROP ROLE IF EXISTS "{name}";')
        else:
            from pilot.managers.database import MariaDBManager

            MariaDBManager(self.destination.bench.config.mariadb).run_admin_sql(
                f"DROP DATABASE IF EXISTS `{name}`;\nDROP USER IF EXISTS '{name}'@'%';"
            )

    def mariadb(self, original: dict) -> None:
        from pilot.managers.database import MariaDBManager

        manager = MariaDBManager(self.destination.bench.config.mariadb)
        name = self.config["db_name"]
        password = manager._sql_quote(self.config["db_password"])
        manager.run_admin_sql(
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER '{name}'@'%' IDENTIFIED BY {password};\n"
            f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{name}'@'%';"
        )
        source = self.mysql_args(original, self.source.bench)
        target = self.mysql_args(self.config, self.destination.bench)
        schema = self.get_parallel_schema(original, source)
        if schema:
            tables, sequences = schema
            with ThreadPoolExecutor(max_workers=4) as executor:
                jobs = [
                    executor.submit(
                        self.stream_mariadb,
                        original,
                        source,
                        target,
                        ("--no-data", "--skip-triggers", "--skip-add-drop-table"),
                        tables[index::4],
                    )
                    for index in range(4)
                ]
                for imported in jobs:
                    imported.result()
            self.stream_mariadb(
                original,
                source,
                target,
                ("--no-create-info", "--no-autocommit"),
            )
            if sequences:
                self.stream_mariadb(original, source, target, ("--no-data", "--skip-triggers"), sequences)
        else:
            self.stream_mariadb(original, source, target)

    @staticmethod
    def get_parallel_schema(original: dict, source: list[str]) -> tuple[list[str], list[str]] | None:
        query = (
            "SELECT (SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE()) + "
            "(SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()) + "
            "(SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "AND LOWER(COLUMN_DEFAULT) LIKE '%nextval%'); "
            "SELECT TABLE_NAME, TABLE_TYPE, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME;"
        )
        output = (
            run_command(
                [
                    "mariadb",
                    *source,
                    "--batch",
                    "--skip-column-names",
                    "--execute",
                    query,
                    "--",
                    original["db_name"],
                ],
                env={**os.environ, "MYSQL_PWD": original["db_password"]},
            )
            .stdout.decode()
            .splitlines()
        )
        if not output or output[0] != "0" or len(output) < 17:
            return None
        tables: list[str] = []
        sequences: list[str] = []
        for row in output[1:]:
            fields = row.split("\t")
            if (
                len(fields) != 3
                or fields[1] not in ("BASE TABLE", "SEQUENCE")
                or fields[2] not in ("InnoDB", "MyISAM")
                or "\\" in fields[0]
            ):
                return None
            (sequences if fields[1] == "SEQUENCE" else tables).append(fields[0])
        return (tables, sequences) if len(tables) >= 16 else None

    def stream_mariadb(self, original, source, target, options=(), tables=()) -> None:
        stream_database(
            [
                "mariadb-dump",
                *source,
                "--single-transaction",
                "--quick",
                "--skip-lock-tables",
                *options,
                "--",
                original["db_name"],
                *tables,
            ],
            ["mariadb", *target, "--", self.config["db_name"]],
            {**os.environ, "MYSQL_PWD": original["db_password"]},
            {**os.environ, "MYSQL_PWD": self.config["db_password"]},
        )

    @staticmethod
    def mysql_args(config: dict, bench) -> list[str]:
        args = ["--user", config.get("db_user") or config["db_name"]]
        socket = config.get("db_socket")
        if not socket and not (config.get("db_host") or config.get("db_port")):
            socket = bench.config.mariadb.socket_path
        if socket:
            return [*args, "--socket", socket]
        return [
            *args,
            "--host",
            config.get("db_host") or bench.config.mariadb.host,
            "--port",
            str(config.get("db_port") or bench.config.mariadb.port),
        ]

    def postgres(self, original: dict) -> None:
        from pilot.managers.database import PostgresManager

        settings = self.destination.bench.config.postgres
        psql = PostgresManager(settings).client_binary("psql")
        pg_dump = PostgresManager(self.source.bench.config.postgres).client_binary("pg_dump")
        name = self.config["db_name"]
        password = self.config["db_password"].replace("'", "''")
        self.postgres_admin_sql(
            f"CREATE ROLE {name} LOGIN PASSWORD '{password}';\nCREATE DATABASE {name} OWNER {name};"
        )
        source = self.pg_args(original, self.source.bench)
        target = self.pg_args(self.config, self.destination.bench)
        stream_database(
            [pg_dump, *source, "--no-owner", "--no-privileges"],
            [psql, *target, "-v", "ON_ERROR_STOP=1"],
            {**os.environ, "PGPASSWORD": original["db_password"]},
            {**os.environ, "PGPASSWORD": self.config["db_password"]},
        )

    def postgres_admin_sql(self, sql: str) -> None:
        from pilot.managers.database import PostgresManager

        settings = self.destination.bench.config.postgres
        admin = [
            PostgresManager(settings).client_binary("psql"),
            "-h",
            settings.host,
            "-p",
            str(settings.port),
            "-U",
            settings.admin_user,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        subprocess.run(
            admin,
            input=sql,
            text=True,
            check=True,
            env={**os.environ, "PGPASSWORD": settings.root_password},
        )

    @staticmethod
    def pg_args(config: dict, bench) -> list[str]:
        return [
            "-h",
            config.get("db_host") or bench.config.postgres.host,
            "-p",
            str(config.get("db_port") or bench.config.postgres.port),
            "-U",
            config.get("db_user") or config["db_name"],
            "-d",
            config["db_name"],
        ]
