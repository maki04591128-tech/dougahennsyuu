from video_automation.mcp_servers.core_server import mcp


async def test_mcp_tool_registered_and_callable():
    tools = await mcp.list_tools()
    assert "ping" in [t.name for t in tools]
    result = await mcp.call_tool("ping", {})
    assert "ok" in str(result)
