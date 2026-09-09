"""Controllers for explicit delivery and Snooze-owned App Server sessions.

The public names are loaded lazily so the v0.1 ``submit`` path does not pay
the startup cost of the optional App Server controller.
"""

__all__ = [
    "AgentController",
    "CompletionRouter",
    "DeliveryController",
    "NativeAppServerBackend",
    "ThreadRegistry",
]


def __getattr__(name: str):
    if name == "AgentController":
        from .agent import AgentController

        return AgentController
    if name == "CompletionRouter":
        from .completion_router import CompletionRouter

        return CompletionRouter
    if name == "DeliveryController":
        from .controller import DeliveryController

        return DeliveryController
    if name == "NativeAppServerBackend":
        from .native_backend import NativeAppServerBackend

        return NativeAppServerBackend
    if name == "ThreadRegistry":
        from .thread_registry import ThreadRegistry

        return ThreadRegistry
    raise AttributeError(name)
