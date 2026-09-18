import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_stdio_discovery_and_offline_call(tmp_path, mode):
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "fgis_mcp.cli"],
            env={**os.environ, "FGIS_DATA_DIR": str(tmp_path), "FGIS_NETWORK": "direct"},
        )
        async with Client(params, mode=mode) as client:
            listed = await client.list_tools()
            assert {"fgis_sources", "fgis_start_download", "fgis_query_dataset"}.issubset(
                {t.name for t in listed.tools}
            )
            result = await client.call_tool("fgis_list_datasets", {})
            assert not result.is_error
            assert json.loads(result.content[0].text)["items"] == []
            resource = await client.read_resource("fgis://help")
            assert resource.contents
            bad = await client.call_tool("fgis_dataset_info", {"dataset_id": "../outside"})
            assert bad.is_error

    asyncio.run(run())


def test_http_discovery_and_bearer_guard(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = "test-only-" + "x" * 40
    env = {**os.environ, "FGIS_DATA_DIR": str(tmp_path), "FGIS_HTTP_TOKEN": token}
    process = subprocess.Popen(
        [sys.executable, "-m", "fgis_mcp.cli", "--transport", "streamable-http", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        ready = False
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=0.2)
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
                ready = True
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        assert ready, "HTTP server did not start"

        async def run():
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, trust_env=False
            ) as http:
                async with streamable_http_client(url, http_client=http) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        result = await client.call_tool("fgis_list_datasets", {})
                        assert not result.is_error

        asyncio.run(run())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
