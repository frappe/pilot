from __future__ import annotations

from pathlib import Path

from tests.admin.backend.test_admin_app import _client

URL = "/api/v1/site-templates"


def test_templates_are_saved_replaced_by_name_and_deleted(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    client.put(f"{URL}/ERPNext%20setup", json={"apps": ["erpnext"]})
    client.put(f"{URL}/Development", json={"apps": ["builder"]})
    replaced = client.put(f"{URL}/erpnext%20SETUP", json={"apps": ["erpnext", " hrms "]})

    assert replaced.get_json() == {"name": "erpnext SETUP", "apps": ["erpnext", "hrms"]}
    assert client.get(URL).get_json() == {
        "templates": [replaced.get_json(), {"name": "Development", "apps": ["builder"]}]
    }

    assert client.delete(f"{URL}/Development").status_code == 204
    assert client.get(URL).get_json() == {"templates": [replaced.get_json()]}
    assert client.delete(f"{URL}/Development").status_code == 404


def test_an_invalid_template_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    for name, apps in (("Dev", "erpnext"), ("Dev", []), ("Dev", ["not an app"]), ("x" * 51, ["erpnext"])):
        response = client.put(f"{URL}/{name}", json={"apps": apps})

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "invalid_site_template"
    assert client.get(URL).get_json() == {"templates": []}
