"""Standard machine-readable error codes and exceptions for FGIS MCP."""


class FgisError(ValueError):
    """Base error for all FGIS MCP operational failures with machine-readable code."""

    def __init__(self, code: str, message: str, status: int | None = None):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(f"[{code}] {message}" if not message.startswith(f"[{code}]") else message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "http_status": self.status}


class NotFoundError(FgisError):
    def __init__(self, message: str = "Requested entity not found"):
        super().__init__("NOT_FOUND", message)


class LocalDatasetIncompleteError(FgisError):
    def __init__(self, message: str = "Local dataset is incomplete; entity or period is missing locally"):
        super().__init__("LOCAL_DATASET_INCOMPLETE", message)


class SourceNotImportedError(FgisError):
    def __init__(self, message: str = "Source has not been imported into local dataset"):
        super().__init__("SOURCE_NOT_IMPORTED", message)


class AmbiguousMatchError(FgisError):
    def __init__(self, message: str = "Multiple ambiguous matches found"):
        super().__init__("AMBIGUOUS_MATCH", message)


class AmbiguousSnapshotError(FgisError):
    def __init__(self, message: str = "Ambiguous snapshot reference"):
        super().__init__("AMBIGUOUS_SNAPSHOT", message)


class SnapshotNotFoundError(FgisError):
    def __init__(self, message: str = "Snapshot not found"):
        super().__init__("SNAPSHOT_NOT_FOUND", message)


class UnresolvedConditionError(FgisError):
    def __init__(self, message: str = "Application condition cannot be resolved from source text"):
        super().__init__("UNRESOLVED_CONDITION", message)


class UnsupportedByFgisMcpError(FgisError):
    def __init__(self, message: str = "Fact or operation is unsupported by FGIS MCP evidence"):
        super().__init__("UNSUPPORTED_BY_FGIS_MCP", message)


class SnapshotImmutableError(FgisError):
    def __init__(
        self,
        message: str = "Snapshot is complete and immutable; cannot overwrite with modified archive",
    ):
        super().__init__("SNAPSHOT_IMMUTABLE", message)
