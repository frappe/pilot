from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RenameSiteTask(Task):
    """Rename a site and refresh whatever serves it, without dropping a request."""

    command: ClassVar[str] = "rename-site"
    # A partial rename can leave the site under a name nothing references.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    new_name: str
    # Keep the old hostname by default so existing links continue to work.
    keep_old_hostname: bool = True
    make_primary: bool = False

    @property
    def resource_keys(self) -> list[str]:
        """Holds both hostnames until the old provider route is retained or released."""
        return [
            self.get_site_resource_key(self.site),
            self.get_site_resource_key(self.new_name),
            self.get_host_resource_key(self.site),
            self.get_host_resource_key(self.new_name),
        ]

    def run(self) -> None:
        """Rename the site."""
        self.require_production_privileges()
        self.rename_site()

    @step("rename", lambda self: f"Rename {self.site} to {self.new_name}")
    def rename_site(self) -> None:
        """Rename the site."""
        self.bench.site(self.site).rename_to(
            self.new_name,
            on_progress=self.report,
            keep_old_hostname=self.keep_old_hostname,
            make_primary=self.make_primary,
        )


if __name__ == "__main__":
    RenameSiteTask.main()
