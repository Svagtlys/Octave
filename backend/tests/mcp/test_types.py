"""Octave MCP domain types — the only vocabulary callers see."""

import pytest
from pydantic import ValidationError

from octave.mcp.types import (
    Notification,
    ServerInfo,
    ToolContent,
    ToolInfo,
    ToolResult,
)


def test_server_info_requires_all_fields() -> None:
    info = ServerInfo(name="s", version="1.0", protocol_version="2025-06-18")
    assert info.name == "s"
    with pytest.raises(ValidationError):
        ServerInfo(name="s", version="1.0")  # type: ignore[call-arg]


def test_tool_info_defaults() -> None:
    tool = ToolInfo(name="add", input_schema={"type": "object"})
    assert tool.description is None


def test_tool_content_rejects_non_text_kind() -> None:
    with pytest.raises(ValidationError):
        ToolContent(kind="image", text="x")  # type: ignore[arg-type]


def test_tool_result_is_error_defaults_false() -> None:
    assert ToolResult(content=[]).is_error is False


def test_notification_params_default_empty() -> None:
    assert Notification(method="notifications/progress").params == {}
    assert Notification(method="m", params={"a": 1}).params == {"a": 1}
