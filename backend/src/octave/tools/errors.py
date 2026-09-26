"""Tool-plane errors (design specs #78, #79)."""

__all__ = ["ToolError", "ToolNameCollisionError", "ToolTranslationError"]


class ToolError(Exception):
    """Base for tool-plane failures (translation and orchestration)."""


class ToolTranslationError(ToolError):
    """Base for translation failures."""


class ToolNameCollisionError(ToolTranslationError):
    """Two tools landed on the same exposed name after sanitization."""
