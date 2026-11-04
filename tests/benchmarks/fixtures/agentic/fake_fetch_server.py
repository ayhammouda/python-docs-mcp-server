"""A stand-in for ``mcp-server-fetch`` in the agentic tests: stdio MCP, no network.

``--edge`` adds tools the bridge and host tests use to observe the server.
"""

import os
import sys

from mcp.server import MCPServer

server = MCPServer("fake-fetch")


@server.tool()
def fetch(url: str, max_length: int = 5000, start_index: int = 0, raw: bool = False) -> str:
    """Fetch a URL and return its contents (fixed text here)."""
    return f"Contents of {url}: the json module encodes and decodes JSON."


if "--edge" in sys.argv:

    @server.tool()
    def env_names() -> list[str]:
        """The sorted names in this process's environment."""
        return sorted(os.environ)

    @server.tool()
    def die() -> str:
        """Exit at once, mid-request."""
        os._exit(1)

    @server.tool()
    def pid() -> int:
        """This server's process id."""
        return os.getpid()


if __name__ == "__main__":
    server.run()
