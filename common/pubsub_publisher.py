"""Async wrapper around google-cloud-pubsub PublisherClient.

Used by the task_dispatcher CronJob to publish trigger messages onto the
shared earnings-events topic. Subscriptions filter by the `event_type`
attribute to route messages to the pre-earnings or ECTS Deployment.
"""

from __future__ import annotations

import asyncio
import json
import logging

from google.cloud import pubsub_v1

log = logging.getLogger(__name__)


class AsyncPublisher:
    """Thin async wrapper around the (blocking) PublisherClient."""

    def __init__(self, project_id: str, topic: str):
        self._client = pubsub_v1.PublisherClient()
        self._topic_path = self._client.topic_path(project_id, topic)

    async def publish(self, data: dict, attributes: dict[str, str]) -> str:
        """Publish a single message and return its message id.

        The blocking publish().result() call is run in a thread executor so
        callers stay non-blocking.
        """
        encoded = json.dumps(data).encode("utf-8")
        loop = asyncio.get_running_loop()
        message_id = await loop.run_in_executor(
            None,
            lambda: self._client.publish(
                self._topic_path, encoded, **attributes
            ).result(),
        )
        log.info(
            "pubsub_published",
            extra={
                "topic": self._topic_path,
                "attrs": attributes,
                "message_id": message_id,
            },
        )
        return message_id
