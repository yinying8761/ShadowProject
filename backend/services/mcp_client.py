"""
MCP (Model Context Protocol) client for integrating external tools.
Placeholder for Phase 3 implementation.
"""


class MCPClient:
    """Manages connections to MCP servers and discovers tools."""

    def __init__(self):
        self._servers: dict[str, dict] = {}

    async def connect_stdio(self, name: str, command: str, args: list[str] | None = None):
        """Connect to an MCP server via stdio. Not yet implemented."""
        raise NotImplementedError("MCP stdio transport — Phase 3")

    async def connect_http(self, name: str, url: str):
        """Connect to an MCP server via HTTP. Not yet implemented."""
        raise NotImplementedError("MCP HTTP transport — Phase 3")

    async def discover_tools(self, server_name: str) -> list[dict]:
        """Discover tools from a connected MCP server."""
        raise NotImplementedError("MCP tool discovery — Phase 3")

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Call a tool on a remote MCP server."""
        raise NotImplementedError("MCP tool call — Phase 3")
