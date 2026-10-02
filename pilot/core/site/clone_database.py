from __future__ import annotations

import os
import re
import subprocess

from pilot.exceptions import BenchError


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
        stream_database(
            [
                "mariadb-dump",
                *source,
                "--single-transaction",
                "--quick",
                "--skip-lock-tables",
                original["db_name"],
            ],
            ["mariadb", *target, name],
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
