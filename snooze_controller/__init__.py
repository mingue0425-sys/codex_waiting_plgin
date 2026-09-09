"""Explicit completion delivery controllers for Codex Snooze.

The first controller is a durable manual-file transport.  Codex App Server
delivery remains feature-gated until the capability probe proves a safe target
thread and acknowledgement protocol.
"""

__all__ = ["DeliveryController"]

from .controller import DeliveryController
