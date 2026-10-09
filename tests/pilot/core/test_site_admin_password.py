import json
import sys

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.site.commands import SiteCommands
from pilot.exceptions import CommandError


@pytest.mark.parametrize("fails", [False, True])
def test_admin_password_uses_stdin_and_closes_frappe_connection(tmp_path, monkeypatch, fails):
    root = tmp_path / "benches/dev"
    root.mkdir(parents=True)
    BenchConfig.default("dev", benches_root=root.parent).write(root)
    bench = Bench(root)
    bench.create_directories()
    bench.python.parent.mkdir(parents=True)
    bench.python.symlink_to(sys.executable)
    frappe = bench.sites_path / "frappe"
    (frappe / "utils").mkdir(parents=True)
    (frappe / "__init__.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "state = {}\n"
        "def init(site, sites_path): state['site'] = site\n"
        "def connect(): state['connected'] = True\n"
        "class DB:\n    def commit(self): state['committed'] = True\n"
        "db = DB()\n"
        "def destroy():\n"
        "    state['destroyed'] = True\n"
        "    Path('password-result.json').write_text(json.dumps(state))\n"
    )
    (frappe / "utils/password.py").write_text(
        "import frappe\n"
        "def update_password(user, password, logout_all_sessions):\n"
        "    frappe.state.update(user=user, password=password, logout=logout_all_sessions)\n"
        + ("    raise RuntimeError('password update failed')\n" if fails else "")
    )
    password = "secret 'quoted'\nwith newline"
    from pilot.utils import run_command

    def checked_run(argv, **kwargs):
        assert password not in " ".join(argv)
        assert json.loads(kwargs["stdin_text"])["password"] == password
        assert "env" not in kwargs
        return run_command(argv, **kwargs)

    monkeypatch.setattr("pilot.core.site.commands.run_command", checked_run)
    if fails:
        with pytest.raises(CommandError, match="password update failed"):
            SiteCommands(bench.site("copy.localhost")).set_admin_password(password)
    else:
        SiteCommands(bench.site("copy.localhost")).set_admin_password(password)
    result = json.loads((bench.sites_path / "password-result.json").read_text())
    assert result["site"] == "copy.localhost"
    assert result["password"] == password
    assert result["user"] == "Administrator"
    assert result["logout"] and result["connected"] and result["destroyed"]
    assert result.get("committed", False) == (not fails)
