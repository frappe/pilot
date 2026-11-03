class BenchError(Exception):
    pass


class BenchAlreadyExistsError(BenchError):
    pass


class ConfigError(BenchError):
    pass


class CommandError(BenchError):
    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.message = message
        self.returncode = returncode


class TaskNotFoundError(BenchError):
    pass


class TaskNotRunningError(BenchError):
    pass


class TaskNotCancellableError(BenchError):
    pass


class TaskConflictError(BenchError):
    pass


class MigrateError(BenchError):
    def __init__(self, message: str, output: str = "", returncode: int = 1):
        super().__init__(message)
        self.output = output
        self.returncode = returncode


class MigrationNotFoundError(BenchError):
    pass


class MigrationConflictError(BenchError):
    pass


class AppValidationError(BenchError):
    pass


class DomainConflictError(BenchError):
    pass


class DomainProviderError(BenchError):
    pass


class RegistryError(BenchError):
    """Base for marketplace-registry-related failures."""


class RegistryUnavailableError(RegistryError):
    """The registry itself failed to load (tampered cache, network, corruption)."""


class AppNotFoundError(RegistryError):
    """The named app isn't in an otherwise successfully-loaded registry."""


class DependencyResolutionError(RegistryError):
    """A dependency chain couldn't be resolved (cycle, version conflict, etc)."""


class InsufficientDiskSpaceError(BenchError):
    def __init__(self, purpose: str, needed_bytes: int, free_bytes: int, reserved_bytes: int):
        super().__init__(
            f"Not enough disk space for {purpose}: it needs about {self.format_size(needed_bytes)}, "
            f"plus {self.format_size(reserved_bytes)} kept free for the system, "
            f"and {self.format_size(free_bytes)} is free."
        )
        self.needed_bytes = needed_bytes
        self.free_bytes = free_bytes

    @staticmethod
    def format_size(size: int) -> str:
        if size >= 1024**3:
            return f"{size / 1024**3:.1f} GB"
        if size >= 1024**2:
            return f"{size / 1024**2:.0f} MB"
        return f"{size / 1024:.0f} KB"


class CronError(BenchError):
    pass


class MalformedSiteConfig(ConfigError):
    """common_site_config.json cannot be parsed, so it must not be rewritten."""


class RemoteSiteError(BenchError):
    pass


class UploadOffsetError(BenchError):
    """A chunk starts past the bytes received, so the client must resume from `received`."""

    def __init__(self, received: int) -> None:
        super().__init__(f"Send the file from byte {received}.")
        self.received = received


class FrappeCloudError(BenchError):
    """Frappe Cloud refused a v1 migration call or could not be reached."""


class DatabaseError(BenchError):
    """A database server operation failed (connection, provisioning, credentials)."""


class TwoFactorError(BenchError):
    """Raised when an enrollment request cannot be honoured."""
