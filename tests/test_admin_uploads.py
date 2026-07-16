from __future__ import annotations

import io
import gzip
import stat
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

from pilot.tasks.manager.task_runner import TaskSubmission
from admin.backend.uploads import (
    UploadError,
    create_upload_directory,
    save_archive_upload,
    save_database_upload,
)
from pilot.config.bench_toml_builder import BenchTomlBuilder


def _tar_bytes() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        content = b"asset"
        member = tarfile.TarInfo("files/asset.txt")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def _unsafe_tar_bytes() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        content = b"escape"
        member = tarfile.TarInfo("../../escape.txt")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def _upload(content: bytes, filename: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(content), filename=filename)


def _client(bench_root: Path):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(
            bench_root.name,
            {"admin_enabled": True, "admin_password": "secret"},
        ).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_database_upload_uses_generated_contained_name(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    path = save_database_upload(
        _upload(b"-- backup\nCREATE TABLE tab (id int);", "../../outside.sql"), directory
    )

    assert path.parent == directory
    assert path.name != "outside.sql"
    assert path.suffix == ".sql"
    assert path.read_bytes().startswith(b"-- backup")
    assert not (tmp_path.parent / "outside.sql").exists()
    assert stat.S_IMODE((tmp_path / "tmp" / "uploads").stat().st_mode) == 0o700
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_database_upload_accepts_gzipped_sql(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    path = save_database_upload(
        _upload(gzip.compress(b"-- backup\nCREATE TABLE tab (id int);"), "backup.sql.gz"),
        directory,
    )

    assert path.name.endswith(".sql.gz")


def test_plain_database_validation_uses_bounded_read(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
        path = save_database_upload(
            _upload(b"-- backup\nCREATE TABLE tab (id int);", "backup.sql"),
            directory,
        )

    assert path.is_file()


def test_upload_directory_must_resolve_inside_bench(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-uploads"
    outside.mkdir()
    original_mode = stat.S_IMODE(outside.stat().st_mode)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "uploads").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UploadError, match="outside the bench"):
        create_upload_directory(tmp_path)

    assert stat.S_IMODE(outside.stat().st_mode) == original_mode


def test_database_upload_rejects_oversized_file(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    with pytest.raises(UploadError, match="exceeds"):
        save_database_upload(_upload(b"-- too large", "backup.sql"), directory, max_bytes=4)

    assert not any(directory.iterdir())


@pytest.mark.parametrize(
    ("content", "filename"),
    [
        (b"not sql", "backup.sql"),
        (b"PK\x03\x04not-a-database", "backup.sql.gz"),
    ],
)
def test_database_upload_rejects_wrong_content(
    tmp_path: Path, content: bytes, filename: str
) -> None:
    directory = create_upload_directory(tmp_path)

    with pytest.raises(UploadError, match="database backup"):
        save_database_upload(_upload(content, filename), directory)


def test_archive_upload_requires_tar_content(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    with pytest.raises(UploadError, match="tar archive"):
        save_archive_upload(_upload(b"PK\x03\x04zip", "public.tar"), directory, "public files")


def test_archive_upload_accepts_tar_and_generates_name(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    path = save_archive_upload(_upload(_tar_bytes(), "../../public.tar"), directory, "public files")

    assert path.parent == directory
    assert path.name != "public.tar"
    assert path.suffix == ".tar"


def test_archive_upload_rejects_unsafe_members(tmp_path: Path) -> None:
    directory = create_upload_directory(tmp_path)

    with pytest.raises(UploadError, match="unsafe path"):
        save_archive_upload(_upload(_unsafe_tar_bytes(), "public.tar"), directory, "public files")

    assert not any(directory.iterdir())


def test_site_restore_passes_only_generated_paths_to_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)

    with (
        patch(
            "admin.backend.views.sites.TaskRunner.submit",
            return_value=TaskSubmission("task-1", True),
        ) as submit,
        patch(
            "admin.backend.views.sites.accepted_task_response",
            return_value=({"task_id": "task-1"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/site-restores",
            data={
                "name": "new.localhost",
                "admin_password": "site-secret",
                "db_file": (
                    io.BytesIO(b"-- backup\nCREATE TABLE tab (id int);"),
                    "../../backup.sql",
                ),
                "public_files": (io.BytesIO(_tar_bytes()), "../../public.tar"),
            },
        )

    assert response.status_code == 202
    args = submit.call_args.args[1]
    upload_root = (bench_root / "tmp" / "uploads").resolve()
    for key in ("db_file", "public_files"):
        path = Path(args[key])
        assert path.resolve().is_relative_to(upload_root)
        assert ".." not in path.parts
    assert Path(args["db_file"]).name != "backup.sql"
    assert Path(args["public_files"]).name != "public.tar"
    assert args["admin_password"] == "site-secret"
    assert submit.call_args.kwargs["resource_key"] == "site:new.localhost"
    assert set(submit.call_args.kwargs["callbacks"]) == {
        "on_success",
        "on_failure",
        "on_cancel",
    }


def test_site_restore_generates_admin_password_when_omitted(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    with (
        patch(
            "admin.backend.views.sites.TaskRunner.submit",
            return_value=TaskSubmission("task-1", True),
        ) as submit,
        patch(
            "admin.backend.views.sites.accepted_task_response",
            return_value=({"task_id": "task-1"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/site-restores",
            data={
                "name": "new.localhost",
                "db_file": (io.BytesIO(b"-- backup\nCREATE TABLE tab (id int);"), "backup.sql"),
            },
        )

    assert response.status_code == 202
    assert response.get_json() == {"task_id": "task-1"}
    password = submit.call_args.args[1]["admin_password"]
    assert password and password != "admin"


def test_site_restore_replay_removes_duplicate_upload_directory(
    tmp_path: Path,
) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    request_data = {
        "name": "new.localhost",
        "db_file": (
            io.BytesIO(b"-- backup\nCREATE TABLE tab (id int);"),
            "backup.sql",
        ),
    }

    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        first = client.post(
            "/api/v1/site-restores",
            data=request_data,
            headers={"Idempotency-Key": "restore-request"},
        )
        replay = client.post(
            "/api/v1/site-restores",
            data={
                "name": "new.localhost",
                "db_file": (
                    io.BytesIO(b"-- backup\nCREATE TABLE tab (id int);"),
                    "another.sql",
                ),
            },
            headers={"Idempotency-Key": "restore-request"},
        )

    assert first.status_code == replay.status_code == 202
    first_body = first.get_json()
    assert first_body["task_id"] == replay.get_json()["task_id"]
    assert first.headers["Location"] == f"/api/v1/tasks/{first_body['task_id']}"
    assert first_body["args"] == {
        "name": "new.localhost",
        "admin_password": "[redacted]",
    }
    upload_directories = list((bench_root / "tmp" / "uploads").iterdir())
    assert len(upload_directories) == 1
