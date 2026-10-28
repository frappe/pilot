from __future__ import annotations

import argparse
import secrets
import threading
from functools import partial
from pathlib import Path
from unittest.mock import patch

from flask import Flask, abort, jsonify, redirect, request
from werkzeug.exceptions import NotFound
from werkzeug.serving import make_server

from pilot.integrations.central.metadata import InstanceMetadata

# The trial plan and the bigger plans Central offers after conversion. Central leaves out
# plans smaller than the trial plan, so the simulation does too.
PLANS = {
    "vm-1-2-1g": ("0.5 vCPU / 1 GB", "0.5 vCPU · 1 GB RAM · 12.5 GB disk", 15),
    "vm-1-2": ("0.5 vCPU / 2 GB", "0.5 vCPU · 2 GB RAM · 25 GB disk", 20),
    "vm-1": ("1 vCPU / 4 GB", "1 vCPU · 4 GB RAM · 50 GB disk", 40),
    "vm-2": ("2 vCPU / 8 GB", "2 vCPU · 8 GB RAM · 100 GB disk", 80),
}
TRIAL_PLAN = "vm-1-2-1g"


class BillingSimulation:
    """Disposable billing state for the local Cloud Settings flow."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.failure = False
        self.profile_complete = False
        self.card_active = False
        self.is_trial = False
        self.plan = TRIAL_PLAN
        self.checkouts: dict[str, bool] = {}
        self.profile = {
            "currency": "USD",
            "legal_name": "Example Company (simulation)",
            "email": "billing@example.test",
            "address_line1": "1 Example Street",
            "city": "Example City",
            "state": "Example State",
            "country": "US",
            "pincode": "12345",
            "gstin": "",
            "supported_currencies": ["USD"],
        }

    @property
    def trial(self) -> dict | None:
        """An open trial, as Central reports it until a payment method converts it."""
        if not self.is_trial:
            return None
        title, _, rate = PLANS[TRIAL_PLAN]
        return {
            "state": "Active",
            "ends_on": "2026-11-02",
            "days_left": 5,
            "deletes_on": "2026-11-09",
            "held": False,
            "plan": {"title": title, "monthly_rate": rate, "currency": "USD"},
        }

    def activate_card(self) -> None:
        """A new payment method converts the trial, as Central's payment hook does."""
        self.card_active = True
        self.is_trial = False

    @property
    def summary(self) -> dict:
        title, subtitle, _ = PLANS[self.plan]
        return {
            "currency": "USD",
            "plan": {"name": title, "subtitle": subtitle, "specs": {}},
            "trial": self.trial,
            "estimate": {"amount": "$10.00", "note": "Sample monthly estimate"},
            "credit": {"amount": "$25.00", "note": "Sample credit only", "warning": False},
            "payment_method": {"name": "simulated-card", "label": "Simulated Visa ending 4242"}
            if self.card_active
            else None,
            "profile_complete": self.profile_complete,
        }

    def call(self, method: str, data: dict, base_url: str) -> dict | list:
        if method == "get_billing_summary":
            return self.summary
        if method == "get_billing_profile":
            return self.profile
        if method == "save_billing_profile":
            self.profile.update(
                {
                    key: value
                    for key, value in data.items()
                    if key in self.profile and key != "supported_currencies"
                }
            )
            self.profile_complete = True
            return {"saved": True}
        if method in ("get_plan_options", "change_plan"):
            return self.plans(method, data)
        if method == "get_payment_gateways":
            return [
                {
                    "name": "simulation",
                    "label": "Local simulation",
                    "adapter_key": "Stripe",
                    "method_types": ["Card"],
                    "subtitle": "No card details or real payment",
                }
            ]
        return self.payment(method, data, base_url)

    def plans(self, method: str, data: dict) -> dict:
        if method == "change_plan":
            if data.get("plan") not in PLANS:
                abort(400, "Unknown simulated plan.")
            self.plan = data["plan"]
            return {"queued": True, "resized": False, "progress_url": None}
        return {
            "currency": "USD",
            "provider": "Simulation",
            "region": "Local",
            "current": self.plan,
            "plans": [
                {
                    "name": name,
                    "title": title,
                    "subtitle": subtitle,
                    "price": f"${rate}/mo",
                    "is_current": name == self.plan,
                }
                for name, (title, subtitle, rate) in PLANS.items()
            ],
            "sufficient": True,
        }

    def payment(self, method: str, data: dict, base_url: str) -> dict:
        if method == "create_payment_method_checkout":
            reference = secrets.token_hex(12)
            self.checkouts[reference] = False
            return {"reference": reference, "checkout_url": f"{base_url}/checkout/{reference}"}
        if method == "confirm_payment_method_checkout":
            if data.get("reference") not in self.checkouts:
                abort(400, "Unknown simulated checkout.")
            active = self.checkouts[data["reference"]]
            if active:
                self.activate_card()
            return {"active": active, "message": "Finish the local simulated checkout, then check again."}
        if method == "reconcile_payment_setup":
            if any(self.checkouts.values()):
                self.activate_card()
            return {"activated": 1 if self.card_active else 0}
        if method == "remove_payment_method":
            self.card_active = False
            self.checkouts.clear()
            return {"removed": True}
        raise NotFound("This operation is not simulated.")


