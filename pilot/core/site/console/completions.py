from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pilot.internal.command import run_command

if TYPE_CHECKING:
    from pilot.core.site import Site

TIMEOUT_SECONDS = 60

# Everything comes from the site's own frappe, so it matches its version and installed apps.
_BOOTSTRAP = """\
import json
import sys

import frappe


def doctype(name):
    if not frappe.db.exists("DocType", name):
        return None
    from frappe.model.document import get_controller

    meta = frappe.get_meta(name)
    fields = [
        {"fieldname": f.fieldname, "fieldtype": f.fieldtype, "label": f.label or "", "options": f.options or ""}
        for f in meta.fields
        if f.fieldtype not in frappe.model.display_fieldtypes
    ]
    standard = [*frappe.model.default_fields, *(frappe.model.child_table_fields if meta.istable else ())]
    fields += [{"fieldname": f, "fieldtype": "", "label": "", "options": ""} for f in standard]
    controller = get_controller(name)
    return {"fields": fields, "controller": f"{controller.__module__}.{controller.__qualname__}"}


def answer(mode, argument):
    if mode == "doctypes":
        return frappe.get_all("DocType", pluck="name", order_by="name")
    return doctype(argument)


frappe.init(site=sys.argv[1], sites_path=".")
frappe.connect()
try:
    print(json.dumps(answer(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")))
finally:
    frappe.destroy()
"""


class ConsoleCompletions:
    """Names the console editor suggests, read on demand from the site's own Frappe."""

    def __init__(self, site: "Site") -> None:
        self.site = site

    @property
    def doctypes(self) -> list[str]:
        return self._query("doctypes")

    def get_doctype(self, doctype: str) -> dict[str, Any]:
        """Fields of a doctype, its standard fields, and the dotted path of its controller class."""
        found = self._query("doctype", doctype)
        if found is None:
            raise ValueError(f"DocType '{doctype}' does not exist.")
        return found

    def _query(self, *args: str) -> Any:
        argv = [str(self.site.bench.python), "-c", _BOOTSTRAP, self.site.config.name, *args]
        result = run_command(argv, cwd=self.site.bench.sites_path, timeout=TIMEOUT_SECONDS)
        # Apps may print while Frappe loads, so the answer is the last line.
        return json.loads(result.stdout.decode().strip().splitlines()[-1])
