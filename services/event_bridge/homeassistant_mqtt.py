from __future__ import annotations

"""MQTT bridge utilities for Home Assistant integration."""

from typing import Any, Callable, Dict, Optional
import json
import logging
import ssl
import threading
import time

import paho.mqtt.client as mqtt


_LOGGER = logging.getLogger(__name__)


class MQTTConnectionError(RuntimeError):
    """Raised when an MQTT operation cannot be completed."""


class HAMQTTBridge:
    """Local-first MQTT bridge for Home Assistant.

    The bridge provides a minimal abstraction over ``paho-mqtt`` so that HALCYON
    modules can publish Home Assistant service call requests and receive
    asynchronous events.

    Attributes
    ----------
    host:
        Hostname or IP address of the MQTT broker.
    port:
        TCP port of the MQTT broker (defaults to ``1883`` for plain TCP).
    client_id:
        Identifier used when establishing the MQTT session.
    on_event:
        Optional callable invoked whenever an event message is received on the
        ``halcyon/ha/event/#`` topic hierarchy.
    """

    SERVICE_TOPIC = "halcyon/ha/call"
    EVENT_TOPIC = "halcyon/ha/event/#"

    def __init__(
        self,
        host: str,
        *,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls: bool | ssl.SSLContext = False,
        client_id: str = "halcyon-mqtt",
        keepalive: int = 30,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.keepalive = keepalive
        self.username = username
        self.password = password
        self._tls = tls
        self._on_event = on_event
        self._client = mqtt.Client(client_id=client_id, clean_session=True)
        if username and password:
            self._client.username_pw_set(username=username, password=password)
        if tls:
            if isinstance(tls, ssl.SSLContext):
                self._client.tls_set_context(tls)
            else:
                self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
                self._client.tls_insecure_set(False)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._connected = threading.Event()
        self._should_run = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    def start(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        """Connect to the MQTT broker and start the network loop."""

        with self._lock:
            if self._thread and self._thread.is_alive():
                _LOGGER.debug("HAMQTTBridge already running")
                return
            self._should_run.set()
            self._connected.clear()
            try:
                self._client.connect(self.host, self.port, keepalive=self.keepalive)
            except Exception as exc:  # pragma: no cover - depends on network
                raise MQTTConnectionError("Failed to connect to MQTT broker") from exc
            self._thread = threading.Thread(target=self._loop_forever, daemon=True)
            self._thread.start()
        if wait:
            if not self._connected.wait(timeout=timeout):
                raise MQTTConnectionError("Timed out waiting for MQTT connection")

    def stop(self) -> None:
        """Stop the background MQTT loop and disconnect the client."""

        with self._lock:
            self._should_run.clear()
            if self._client.is_connected():
                try:
                    self._client.disconnect()
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception("Error disconnecting from MQTT broker")
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None
            self._connected.clear()

    # ------------------------------------------------------------------
    # MQTT callbacks
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker %s:%s", self.host, self.port)
            self._connected.set()
            client.subscribe(self.EVENT_TOPIC, qos=1)
        else:  # pragma: no cover - depends on broker response
            _LOGGER.error("MQTT connection failed with code %s", rc)
            self._connected.clear()

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        self._connected.clear()
        if self._should_run.is_set():
            _LOGGER.warning("Unexpected MQTT disconnect (code=%s), retrying...", rc)
            self._schedule_reconnect()
        else:
            _LOGGER.info("MQTT client disconnected")

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        if not self._on_event:
            return
        payload: Dict[str, Any]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:  # pragma: no cover - defensive
            payload = {"raw": msg.payload.decode("utf-8", errors="ignore")}
        try:
            self._on_event(msg.topic, payload)
        except Exception:  # pragma: no cover - user callback
            _LOGGER.exception("Error in MQTT on_event callback")

    # ------------------------------------------------------------------
    # Command publishing
    def call_service(self, domain: str, service: str, data: Dict[str, Any]) -> bool:
        """Publish a Home Assistant service call request."""

        message = {
            "domain": domain,
            "service": service,
            "data": data,
            "ts": time.time(),
        }
        return self._publish_json(self.SERVICE_TOPIC, message, qos=1)

    def publish_note(self, topic_suffix: str, payload: Dict[str, Any], *, qos: int = 1) -> bool:
        """Publish an auxiliary message under the ``halcyon/`` namespace."""

        topic = f"halcyon/{topic_suffix.lstrip('/')}"
        return self._publish_json(topic, payload, qos=qos)

    def wait_until_connected(self, timeout: float | None = None) -> bool:
        """Block until the bridge reports an active connection."""

        return self._connected.wait(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    def _loop_forever(self) -> None:
        while self._should_run.is_set():
            try:
                self._client.loop_forever()
            except Exception:  # pragma: no cover - depends on network
                _LOGGER.exception("MQTT loop exited unexpectedly; retrying")
                time.sleep(1.0)
            if self._should_run.is_set() and not self._connected.is_set():
                try:
                    self._client.reconnect()
                except Exception:  # pragma: no cover - depends on network
                    _LOGGER.warning("Reconnection attempt failed", exc_info=True)
                    time.sleep(2.0)
            else:
                break

    def _publish_json(self, topic: str, payload: Dict[str, Any], *, qos: int) -> bool:
        data = json.dumps(payload, separators=(",", ":"))
        try:
            info = self._client.publish(topic, data, qos=qos, retain=False)
        except Exception:  # pragma: no cover - depends on network
            _LOGGER.exception("Failed to publish MQTT message to %s", topic)
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.error("MQTT publish to %s failed (rc=%s)", topic, info.rc)
            return False
        return True

    def _schedule_reconnect(self) -> None:
        def _reconnect() -> None:
            delay = 1.0
            while self._should_run.is_set() and not self._connected.is_set():
                try:
                    self._client.reconnect()
                    return
                except Exception:  # pragma: no cover - depends on network
                    _LOGGER.warning("MQTT reconnect attempt failed; retrying in %.1fs", delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)

        threading.Thread(target=_reconnect, daemon=True).start()


__all__ = ["HAMQTTBridge", "MQTTConnectionError"]
