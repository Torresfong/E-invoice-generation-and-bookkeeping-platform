"""The real `ObjectStorage`, over Amazon S3 (ticket #14, D14, D53 (3)).

Credentials are never read here: boto3 finds them itself (the task's IAM role in production). A failure is logged
by its error class and code only, never a key or a bucket's content.
"""

import logging

logger = logging.getLogger(__name__)


class ObjectStorageCallFailed(RuntimeError):
    """S3 did not accept the call. Raised from a job (`core.documents`), so ADR 0001's retry and backoff apply."""


class S3Storage:
    def __init__(self, client, bucket):
        self._client = client
        self._bucket = bucket

    def put(self, key, data):
        self._call("write", self._client.put_object, Bucket=self._bucket, Key=key, Body=data)

    def get(self, key):
        response = self._call("read", self._client.get_object, Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def _call(self, action, operation, **kwargs):
        try:
            return operation(**kwargs)
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
            logger.error("Object storage %s failed: %s %s", action, type(error).__name__, code)
            raise ObjectStorageCallFailed(f"S3 did not accept the {action} ({type(error).__name__} {code})") from None


def s3_client(region):
    """The real S3 client. boto3 is imported here so nothing else needs it, and nothing connects until a call."""
    import boto3
    from botocore.config import Config

    logging.getLogger("botocore").setLevel(logging.INFO)
    # A job waits on this, not a web request, but still should not hang indefinitely; the job queue's own retry and
    # backoff (ADR 0001) cover a failure, so boto3 is given none of its own.
    config = Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1})
    return boto3.client("s3", region_name=region, config=config)


def storage_from_settings(bucket, region, make_client=s3_client):
    """The adapter, or None when no bucket is configured (a deployment that has not set storage up)."""
    if not bucket:
        return None
    return S3Storage(make_client(region), bucket)
