from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class ClearCacheTask(Task):
    command: ClassVar[str] = "clear-cache"

    site: str

    def run(self) -> None:
        self.clear_cache()

    @step("clear_cache", lambda self: f"Clear cache for {self.site}")
    def clear_cache(self) -> None:
        self.bench.site(self.site).clear_cache()


if __name__ == "__main__":
    ClearCacheTask.main()
