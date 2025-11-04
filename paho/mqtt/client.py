"""Simplified MQTT client stub compatible with HALCYON orchestrator tests."""
from __future__ import annotations

from typing import Any, Callable, Optional


class Client:
    """Extremely small subset of the Paho MQTT client interface."""

    def __init__(self, client_id: str | None = None, clean_session: bool = True) -> None:
        self.client_id = client_id
        self.clean_session = clean_session
        self.on_connect: Optional[Callable[..., Any]] = None
        self.on_message: Optional[Callable[..., Any]] = None
        self.on_disconnect: Optional[Callable[..., Any]] = None

    # Credential helpers -------------------------------------------------
    def username_pw_set(self, username: str, password: str) -> None:  # pragma: no cover - trivial
        self._username = username
        self._password = password

    def tls_set(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - placeholder
        self._tls_args = (args, kwargs)

    # Network lifecycle --------------------------------------------------
    def connect(self, host: str, port: int, keepalive: int) -> None:  # pragma: no cover - placeholder
        self._connection = (host, port, keepalive)

    def loop_forever(self) -> None:  # pragma: no cover - placeholder
        return None

    def loop_start(self) -> None:  # pragma: no cover - placeholder
        return None

    # Messaging ----------------------------------------------------------
    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> bool:
        self._last_published = (topic, payload, qos, retain)
        return True

    def subscribe(self, topic: str, qos: int = 0) -> bool:  # pragma: no cover - placeholder
        self._last_subscription = (topic, qos)
        return True

    def disconnect(self) -> None:  # pragma: no cover - placeholder
        return None
