from __future__ import annotations

from typing import TYPE_CHECKING

from admin.backend.core.editor.workspace import EditorLockedError, EditorWorkspace

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteScriptsWorkspace(EditorWorkspace):
    """A site's server_scripts folder. Files mirror Server Script records, so only content is editable."""

    def __init__(self, site: "Site") -> None:
        super().__init__(site.server_scripts_path, scope=f"site-{site.config.name}", isolated_git=True)
        self.site = site

    def create(self, rel: str, kind: str) -> dict:
        raise EditorLockedError("Create the Server Script in Desk. Its file is made on save.")

    def rename(self, frm: str, to: str) -> dict:
        raise EditorLockedError("Server Script files are named after their record and cannot be renamed.")

    def delete(self, rel: str) -> None:
        target = self.safe(rel)
        if not target.is_file() or target.suffix != ".py" or target.parent.resolve() != self.root_real:
            raise EditorLockedError("Only Server Script files can be deleted here.")
        self.site.delete_server_script(target.stem)
