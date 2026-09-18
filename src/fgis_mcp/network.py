"""Bounded, explicit routes. Direct disables proxies, not OS VPN routing."""

import hashlib
import json
import shlex
import ssl
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

import truststore

from .config import Config

HOST = "fgiscs.minstroyrf.ru"
BASE = f"https://{HOST}/api/"


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(message)

    def as_dict(self):
        return {"code": self.code, "message": str(self), "http_status": self.status}


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in {None, 443}:
            raise SourceError("REDIRECT_BLOCKED", "FGIS redirected outside its HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Network:
    def __init__(self, config: Config):
        self.config = config
        proxy = {"http": config.proxy, "https": config.proxy} if config.network == "proxy" else {}
        handler = ProxyHandler() if config.network == "system" else ProxyHandler(proxy)
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.opener = build_opener(handler, HTTPSHandler(context=context), SameHostRedirect())
        self._lock = threading.Lock()
        self._last = 0.0

    def _pace(self):
        with self._lock:
            time.sleep(max(0, self.config.interval - (time.monotonic() - self._last)))
            self._last = time.monotonic()

    def _ssh(self, url: str, timeout: float, limit: int) -> bytes:
        # No shell on this host; remote command is constructed only from quoted fixed arguments.
        command = shlex.join(
            [
                "curl",
                "--silent",
                "--show-error",
                "--noproxy",
                "*",
                "--proto",
                "=https",
                "--max-time",
                str(timeout),
                "--max-filesize",
                str(limit),
                "--write-out",
                "\n%{http_code}",
                url,
            ]
        )
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.config.ssh_host, command],
                capture_output=True,
                timeout=timeout + 12,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceError(
                "SSH_FAILED", "SSH unavailable or timed out; check your configured host"
            ) from exc
        if result.returncode != 0:
            # Never return remote stderr: it may contain credentials, hostnames or proxy URLs.
            raise SourceError("SSH_FAILED", "SSH/curl failed; verify SSH access, curl and remote network")
        body, _, status = result.stdout.rpartition(b"\n")
        if not status.isdigit():
            raise SourceError("SSH_FAILED", "Remote curl returned no HTTP status")
        if int(status) != 200:
            raise self._http_error(int(status))
        if len(body) > limit:
            raise SourceError("TOO_LARGE", "FGIS response exceeds the configured safety limit")
        return body

    @staticmethod
    def _http_error(status: int):
        return SourceError(
            "ACCESS_DENIED" if status in {401, 403} else "HTTP_ERROR",
            f"FGIS returned HTTP {status}. This alone does not identify a VPN or its cause.",
            status,
        )

    def fetch(self, path: str, params: dict | None = None, *, file=False):
        if not path or path.startswith("/") or any(s in path for s in (":", "..", "?", "#")):
            raise ValueError("Expected a relative FGIS API path")
        url = BASE + path + ("?" + urlencode(params, doseq=True) if params else "")
        timeout = self.config.file_timeout if file else self.config.timeout
        limit = self.config.max_response_mib * 1024 * 1024
        for attempt in range(self.config.attempts):
            self._pace()
            try:
                if self.config.network == "ssh":
                    body = self._ssh(url, timeout, limit)
                else:
                    req = Request(
                        url,
                        headers={
                            "Accept": "*/*" if file else "application/json",
                            "User-Agent": "fgis-mcp/0.1",
                        },
                    )
                    deadline = time.monotonic() + timeout
                    with self.opener.open(req, timeout=timeout) as response:
                        chunks, size = [], 0
                        while True:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Total response deadline exceeded")
                            chunk = response.read1(min(65536, limit + 1 - size))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            size += len(chunk)
                            if size > limit:
                                break
                        body = b"".join(chunks)
                    if len(body) > limit:
                        raise SourceError("TOO_LARGE", "FGIS response exceeds the configured safety limit")
                return body, {
                    "source_url": url,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "route": self.config.network,
                    "bytes": len(body),
                }
            except HTTPError as exc:
                error = self._http_error(exc.code)
            except (URLError, TimeoutError, OSError) as exc:
                if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
                    error = SourceError(
                        "TLS_ERROR",
                        "Certificate verification failed using the OS trust store; "
                        "check system certificates or your organization's TLS proxy",
                    )
                else:
                    error = SourceError("NETWORK_ERROR", f"FGIS connection failed ({type(exc).__name__})")
            except SourceError as exc:
                error = exc
            retriable = error.status in {429, 500, 502, 503, 504} or error.code in {
                "NETWORK_ERROR",
                "SSH_FAILED",
            }
            if not retriable or attempt + 1 == self.config.attempts:
                raise error
            time.sleep(min(8, 2**attempt))
        raise AssertionError("unreachable")

    def get_value(self, path: str, params: dict | None = None, *, large=False):
        body, meta = self.fetch(path, params, file=large)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError(
                "INVALID_JSON", "FGIS returned non-JSON (possibly a gateway or challenge page)"
            ) from exc
        return data, body, meta

    def get_json(self, path: str, params: dict | None = None):
        data, body, meta = self.get_value(path, params)
        if not isinstance(data, list) or any(not isinstance(r, dict) for r in data):
            raise SourceError("SCHEMA_CHANGED", "Expected a FGIS JSON array of objects")
        return data, body, meta

    def diagnose(self):
        result = {
            "route": self.config.network,
            "proxy_bypass": self.config.network == "direct",
            "vpn_bypass_verified": False,
            "guidance": "Direct ignores application/system proxies, but follows OS routes. "
            "For a full-tunnel VPN, exclude this application in the VPN client (split tunneling), "
            "or configure your own HTTP(S) proxy / SSH host. HTTP 403 is not proof of a VPN.",
        }
        try:
            rows, _, meta = self.get_json("EstimatedPrice/CountrySubjects")
            if not rows or any("id" not in r or "name" not in r for r in rows):
                raise SourceError("SCHEMA_CHANGED", "Region catalogue does not match the expected schema")
            result.update(ok=True, regions=len(rows), **meta)
        except SourceError as exc:
            result.update(ok=False, error=exc.as_dict())
        return result
