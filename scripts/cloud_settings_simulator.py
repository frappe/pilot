from __future__ import annotations

import argparse
import ast
import base64
import json
import secrets
import threading
import time
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

# Central queues a converted trial's move to its plan, then the server restarts on it.
MOVE_QUEUE_SECONDS = 3
MOVE_SECONDS = 20
MOVE_STEPS = ("Stopping your site", "Moving to the new plan", "Starting your site")

NO_WAY_TO_PAY = {
    "code": "no_settlement_source",
    "title": "No way to pay this",
    "fix": "Add a payment method or top up your wallet before the due date.",
}
INVOICE_METHODS = (
    "list_server_invoices",
    "get_server_invoice_pdf",
    "create_invoice_checkout",
    "get_checkout_status",
)
# A one-page PDF, so Download has a real file to save.
SAMPLE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>"
    b"endobj 4 0 obj<</Length 44>>stream\nBT /F1 18 Tf 20 70 Td (Sample invoice) Tj ET\nendstream endobj "
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
)

# Central's gateway per billing currency, with the hint it sends for each.
GATEWAYS = {
    "USD": {"name": "Stripe", "adapter_key": "Stripe", "label": "Stripe", "subtitle": "Secure redirect"},
    "INR": {"name": "Razorpay", "adapter_key": "Razorpay", "label": "Razorpay", "subtitle": "Opens here"},
}


def read_reference_data(bench_root: Path) -> tuple[list[str], list[str]]:
    """Frappe's country names and Central's GST states, the lists Central's address form sends."""
    countries = json.loads((bench_root / "apps/frappe/frappe/geo/country_info.json").read_text())
    gst = bench_root / "apps/central/central/billing/india_gst.py"
    states: list[str] = []
    if gst.exists():
        for node in ast.parse(gst.read_text()).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "GST_STATE_CODES":
                states = list(ast.literal_eval(node.value))
    return sorted(countries), states


