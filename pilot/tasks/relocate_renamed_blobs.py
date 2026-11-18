from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RelocateRenamedBlobsTask(Task):
    """Move a renamed site's stored files to its new name. Safe to retry."""

    command: ClassVar[str] = "relocate-renamed-blobs"
    timeout_seconds: ClassVar[int] = 86400

    site: str

    @property
    def resource_keys(self) -> list[str]:
        """The resources this task holds while active."""
        return [self.get_site_resource_key(self.site)]

    def run(self) -> None:
        """Move the stored files."""
        self.relocate()

    @step("relocate", lambda self: f"Move stored files of {self.site}", icon="lucide-folder-input")
    def relocate(self) -> None:
        """Have Frappe copy the files under the former name to the site's prefix."""
        self.bench.site(self.site).relocate_renamed_blobs()


if __name__ == "__main__":
    RelocateRenamedBlobsTask.main()
