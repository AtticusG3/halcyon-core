"""MQTT diagnostic publisher for HALCYON orchestrator."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt


class EventBus:
    """Publishes orchestrator telemetry to MQTT diagnostic topics."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_topic: str = "halcyon",
    ) -> None:
        self._base_topic = base_topic.rstrip("/")
        self._client = mqtt.Client(client_id="halcyon-eventbus", clean_session=True)
        if username and password:
            self._client.username_pw_set(username, password)
        self._client.connect(host, port, keepalive=25)
        self._client.loop_start()

    def publish(self, topic_suffix: str, payload: Dict[str, Any]) -> None:
        topic = f"{self._base_topic}/{topic_suffix.lstrip('/')}"
        message = payload.copy()
        message.setdefault("ts", time.time())
        try:
            self._client.publish(topic, json.dumps(message), qos=0, retain=False)
        except Exception:
            # Diagnostics should never break the core loop; failures are dropped.
            pass
