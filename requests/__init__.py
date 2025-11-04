"""Lightweight requests shim for HALCYON tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class _Response:
    status_code: int = 200
    content: bytes = b""
    headers: Dict[str, str] | None = None
    _json: Any = None

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:  # pragma: no cover - trivial
        return self._json or {}


class Session:
    """Very small subset of :mod:`requests` Session."""

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: float | int = 0):  # pragma: no cover - stub
        raise RuntimeError("requests.Session.get is not implemented in the test shim")

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float | int = 0,
    ):
        raise RuntimeError("requests.Session.request is not implemented in the test shim")


def request(*args: Any, **kwargs: Any):  # pragma: no cover - stub
    raise RuntimeError("requests.request is not implemented in the test shim")


__all__ = ["Session", "request"]
