"""Chat log of the deployed beta, kept as a suggestion box (BETA_CHAT_LOG).

The task's disk is lost on every night stop and rollout, so each line goes to the objects
bucket as its own small object (S3 cannot append). It lives under feedback/, outside the
owners' tenants/ storage: it is not counted in their quota, not part of their backups, and
stays when an owner is deleted. Read it with tools/chat_feedback.
"""
from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "feedback/chat/"
# Writing must never hold up or fail the chat answer.
_writer = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat-feedback")


class ChatFeedback:
    def __init__(self, client: Any, bucket: str, installation: str, tenant: str,
                 subject: str) -> None:
        self.client, self.bucket = client, bucket
        self.installation, self.tenant, self.subject = installation, tenant, subject

    def __call__(self, line: dict[str, Any]) -> None:
        line = {**line, "tenant": self.tenant, "subject": self.subject}
        at = datetime.now(UTC)
        key = (
            f"{PREFIX}{self.installation}/{at:%Y-%m-%d}/"
            f"{at:%H%M%S%f}-{self.tenant[:8]}-{uuid.uuid4().hex[:8]}.json"
        )
        body = json.dumps(line, ensure_ascii=False, default=str).encode()
        _writer.submit(self._put, key, body)

    def _put(self, key: str, body: bytes) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=body,
                ContentType="application/json", ServerSideEncryption="AES256",
            )
        except Exception:
            logger.exception("Chat log line could not be saved")
