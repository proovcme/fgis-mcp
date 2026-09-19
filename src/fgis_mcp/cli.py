import argparse
import hmac
import os

from . import __version__, jobs
from .config import Config
from .service import Service
from .storage import dump


class BearerMiddleware:
    def __init__(self, app, token):
        self.app, self.expected = app, ("Bearer " + token).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"www-authenticate", b"Bearer"), (b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def main():
    parser = argparse.ArgumentParser(description="Public FGIS CS MCP server and dataset downloader")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=[
            "serve",
            "diagnose",
            "download",
            "job",
            "cancel",
            "resume",
            "datasets",
            "export",
            "audit",
            "verify",
            "import",
            "import-opendata",
            "norm-history",
        ],
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--query", action="append")
    parser.add_argument("--collection", type=int, action="append")
    parser.add_argument("--source", action="append")
    parser.add_argument("--include-archive", action="store_true")
    parser.add_argument("--all-periods", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=25000)
    parser.add_argument("--zone", type=int)
    parser.add_argument("--period", type=int)
    parser.add_argument("--id")
    parser.add_argument("--file")
    parser.add_argument("--code")
    parser.add_argument("--snapshot")
    parser.add_argument("--edition")
    parser.add_argument("--note")
    parser.add_argument("--output")
    parser.add_argument("--format", action="append", choices=["jsonl", "parquet"])
    args = parser.parse_args()
    try:
        config = Config.from_env()
        service = Service(config)
        if args.command == "serve":
            from .server import create_server

            server = create_server(config)
            if args.transport == "stdio":
                server.run()
                return
            token = os.getenv("FGIS_HTTP_TOKEN", "")
            if args.host not in {"127.0.0.1", "::1", "localhost"} and len(token) < 32:
                parser.error("Non-loopback HTTP requires FGIS_HTTP_TOKEN (at least 32 characters)")
            import uvicorn

            app = server.streamable_http_app(host=args.host, stateless_http=True, json_response=True)
            if token:
                app = BearerMiddleware(app, token)
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
            return
        if args.command == "diagnose":
            result = service.network.diagnose()
        elif args.command == "download":
            if bool(args.zone) != bool(args.period):
                parser.error("--zone and --period must be provided together")
            books = [{"zone_id": args.zone, "period_id": args.period}] if args.zone else None
            result = jobs.start(
                config,
                args.query,
                args.collection,
                books,
                sources=args.source,
                include_archive=args.include_archive,
                all_periods=args.all_periods,
                max_tasks=args.max_tasks,
            )
        elif args.command in {"job", "cancel", "resume"}:
            if not args.id:
                parser.error("job requires --id")
            result = (
                jobs.launch(config, args.id, max_tasks=args.max_tasks)
                if args.command == "resume"
                else (jobs.cancel if args.command == "cancel" else jobs.status)(config, args.id)
            )
        elif args.command == "datasets":
            result = service.datasets()
        elif args.command == "verify":
            if not args.id:
                parser.error("verify requires --id")
            result = service.verify_dataset(args.id)
        elif args.command == "import":
            if not args.id:
                parser.error("import requires --id")
            if not args.file:
                parser.error("import requires --file")
            src = args.source[0] if args.source else "ter"
            result = service.import_manual_file(
                args.id, args.file, source=src, edition=args.edition, note=args.note
            )
        elif args.command == "import-opendata":
            if not args.file:
                parser.error("import-opendata requires --file <archive_path>")
            result = service.import_opendata_archive(args.file, dataset_id=args.id, snapshot_id=args.snapshot)
        elif args.command == "norm-history":
            if not args.code:
                parser.error("norm-history requires --code <norm_code>")
            result = service.norm_history(args.code, dataset_id=args.id)
        elif args.command == "audit":
            from pathlib import Path

            from . import catalogs
            from .network import SourceError
            from .storage import now

            audit_report = {
                "checked_at": now(),
                "scope": "root contracts and OpenData; non-intrusive smoke audit",
                "sources": [],
            }
            all_ok = True
            for task in catalogs.task_roots(["all_public"]):
                src = task["source"]
                try:
                    payload, _, meta = service.network.get_value(*catalogs.request(task))
                    child_tasks = catalogs.children(payload, task)
                    entry = {
                        "source": src,
                        "ok": True,
                        "root_items": len(catalogs.items_from(payload, task)),
                        "next_tasks": len(child_tasks),
                        **meta,
                    }
                except SourceError as exc:
                    entry = {"source": src, "ok": False, "error": exc.as_dict()}
                    all_ok = False
                audit_report["sources"].append(entry)

            result = audit_report
            if args.output:
                out_path = Path(args.output).expanduser().resolve()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(dump(audit_report), encoding="utf-8")
            if not all_ok:
                raise SystemExit(1)
        else:
            if not args.id:
                parser.error("export requires --id")
            result = service.export(args.id, args.format or ["jsonl", "parquet"])
        print(dump(result))
        if args.command == "diagnose" and not result["ok"]:
            raise SystemExit(1)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
