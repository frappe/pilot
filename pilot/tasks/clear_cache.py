from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class ClearCacheTask(Task):
    command: ClassVar[str] = "clear-cache"
    timeout_seconds: ClassVar[int] = 300

    site: str

    @property
    def resource_keys(self) -> list[str]:
        """The resources this task holds while active."""
        return [self.get_site_resource_key(self.site)]

    def run(self) -> None:
        """Clear the site cache."""
        self.clear_cache()

    @step("clear_cache", lambda self: f"Clear cache for {self.site}")
    def clear_cache(self) -> None:
        """Clear the site cache."""
        self.bench.site(self.site).clear_cache()


if __name__ == "__main__":
    ClearCacheTask.main()
