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


class TaskConflictError(BenchError):
    pass


class MigrateError(BenchError):
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


class DatabaseError(BenchError):
    """A database server operation failed (connection, provisioning, credentials)."""
