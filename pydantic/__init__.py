"""Minimal stub of the :mod:`pydantic` package for test execution.

This lightweight implementation covers only the features used within the
repository, providing enough surface area for the unit tests to import the
runtime modules without installing the real dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

__all__ = [
    "BaseModel",
    "Field",
    "FieldInfo",
    "NonNegativeFloat",
    "PositiveInt",
    "validator",
]


class _Missing:
    pass


MISSING = _Missing()


@dataclass
class FieldInfo:
    """Container describing default values supplied to :func:`Field`."""

    default: Any = MISSING
    default_factory: Optional[Callable[[], Any]] = None

    def get_default(self) -> Any:
        if self.default_factory is not None:
            return self.default_factory()
        if self.default is not MISSING:
            return self.default
        raise ValueError("Missing required field")


def Field(
    default: Any = MISSING,
    *,
    default_factory: Optional[Callable[[], Any]] = None,
    **_: Any,
) -> FieldInfo:
    """Return a :class:`FieldInfo` describing default configuration."""

    if default is Ellipsis:
        default = MISSING
    return FieldInfo(default=default, default_factory=default_factory)


class BaseModel:
    """Very small subset of :class:`pydantic.BaseModel` semantics."""

    def __init__(self, **data: Any) -> None:
        fields = self._collect_fields()
        for name, default in fields.items():
            if name in data:
                value = data.pop(name)
            else:
                value = self._resolve_default(default, field=name)
            setattr(self, name, value)
        for key, value in data.items():
            setattr(self, key, value)

    @classmethod
    def _collect_fields(cls) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            annotations = getattr(base, "__annotations__", {}) or {}
            for name in annotations:
                if name not in fields:
                    fields[name] = getattr(base, name, MISSING)
        return fields

    @staticmethod
    def _resolve_default(default: Any, *, field: str) -> Any:
        if isinstance(default, FieldInfo):
            return default.get_default()
        if default is MISSING or default is Ellipsis:
            raise ValueError(f"Missing required field: {field}")
        if callable(default):
            return default()
        return default

    def dict(self, *, exclude_none: bool = False) -> Dict[str, Any]:
        result = {key: getattr(self, key) for key in self.__dict__}
        if exclude_none:
            result = {k: v for k, v in result.items() if v is not None}
        return result


def validator(*_args: Any, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator stub that leaves the wrapped function unchanged."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return decorator


# Simple aliases used for type annotations.
NonNegativeFloat = float
PositiveInt = int
