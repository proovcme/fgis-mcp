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
            assert {
                "fgis_sources",
                "fgis_start_download",
                "fgis_query_dataset",
                "fgis_read_document",
                "fgis_search_document",
                "fgis_document_outline",
                "fgis_read_document_table",
                "fgis_compare_norms",
                "fgis_extract_coefficients",
                "fgis_price_history",
                "fgis_verify_dataset",
                "fgis_import_manual_file",
                "fgis_opendata_list",
                "fgis_opendata_get",
                "fgis_norm_history",
                "fgis_compare_snapshots",
                "fgis_import_opendata",
            }.issubset({t.name for t in listed.tools})
            result = await client.call_tool("fgis_list_datasets", {})
            assert not result.is_error
            assert json.loads(result.content[0].text)["items"] == []
            opendata_res = await client.call_tool("fgis_opendata_list", {})
            assert not opendata_res.is_error
            assert json.loads(opendata_res.content[0].text)["items"]
            resource = await client.read_resource("fgis://help")
            assert resource.contents
            bad = await client.call_tool("fgis_dataset_info", {"dataset_id": "../outside"})
            assert bad.is_error

    asyncio.run(run())


def test_online_document_calls_over_mcp_without_dataset(config, monkeypatch):
    import hashlib

    from fgis_mcp.server import create_server

    class Network:
        def __init__(self, config):
            pass

        def get_value(self, path, *, large=False):
            payload = {
                "name": "Тест",
                "fullPublishedText": "<p>Коэффициент</p><table><tr><td>1,15</td></tr></table>",
            }
            raw = json.dumps(payload).encode()
            return (
                payload,
                raw,
                {"sha256": hashlib.sha256(raw).hexdigest(), "source_url": "https://example.invalid"},
            )

    monkeypatch.setattr("fgis_mcp.service.Network", Network)

    async def run():
        async with Client(create_server(config)) as client:
            args = {"document_guid": "11111111-1111-4111-8111-111111111111"}
            read = await client.call_tool("fgis_read_document", args)
            assert not read.is_error
            doc = json.loads(read.content[0].text)
            args["expected_sha256"] = doc["provenance"]["sha256"]
            for name, extra in [
                ("fgis_search_document", {"query": "коэффициент"}),
                ("fgis_document_outline", {}),
                ("fgis_read_document_table", {"table_index": 0}),
            ]:
                result = await client.call_tool(name, args | extra)
                assert not result.is_error
                assert json.loads(result.content[0].text)["cache_hit"]
            assert list(config.root.iterdir()) == []

    asyncio.run(run())


def test_operational_failures_are_structured_at_mcp_boundary(config, monkeypatch):
    from fgis_mcp.network import SourceError
    from fgis_mcp.server import create_server

    class Network:
        def __init__(self, config):
            pass

        def get_json(self, path, params=None):
            raise SourceError("NETWORK_ERROR", "FGIS connection failed (TimeoutError)")

    monkeypatch.setattr("fgis_mcp.service.Network", Network)

    async def run():
        async with Client(create_server(config)) as client:
            network_result = await client.call_tool("fgis_search_norms", {"query": "кабель"})
            assert not network_result.is_error
            network_data = json.loads(network_result.content[0].text)
            assert network_data == {
                "status": "NETWORK_ERROR",
                "error_code": "NETWORK_ERROR",
                "error": "FGIS connection failed (TimeoutError)",
                "message": "FGIS connection failed (TimeoutError)",
                "http_status": None,
                "retryable": True,
            }

            history_result = await client.call_tool("fgis_norm_history", {"code": "01-01-001-01"})
            assert not history_result.is_error
            history_data = json.loads(history_result.content[0].text)
            assert history_data["status"] == "LOCAL_DATASET_INCOMPLETE"
            assert history_data["error_code"] == "LOCAL_DATASET_INCOMPLETE"
            assert history_data["retryable"] is False

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
