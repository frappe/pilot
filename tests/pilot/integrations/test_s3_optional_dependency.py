"""boto3 stays optional until constructing an S3 client."""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest


@pytest.fixture
def without_boto3(monkeypatch: pytest.MonkeyPatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3.") or name == "botocore" or name.startswith("botocore."):
            raise ImportError(f"mocked missing: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for name in list(sys.modules):
        if name.startswith(("boto3", "botocore", "pilot.integrations.s3")):
            monkeypatch.delitem(sys.modules, name, raising=False)

    module = importlib.import_module("pilot.integrations.s3.base")
    try:
        yield importlib.reload(module)
    finally:
        for name in list(sys.modules):
            if name.startswith(("pilot.integrations.s3",)):
                monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.import_module("pilot.integrations.s3.base")


def test_module_imports_without_boto3(without_boto3) -> None:
    assert without_boto3.boto3 is None
    assert without_boto3._STREAM_TRANSFER is None


def test_constructing_s3_without_boto3_raises_a_clear_error(without_boto3) -> None:
    with pytest.raises(RuntimeError, match="boto3 is not installed"):
        without_boto3.S3(
            access_key="a",
            secret_key="b",
            region_name="us-east-1",
            provider="aws",
            bucket_name="x",
        )
