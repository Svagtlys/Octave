"""Translation-layer errors (design spec #78)."""

__all__ = ["ToolNameCollisionError", "ToolTranslationError"]


class ToolTranslationError(Exception):
    """Base for tool translation failures."""


class ToolNameCollisionError(ToolTranslationError):
    """Two tools landed on the same exposed name after sanitization."""
