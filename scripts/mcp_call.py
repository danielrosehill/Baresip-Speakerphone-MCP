"""Call a speakerphone MCP tool: uv run scripts/mcp_call.py <url> <tool> [json-args]."""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str, tool: str, args: dict) -> None:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            for c in res.content:
                print(getattr(c, "text", c))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}))