class BillingSimulation:
    """Disposable billing state for the local Cloud Settings flow."""

    def __init__(
        self,
        trial_app: str | None = None,
        countries: list[str] | None = None,
        india_states: list[str] | None = None,
    ) -> None:
        self.trial_app = trial_app
        self.countries = countries or []
        self.india_states = india_states or []
        self.reset()

    def reset(self) -> None:
        self.failure = False
        self.profile_complete = False
        self.card_active = False
        self.is_trial = False
        self.is_trial_ended = False
        self.plan = TRIAL_PLAN
        self.checkouts: dict[str, bool] = {}
        self.move_started_at: float | None = None
        self.invoice_checkouts: dict[str, str] = {}
        # Central's server share of each team invoice. August also covered another server of the
        # team, so it is a statement only, with no PDF or payment here.
        self.invoices = [
            self._invoice("2026-10", "Outstanding", "$40.00"),
            self._invoice("2026-09", "Paid", "$40.00"),
            self._invoice("2026-08", "Paid", "$38.71", is_whole_invoice=False),
        ]

        self.profile = {
            "currency": "USD",
            "legal_name": "Example Company (simulation)",
            "email": "billing@example.test",
            "phone": "",
            "address_line1": "1 Example Street",
            "city": "Example City",
            "state": "Example State",
            "country": "United States",
            "pincode": "12345",
            "gstin": "",
            "supported_currencies": list(GATEWAYS),
            "currency_locked": False,
            "countries": self.countries,
            "india_states": self.india_states,
        }

    @staticmethod
    def _invoice(month: str, status: str, amount: str, is_whole_invoice: bool = True) -> dict:
        return {
            "name": f"SIM-INV-{month}",
            "period_start": f"{month}-01",
            "period_end": f"{month}-30",
            "status": status,
            "amount": amount,
            "has_pdf": is_whole_invoice,
            "can_pay": is_whole_invoice and status == "Outstanding",
        }

    @property
    def trial(self) -> dict | None:
        """An open trial, as Central reports it until a payment method converts it."""
        if not self.is_trial:
            return None
        title, _, rate = PLANS[TRIAL_PLAN]
        return {
            "state": "Ended" if self.is_trial_ended else "Active",
            "ends_on": "2026-11-02",
            "days_left": 0 if self.is_trial_ended else 5,
            "deletes_on": "2026-11-09",
            "held": False,
            "plan": {"title": title, "monthly_rate": rate, "currency": "USD"},
            "app": self.trial_app,
        }

    def activate_card(self) -> None:
        """A new payment method converts the trial, as Central's payment hook does, and the
        server moves from the trial plan to the plan the customer chose."""
        if self.is_trial and self.plan != TRIAL_PLAN:
            self.move_started_at = time.monotonic()
        self.card_active = True
        self.is_trial = False

    @property
    def move_elapsed(self) -> float | None:
        """Seconds into the move, or None when no move runs."""
        if self.move_started_at is None:
            return None
        elapsed = time.monotonic() - self.move_started_at
        return elapsed if elapsed < MOVE_QUEUE_SECONDS + MOVE_SECONDS else None

    def get_plan_change(self, base_url: str) -> dict | None:
        """The pending move, with its progress page once Central has queued it."""
        elapsed = self.move_elapsed
        if elapsed is None:
            return None
        url = f"{base_url}/progress" if elapsed >= MOVE_QUEUE_SECONDS else None
        return {"plan": PLANS[self.plan][0], "progress_url": url}

    def get_summary(self, base_url: str) -> dict:
        title, subtitle, _ = PLANS[self.plan]
        return {
            "currency": "USD",
            "plan": {"name": title, "subtitle": subtitle, "specs": {}},
            "trial": self.trial,
            "estimate": {"amount": "$10.00", "note": "Sample monthly estimate"},
            "credit": {"amount": "$25.00", "note": "Sample credit only", "warning": False, "available": True},
            "payment_method": {"name": "simulated-card", "label": "Simulated Visa ending 4242"}
            if self.card_active
            else None,
            "profile_complete": self.profile_complete,
            "plan_change": self.get_plan_change(base_url),
            "next_bill": self.next_bill,
        }

    @property
    def next_bill(self) -> dict:
        """The server's next bill. A paid server without a card has no way to pay it."""
        title, _, rate = PLANS[self.plan]
        amount = f"${rate:.2f}"
        return {
            "amount": amount,
            "subtotal": amount,
            "tax": None,
            "tax_type": None,
            "charge_on": "2026-12-01",
            "days_left": 24,
            "lines": [{"item": title, "detail": "Monthly plan", "amount": amount}],
            "blockers": [] if self.card_active or self.is_trial else [NO_WAY_TO_PAY],
        }

    def call(self, method: str, data: dict, base_url: str) -> dict | list:
        if method == "get_billing_summary":
            return self.get_summary(base_url)
        if method == "get_billing_profile":
            return self.profile
        if method == "save_billing_profile":
            return self.save_profile(data)
        if method in ("get_plan_options", "change_plan"):
            return self.plans(method, data)
        if method == "get_payment_gateways":
            return [GATEWAYS[self.profile["currency"]]]
        if method in INVOICE_METHODS:
            return self.invoice_call(method, data, base_url)
        if method == "add_payment_method":
            abort(
                501,
                "Razorpay Checkout needs real Razorpay keys. Use an address outside India to test the card flow.",
            )
        return self.payment(method, data, base_url)

    def save_profile(self, data: dict) -> dict:
        """Central's rule: India is billed only in INR, and only India is billed in INR."""
        if (data.get("country") == "India") != (data.get("currency") == "INR"):
            abort(400, "A team in India is billed in INR, and only a team in India.")
        editable = set(self.profile) - {
            "supported_currencies",
            "currency_locked",
            "countries",
            "india_states",
        }
        self.profile.update({key: value for key, value in data.items() if key in editable})
        self.profile_complete = True
        return {"saved": True}

    def plans(self, method: str, data: dict) -> dict:
        if method == "change_plan":
            if data.get("plan") not in PLANS:
                abort(400, "Unknown simulated plan.")
            self.plan = data["plan"]
            return {"queued": True, "resized": False, "progress_url": None}
        # A trial is never billed on its trial plan, so Central offers only the paid plans
        # and selects the one the trial converts to.
        paid = {name: plan for name, plan in PLANS.items() if not (self.is_trial and name == TRIAL_PLAN)}
        return {
            "currency": "USD",
            "provider": "Simulation",
            "region": "Local",
            "current": None if self.is_trial else self.plan,
            "selected": (self.plan if self.plan in paid else next(iter(paid))) if self.is_trial else None,
            "plans": [
                {
                    "name": name,
                    "title": title,
                    "subtitle": subtitle,
                    "price": f"${rate}/mo",
                    "is_current": not self.is_trial and name == self.plan,
                }
                for name, (title, subtitle, rate) in paid.items()
            ],
            "sufficient": True,
        }

    def invoice_call(self, method: str, data: dict, base_url: str) -> dict | list:
        if method == "list_server_invoices":
            return [] if self.is_trial else self.invoices
        if method == "get_server_invoice_pdf":
            self._get_whole_invoice(data.get("invoice"))
            return {"filename": f"{data['invoice']}.pdf", "content": base64.b64encode(SAMPLE_PDF).decode()}
        if method == "create_invoice_checkout":
            self._get_whole_invoice(data.get("invoice"))
            reference = secrets.token_hex(12)
            self.checkouts[reference] = False
            self.invoice_checkouts[reference] = data["invoice"]
            return {"reference": reference, "checkout_url": f"{base_url}/checkout/{reference}"}
        return self.settle_invoice_checkout(data.get("reference"))

    def _get_whole_invoice(self, name: str | None) -> dict:
        invoice = next((row for row in self.invoices if row["name"] == name), None)
        if not invoice:
            abort(404, "Unknown simulated invoice.")
        if not invoice["has_pdf"]:
            abort(
                403,
                "This invoice also covers your team's other sites. Open it from the Frappe Cloud dashboard.",
            )
        return invoice

    def settle_invoice_checkout(self, reference: str | None) -> dict:
        """A completed checkout pays its invoice, as Central's capture does."""
        if reference not in self.invoice_checkouts:
            abort(400, "Unknown simulated checkout.")
        if not self.checkouts[reference]:
            return {"status": "pending"}
        invoice = self._get_whole_invoice(self.invoice_checkouts[reference])
        invoice.update(status="Paid", can_pay=False)
        return {"status": "paid"}

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


