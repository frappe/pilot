import subprocess
import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class ClearCacheTask(BaseTask):
    command: ClassVar[str] = "clear-cache"

    site: str

    def run(self) -> None:
        self.clear_cache()

    @step("clear_cache", lambda self: f"Clear cache for {self.site}")
    def clear_cache(self) -> None:
        result = subprocess.run(
            [*self.bench.frappe_call, "frappe", "--site", self.site, "clear-cache"]
        )
        if result.returncode != 0:
            sys.exit(result.returncode)


if __name__ == "__main__":
    ClearCacheTask.main()
