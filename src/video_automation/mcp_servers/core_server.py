"""MCPテストサーバー (P1-10)。MCPは内部サービスを呼ぶアダプター。"""

from mcp.server.fastmcp import FastMCP

from video_automation.app.settings import APP_NAME, APP_VERSION

mcp = FastMCP("mcp_core")


def ping() -> dict[str, str]:
    return {"status": "ok", "application": APP_NAME, "version": APP_VERSION}


mcp.tool(name="ping", description="疎通確認用テストツール")(ping)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