def create_simulator(base_url: str, trial_app: str | None = None, bench_root: Path | None = None) -> Flask:
    app = Flask("cloud-settings-simulation")
    countries, india_states = read_reference_data(bench_root) if bench_root else ([], [])
    state = BillingSimulation(trial_app, countries, india_states)
    token = secrets.token_hex(24)
    register_metadata(app, token, base_url)
    register_central(app, state, token, base_url)
    register_checkout(app, state)
    register_progress(app, state)

    @app.get("/")
    def controls():
        return f"""<!doctype html><title>Cloud Settings simulation</title>
        <h1>Local Cloud Settings simulation</h1>
        <p>Sample data only. No real charges, cards, or Central account changes.</p>
        <p>Open Cloud Settings in your local app to test Billing. Save the sample billing profile,
        continue with Stripe, complete the simulated checkout, then check its status. An address in
        India bills in INR through Razorpay, which needs real keys, so the simulation stops there.</p>
        <p>Simulated API failure: {"on" if state.failure else "off"}</p>
        <form method="post" action="/failure"><button>Toggle API failure</button></form>
        <p>Free trial: {"open" if state.is_trial else "none"}. A completed checkout converts it.</p>
        <form method="post" action="/trial"><button>Start a free trial</button></form>
        <form method="post" action="/trial/ended"><button>End the free trial</button></form>
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
        state.invoices = []
        return redirect("/")

    @app.post("/trial/ended")
    def trial_ended():
        state.reset()
        state.is_trial = True
        state.is_trial_ended = True
        state.invoices = []
        return redirect("/")

    @app.post("/reset")
    def reset():
        state.reset()
        return redirect("/")

    return app


def register_central(app: Flask, state: BillingSimulation, token: str, base_url: str) -> None:
    @app.route("/api/method/central.billing.api.billing_api.<method>", methods=["GET", "POST"])
    def billing(method: str):
        if request.headers.get("X-Pilot-Token") != token:
            abort(401)
        if state.failure:
            return jsonify(
                exception="SimulationError: Simulated Central outage. Disable it on the simulation page."
            ), 503
        return jsonify(message=state.call(method, request.get_json(silent=True) or {}, base_url))

    # Central locks a server past its sites while its trial is open.
    @app.get("/api/method/central.api.pilot.get_server_access")
    def server_access():
        if request.headers.get("X-Pilot-Token") != token:
            abort(401)
        return jsonify(message={"is_locked": state.is_trial})


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


def register_progress(app: Flask, state: BillingSimulation) -> None:
    """Central's move progress page, which sends the customer back once the site answers."""

    @app.get("/progress/status")
    def progress_status():
        elapsed = state.move_elapsed
        if elapsed is None:
            return jsonify(done=True, step=len(MOVE_STEPS))
        step = int(max(0.0, elapsed - MOVE_QUEUE_SECONDS) / MOVE_SECONDS * len(MOVE_STEPS))
        return jsonify(done=False, step=min(step, len(MOVE_STEPS) - 1))

    @app.get("/progress")
    def progress():
        steps = "".join(f"<li>{label}</li>" for label in MOVE_STEPS)
        return f"""<!doctype html><title>Upgrading your site</title>
        <style>body{{font:15px system-ui;max-width:32rem;margin:15vh auto;padding:0 16px;color:#222}}
        li{{margin:.4rem 0;color:#999}} li.done{{color:#1f7a4d}} li.now{{color:#222;font-weight:600}}</style>
        <p>Local simulation of Central's progress page.</p>
        <h1>Upgrading your site</h1><p>Your site restarts once. Its data stays as it is.</p>
        <ol id="steps">{steps}</ol><p id="ready" hidden>Your site is ready. Taking you back…</p>
        <script>
        const back = document.referrer || "/"
        const tick = async () => {{
          const status = await (await fetch("/progress/status")).json()
          document.querySelectorAll("#steps li").forEach((li, i) =>
            li.className = status.done || i < status.step ? "done" : i === status.step ? "now" : "")
          if (!status.done) return setTimeout(tick, 1000)
          document.getElementById("ready").hidden = false
          setTimeout(() => location.assign(back), 1500)
        }}
        tick()
        </script>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a local Pilot with disposable simulated Central billing."
    )
    parser.add_argument("--bench-root", type=Path, required=True)
    parser.add_argument("--pilot-port", type=int, default=8003)
    parser.add_argument("--simulation-port", type=int, default=8124)
    parser.add_argument("--trial-app", help="The app a simulated trial is for, which cannot be uninstalled.")
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.simulation_port}"

    from admin.backend.app import create_app

    # A browser holds an idle connection to the controls page, which would stall a single thread
    # and with it Pilot's metadata reads.
    simulator = make_server(
        "127.0.0.1",
        args.simulation_port,
        create_simulator(base_url, args.trial_app, args.bench_root.resolve()),
        threaded=True,
    )
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
