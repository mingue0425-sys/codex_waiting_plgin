"""Optional hook boundary; automatic interception is disabled by the gate."""

from .hooks import pre_tool_use

__all__ = ["pre_tool_use"]
