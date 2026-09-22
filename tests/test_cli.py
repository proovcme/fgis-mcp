import json
import sys

from fgis_mcp import cli


def _run_cli(monkeypatch, capsys, service, *args):
    monkeypatch.setattr(cli.Config, "from_env", staticmethod(lambda: object()))
    monkeypatch.setattr(cli, "Service", lambda config: service)
    monkeypatch.setattr(sys, "argv", ["fgis-mcp", *args])
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_norm_history_cli_passes_family_and_incomplete_flag(monkeypatch, capsys):
    calls = []

    class Service:
        def norm_history(self, code, dataset_id=None, family=None, include_incomplete=False):
            calls.append((code, dataset_id, family, include_incomplete))
            return {"ok": True}

    result = _run_cli(
        monkeypatch,
        capsys,
        Service(),
        "norm-history",
        "--id",
        "dataset-1",
        "--code",
        "17-01-001-01",
        "--family",
        "ГЭСН",
        "--include-incomplete",
    )

    assert result == {"ok": True}
    assert calls == [("17-01-001-01", "dataset-1", "ГЭСН", True)]


def test_export_cli_passes_incomplete_flag(monkeypatch, capsys):
    calls = []

    class Service:
        def export(self, dataset_id, formats, include_incomplete=False):
            calls.append((dataset_id, formats, include_incomplete))
            return {"files": []}

    result = _run_cli(
        monkeypatch,
        capsys,
        Service(),
        "export",
        "--id",
        "dataset-1",
        "--format",
        "jsonl",
        "--include-incomplete",
    )

    assert result == {"files": []}
    assert calls == [("dataset-1", ["jsonl"], True)]
