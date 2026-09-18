import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_data_path


@dataclass(frozen=True)
class Config:
    root: Path
    network: str = "direct"
    proxy: str = field(default="", repr=False)
    ssh_host: str = field(default="", repr=False)
    timeout: float = 20
    file_timeout: float = 180
    interval: float = 1
    attempts: int = 3
    max_response_mib: int = 256

    def __post_init__(self):
        if not 1 <= self.max_response_mib <= 1024:
            raise ValueError("FGIS_MAX_RESPONSE_MIB must be 1..1024")
        if self.network not in {"direct", "system", "proxy", "ssh"}:
            raise ValueError("FGIS_NETWORK must be direct, system, proxy or ssh")
        if self.network == "proxy":
            p = urlsplit(self.proxy)
            if p.scheme not in {"http", "https"} or not p.hostname:
                raise ValueError("FGIS_PROXY_URL must be an HTTP(S) proxy URL")
        if self.network == "ssh" and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", self.ssh_host):
            raise ValueError("FGIS_SSH_HOST must be an SSH alias or user@host, without options")
        if not (1 <= self.timeout <= 300 and 1 <= self.file_timeout <= 900):
            raise ValueError("Timeouts must be positive and bounded")
        if not (0.1 <= self.interval <= 60 and 1 <= self.attempts <= 5):
            raise ValueError("Request interval must be 0.1..60 seconds; attempts 1..5")

    @classmethod
    def from_env(cls):
        return cls(
            root=Path(os.getenv("FGIS_DATA_DIR") or user_data_path("fgis-mcp", appauthor=False)).expanduser(),
            network=os.getenv("FGIS_NETWORK", "direct"),
            proxy=os.getenv("FGIS_PROXY_URL", ""),
            ssh_host=os.getenv("FGIS_SSH_HOST", ""),
            timeout=float(os.getenv("FGIS_TIMEOUT", "20")),
            file_timeout=float(os.getenv("FGIS_FILE_TIMEOUT", "180")),
            max_response_mib=int(os.getenv("FGIS_MAX_RESPONSE_MIB", "256")),
            interval=float(os.getenv("FGIS_REQUEST_INTERVAL", "1")),
        )
