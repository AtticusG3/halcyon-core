"""HALCYON orchestrator package with lazy re-exports."""

__all__ = [
    "EventBus",
    "MessageRouter",
    "Orchestrator",
    "OrchestratorDependencies",
    "RouterConfig",
    "SessionStore",
]


def __getattr__(name: str):  # pragma: no cover - simple proxy
    if name in {"EventBus"}:
        from .logging.event_bus import EventBus

        return EventBus
    if name in {"MessageRouter", "RouterConfig"}:
        from .routing.message_router import MessageRouter, RouterConfig

        return {"MessageRouter": MessageRouter, "RouterConfig": RouterConfig}[name]
    if name in {"SessionStore"}:
        from .context.session_state import SessionStore

        return SessionStore
    if name in {"Orchestrator", "OrchestratorDependencies"}:
        from .orchestrator import Orchestrator, OrchestratorDependencies

        return {"Orchestrator": Orchestrator, "OrchestratorDependencies": OrchestratorDependencies}[name]
    raise AttributeError(name)
