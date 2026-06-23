class BenchError(Exception):
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


class VolumeError(BenchError):
    pass


class MigrateError(BenchError):
    pass
