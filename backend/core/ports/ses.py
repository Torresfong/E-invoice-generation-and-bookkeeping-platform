"""The real `EmailSender`, over the Amazon SES API (#151, D14, D53 (3)).

The API, not SMTP: SES has no SMTP endpoint in ap-southeast-5. Credentials are never read here: boto3 finds them
itself (the task's IAM role in production), so none is in the repository, a setting or a log.

A message body holds a single-use link, so nothing here logs a body, a subject or an address, and a failure is logged
by its error class and code only. The exception raised carries no cause either, since Django logs a 500's whole chain.

Idempotency. SES has no idempotency key, so the adapter keeps the keys itself, as the port contract says it must. It
does that **in memory, per process**: a repeat within one process sends once, but a worker that crashes after SES
accepted a message and is then run again as a new process has forgotten the key and sends a second copy. That is the
limit; closing it needs a table of sent keys, which is a tenancy decision (a company column, RLS) left to the first
ticket that sends mail from a job (#12). Today's callers (sign-up, invitation, reset) are web requests and pass no key.
A second thread that repeats a key while the first send is still in flight returns at once, and if that send then fails
only the first caller sees the error; a job that is run again after its failure sends, because the failure freed the key.
"""

import logging
import threading

from .interfaces import IdempotencyKeyMissing

logger = logging.getLogger(__name__)


class EmailSendFailed(RuntimeError):
    """The mail service did not accept the message. The caller's request rolls back (D53 (3))."""


class SesEmail:
    def __init__(self, client, sender):
        self._client = client
        self._sender = sender
        self._keys = set()  # keys sent or being sent by this process
        self._lock = threading.Lock()

    def send(self, to, subject, body, idempotency_key=None):
        from core import jobs  # not at import: the jobs module does not depend on the ports package

        if idempotency_key is None and jobs.in_job():
            raise IdempotencyKeyMissing("a job made an external call without its idempotency key")
        if idempotency_key is not None:
            with self._lock:
                if idempotency_key in self._keys:
                    return
                self._keys.add(idempotency_key)  # before the call, so two threads cannot both send
        try:
            self._client.send_email(
                FromEmailAddress=self._sender,
                Destination={"ToAddresses": [to]},
                Content={
                    "Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                    }
                },
            )
        except Exception as error:
            if idempotency_key is not None:
                with self._lock:
                    self._keys.discard(idempotency_key)  # a failed send leaves the key free for the retry
            code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
            logger.error("Email send failed: %s %s", type(error).__name__, code)
            raise EmailSendFailed(f"the mail service did not accept the message ({type(error).__name__} {code})") from None


def ses_client(region):
    """The real SES v2 client. boto3 is imported here so nothing else needs it, and nothing connects until a send."""
    import boto3
    from botocore.config import Config

    # botocore logs each request's parameters at DEBUG, and the body holds a single-use link.
    logging.getLogger("botocore").setLevel(logging.INFO)
    # A web request waits on the send, so give up in seconds, as the gateway does. No automatic retry: one after a read
    # timeout could deliver the message twice, and the caller's request fails and is tried again as a whole.
    config = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
    return boto3.client("sesv2", region_name=region, config=config)


def email_from_settings(sender, region, make_client=ses_client):
    """The adapter, or None when no sender address is configured (a deployment that has not set mail up)."""
    if not sender:
        return None
    return SesEmail(make_client(region), sender)
