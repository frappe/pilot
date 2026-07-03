import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError, EndpointConnectionError

from pilot.config.s3_config import S3Config

# Non-seekable streams (e.g. a subprocess's stdin pipe) still get parallel
# range GETs: s3transfer buffers out-of-order parts and writes them in order.
_STREAM_TRANSFER = TransferConfig(multipart_chunksize=64 * 1024 * 1024, max_concurrency=8)

ENDPOINT_TEMPLATES = {
    "aws": "https://s3.{region}.amazonaws.com",
    "digitalocean": "https://{region}.digitaloceanspaces.com",
    "hetzner": "https://{region}.your-objectstorage.com",
}

PROVIDER_LABELS = {
    "aws": "Amazon S3",
    "digitalocean": "DigitalOcean Spaces",
    "hetzner": "Hetzner Object Storage",
}

SUPPORTED_REGIONS = {
    "aws": [
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-west-2",
        "eu-central-1",
        "ap-south-1",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
    ],
    "digitalocean": ["nyc3", "sfo3", "sgp1", "ams3", "fra1"],
    "hetzner": ["fsn1", "nbg1", "hel1"],
}


def build_endpoint_url(provider: str, region: str) -> str:
    try:
        return ENDPOINT_TEMPLATES[provider].format(region=region)
    except KeyError:
        raise ValueError(f"Unsupported provider: {provider}")


class S3IntegrationError(Exception):
    pass


@dataclass
class S3:
    access_key: str
    secret_key: str
    region_name: str
    provider: str
    bucket_name: str
    endpoint_url: str = field(init=False)
    client: BaseClient = field(init=False)

    def __post_init__(self):
        try:
            self.endpoint_url = build_endpoint_url(self.provider, self.region_name)
        except ValueError as error:
            raise S3IntegrationError(str(error)) from error
        addressing_style = "virtual" if self.provider == "aws" else "path"

        self.client = boto3.client(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            endpoint_url=self.endpoint_url,
            region_name=self.region_name,
            config=Config(signature_version="s3v4", s3={"addressing_style": addressing_style}),
        )

    @classmethod
    def from_config(cls, config: S3Config) -> "S3":
        """Connect using bench.toml's [s3] section, creating the bucket on first use."""
        if not config.is_configured:
            raise S3IntegrationError("S3 integration is not configured via settings")
        client = cls(
            config.access_key,
            config.secret_key,
            region_name=config.region,
            provider=config.provider,
            bucket_name=config.bucket,
        )
        client.create_bucket_if_not_present(config.bucket)
        return client

    def create_bucket_if_not_present(self, bucket_name: str) -> None:
        try:
            self.client.head_bucket(Bucket=bucket_name)
        except EndpointConnectionError as error:
            raise S3IntegrationError(f"Could not reach S3 endpoint '{self.endpoint_url}'. Check the endpoint URL and network access.") from error
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code == "404":
                self.create_bucket(bucket_name)
            elif code in ("401", "403"):
                raise S3IntegrationError(f"Access to bucket '{bucket_name}' was denied. Check the S3 credentials.") from error
            else:
                raise S3IntegrationError(f"Could not verify bucket '{bucket_name}': {error.response['Error'].get('Message', code)}") from error

    def create_bucket(self, bucket_name: str) -> None:
        try:
            if self.provider == "aws" and self.region_name != "us-east-1":
                self.client.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.region_name},
                )
            else:
                self.client.create_bucket(Bucket=bucket_name)
        except ClientError as error:
            raise S3IntegrationError(f"Failed to create bucket '{bucket_name}': {error.response['Error'].get('Message', error)}") from error

    def upload_file(self, bucket_name: str, local_path: Path, remote_key: str) -> None:
        try:
            self.client.upload_file(str(local_path), bucket_name, remote_key)
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to upload '{local_path.name}' to '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def upload_stream(self, bucket_name: str, remote_key: str, fileobj) -> None:
        """Multipart-uploads any readable file-like object — e.g. a subprocess's
        stdout pipe — without ever buffering the whole thing in memory or on disk."""
        try:
            self.client.upload_fileobj(fileobj, bucket_name, remote_key, Config=_STREAM_TRANSFER)
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to upload stream to '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def download_stream(self, bucket_name: str, remote_key: str, fileobj) -> None:
        """Streams an S3 object into any writable file-like object — e.g. a
        subprocess's stdin pipe — without ever buffering the whole thing in
        memory or on disk."""
        try:
            self.client.download_fileobj(bucket_name, remote_key, fileobj, Config=_STREAM_TRANSFER)
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to download stream from '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def download_file(self, bucket_name: str, remote_key: str, local_path: Path) -> None:
        try:
            self.client.download_file(bucket_name, remote_key, str(local_path))
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to download '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def presigned_url(self, bucket_name: str, remote_key: str, expires_in: int = 25_200) -> str:
        """A time-limited URL the caller can hand straight to a browser/curl —
        the download streams directly from S3, never through this server."""
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": remote_key},
                ExpiresIn=expires_in,
            )
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to create a download link for '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def delete_object(self, bucket_name: str, remote_key: str) -> None:
        try:
            self.client.delete_object(Bucket=bucket_name, Key=remote_key)
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to delete '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def list_objects(self, bucket_name: str, prefix: str) -> list[str]:
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
            return keys
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to list '{bucket_name}/{prefix}': {error.response['Error'].get('Message', error)}",
            ) from error

    def object_exists(self, bucket_name: str, remote_key: str) -> bool:
        try:
            self.client.head_object(Bucket=bucket_name, Key=remote_key)
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "404":
                return False
            raise S3IntegrationError(
                f"Failed to check '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def read_json(self, bucket_name: str, remote_key: str) -> Any:
        try:
            response = self.client.get_object(Bucket=bucket_name, Key=remote_key)
            return json.loads(response["Body"].read())
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to read '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error

    def write_json(self, bucket_name: str, remote_key: str, data: Any) -> None:
        try:
            self.client.put_object(
                Bucket=bucket_name,
                Key=remote_key,
                Body=json.dumps(data, indent=2).encode(),
                ContentType="application/json",
            )
        except ClientError as error:
            raise S3IntegrationError(
                f"Failed to write '{bucket_name}/{remote_key}': {error.response['Error'].get('Message', error)}",
            ) from error
