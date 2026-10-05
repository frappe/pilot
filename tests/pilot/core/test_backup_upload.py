from __future__ import annotations

import io
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pilot.core.bench.uploads import MANIFEST_NAME, STALE_SECONDS, BackupUpload
from pilot.exceptions import BenchError, UploadOffsetError


def _bench(tmp_path: Path) -> MagicMock:
    bench = MagicMock()
    bench.uploads_path = tmp_path / "tmp" / "uploads"
    return bench


def _upload(tmp_path: Path, size: int = 10) -> BackupUpload:
    files = {"database": {"filename": "site-database.sql.gz", "size": size}}
    return BackupUpload.start(_bench(tmp_path), "a.localhost", files)


def _send(upload: BackupUpload, offset: int, data: bytes) -> int:
    return upload.write_chunk("database", offset, io.BytesIO(data), len(data))


def test_chunks_in_order_build_the_file_under_a_name_the_restore_recognises(tmp_path: Path) -> None:
    upload = _upload(tmp_path)

    _send(upload, 0, b"hello")
    assert _send(upload, 5, b"world") == 10

    assert (upload.path / "upload-database.sql.gz").read_bytes() == b"helloworld"
    assert upload.is_complete


def test_a_chunk_sent_again_rewrites_from_its_offset(tmp_path: Path) -> None:
    upload = _upload(tmp_path)
    _send(upload, 0, b"hello")
    _send(upload, 5, b"xxxxx")

    _send(upload, 5, b"world")

    assert (upload.path / "upload-database.sql.gz").read_bytes() == b"helloworld"


def test_a_chunk_past_the_received_bytes_asks_the_client_to_resume(tmp_path: Path) -> None:
    upload = _upload(tmp_path)
    _send(upload, 0, b"hello")

    with pytest.raises(UploadOffsetError) as error:
        _send(upload, 8, b"ab")

    assert error.value.received == 5


def test_a_chunk_beyond_the_announced_size_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="does not fit"):
        _send(_upload(tmp_path, size=4), 0, b"hello")


def test_a_chunk_that_ends_early_keeps_what_arrived_and_resumes_after_it(tmp_path: Path) -> None:
    # A dropped connection must not cost the part of a 256 MB chunk that already arrived.
    upload = _upload(tmp_path)
    _send(upload, 0, b"hello")

    with pytest.raises(UploadOffsetError) as error:
        upload.write_chunk("database", 5, io.BytesIO(b"wo"), 5)

    assert error.value.received == 7
    assert upload.files["database"]["received"] == 7
    assert _send(upload, 7, b"rld") == 10


def test_an_incomplete_upload_cannot_be_claimed_by_a_restore(tmp_path: Path) -> None:
    upload = _upload(tmp_path)
    _send(upload, 0, b"hello")

    with pytest.raises(BenchError, match="Upload all the files"):
        upload.claim(["database"])


def test_a_claimed_upload_belongs_to_the_restore_and_is_not_cancelled(tmp_path: Path) -> None:
    upload = _upload(tmp_path, size=5)
    _send(upload, 0, b"hello")

    path = upload.claim(["database"])
    upload.delete()

    assert (path / "upload-database.sql.gz").exists()
    assert not (path / MANIFEST_NAME).exists()


def test_a_new_upload_removes_uploads_idle_for_three_hours_but_not_claimed_ones(tmp_path: Path) -> None:
    idle = _upload(tmp_path)
    claimed = _upload(tmp_path, size=5)
    _send(claimed, 0, b"hello")
    claimed.claim(["database"])
    old = time.time() - STALE_SECONDS - 60
    for directory in (idle.path, claimed.path):
        for path in [directory, *directory.iterdir()]:
            os.utime(path, (old, old))

    _upload(tmp_path)

    assert not idle.path.exists()
    assert claimed.path.exists()


def test_an_upload_larger_than_the_free_disk_space_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="free disk space"):
        _upload(tmp_path, size=10**18)


def test_an_upload_id_that_is_not_ours_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="does not exist"):
        BackupUpload(_bench(tmp_path), "../../etc")


def test_a_chunk_for_an_upload_handed_to_a_restore_is_refused(tmp_path: Path) -> None:
    upload = _upload(tmp_path, size=5)
    _send(upload, 0, b"hello")
    upload.claim(["database"])

    with pytest.raises(BenchError, match="does not exist"):
        _send(upload, 0, b"HELLO")

    assert (upload.path / "upload-database.sql.gz").read_bytes() == b"hello"