def create_simulator(base_url: str) -> Flask:
    app = Flask("cloud-settings-simulation")
    state = BillingSimulation()
    token = secrets.token_hex(24)
    register_metadata(app, token, base_url)
    register_checkout(app, state)

    @app.route("/api/method/central.billing.api.billing_api.<method>", methods=["GET", "POST"])
    def billing(method: str):
        if request.headers.get("X-Pilot-Token") != token:
            abort(401)
        if state.failure:
            return jsonify(
                exception="SimulationError: Simulated Central outage. Disable it on the simulation page."
            ), 503
        return jsonify(message=state.call(method, request.get_json(silent=True) or {}, base_url))

    @app.get("/")
    def controls():
        return f"""<!doctype html><title>Cloud Settings simulation</title>
        <h1>Local Cloud Settings simulation</h1>
        <p>Sample data only. No real charges, cards, or Central account changes.</p>
        <p>Open Cloud Settings in your local app to test Billing. Save the sample billing profile,
        continue with Local simulation, complete the simulated checkout, then check its status.</p>
        <p>Simulated API failure: {"on" if state.failure else "off"}</p>
        <form method="post" action="/failure"><button>Toggle API failure</button></form>
        <p>Free trial: {"open" if state.is_trial else "none"}. A completed checkout converts it.</p>
        <form method="post" action="/trial"><button>Start a free trial</button></form>
        <form method="post" action="/reset"><button>Reset sample billing data</button></form>
        <p>After reset, close and reopen Cloud Settings.</p>"""

    @app.post("/failure")
    def failure():
        state.failure = not state.failure
        return redirect("/")

    @app.post("/trial")
    def trial():
        state.reset()
        state.is_trial = True
        return redirect("/")

    @app.post("/reset")
    def reset():
        state.reset()
        return redirect("/")

    return app


def register_metadata(app: Flask, token: str, base_url: str) -> None:
    @app.put("/latest/api/token")
    def metadata_token():
        return token

    @app.get("/latest/meta-data/attributes/<name>")
    def metadata(name: str):
        if request.headers.get("X-metadata-token") != token:
            abort(401)
        if name != "pilot-central":
            abort(404)
        return jsonify(
            {
                "central_endpoint": base_url,
                "central_auth_token": token,
                "jwks_url": f"{base_url}/jwks",
                "jwks_audience_id": "local-simulation",
            }
        )


def register_checkout(app: Flask, state: BillingSimulation) -> None:
    @app.get("/checkout/<reference>")
    def checkout(reference: str):
        if reference not in state.checkouts:
            abort(404)
        return """<!doctype html><title>Simulated checkout</title>
        <h1>Simulated checkout</h1><p>No payment runs here. Do not enter card details.</p>
        <form method="post"><button>Complete simulated setup</button></form>
        <p>To cancel, close this tab and use Cancel in Cloud Settings.</p>"""

    @app.post("/checkout/<reference>")
    def complete(reference: str):
        if reference not in state.checkouts:
            abort(404)
        state.checkouts[reference] = True
        return "<h1>Simulated setup complete</h1><p>Return to Cloud Settings and click Check status.</p>"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a local Pilot with disposable simulated Central billing."
    )
    parser.add_argument("--bench-root", type=Path, required=True)
    parser.add_argument("--pilot-port", type=int, default=8003)
    parser.add_argument("--simulation-port", type=int, default=8124)
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.simulation_port}"

    from admin.backend.app import create_app

    simulator = make_server("127.0.0.1", args.simulation_port, create_simulator(base_url))
    pilot = make_server("127.0.0.1", args.pilot_port, create_app(args.bench_root.resolve()), threaded=True)
    threading.Thread(target=simulator.serve_forever, daemon=True).start()
    # Only this disposable process uses the local metadata service.
    local_metadata = partial(InstanceMetadata, base_url=f"{base_url}/latest")
    with (
        patch("pilot.integrations.central.metadata.InstanceMetadata", local_metadata),
        patch("pilot.integrations.central.InstanceMetadata", local_metadata),
    ):
        print(f"Simulation controls: {base_url}", flush=True)
        print(f"Local Pilot endpoint: http://127.0.0.1:{args.pilot_port}", flush=True)
        try:
            pilot.serve_forever()
        finally:
            simulator.shutdown()
            simulator.server_close()
            pilot.server_close()


if __name__ == "__main__":
    main()
