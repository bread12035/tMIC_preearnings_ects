"""Sync wrapper around google-cloud-pubsub PublisherClient.

Used by the task_dispatcher CronJob to publish trigger messages onto the
shared earnings-events topic. Subscriptions filter by the `event_type`
attribute to route messages to the pre-earnings or ECTS Deployment.
"""

from __future__ import annotations

import json
import logging

from google.cloud import pubsub_v1

log = logging.getLogger(__name__)


class SyncPublisher:
    """Thin sync wrapper around PublisherClient. Replaces AsyncPublisher."""

    def __init__(self, project_id: str, topic: str):
        self._client = pubsub_v1.PublisherClient()
        self._topic_path = self._client.topic_path(project_id, topic)

    def publish(self, data: dict, attributes: dict[str, str]) -> str:
        """Publish one message synchronously. Blocks until broker confirms."""
        encoded = json.dumps(data).encode("utf-8")
        future = self._client.publish(self._topic_path, encoded, **attributes)
        message_id = future.result()  # blocks; raises on failure
        log.info(
            "pubsub_published",
            extra={
                "topic": self._topic_path,
                "attrs": attributes,
                "message_id": message_id,
            },
        )
        return message_id


# Backwards-compatibility alias (nothing in the codebase imports AsyncPublisher
# after this migration, but keep it to avoid hard import errors during rollout)
AsyncPublisher = SyncPublisher
