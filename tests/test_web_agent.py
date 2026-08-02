"""The HTTP-facing agent must expose only read-only investment tools."""

from cli_agent import tools


def test_web_tool_allowlist_excludes_host_capabilities():
    names = {schema["name"] for schema in tools.SAFE_WEB_TOOL_SCHEMAS}
    assert names
    assert not names.intersection({"read_file", "write_file", "run_shell"})


def test_web_executor_rejects_non_allowlisted_tool():
    result = tools.execute_web_tool("read_file", {"path": "/etc/passwd"})
    assert result.startswith("錯誤")
