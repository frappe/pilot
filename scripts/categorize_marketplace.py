#!/usr/bin/env python3
"""
Assign a single 'category' field to each app in registry/apps.json using a 6-category taxonomy.

Categories:
  Applications      — standalone Frappe apps (ERPNext, CRM, HRMS, etc.)
  Extensions — modules and add-ons that extend ERPNext
  Integrations      — third-party service connectors
  Compliance     — country-specific compliance and localisation apps
  Developer   — framework, builders, dev utilities
  Utilities — UI themes, small utility apps

Run:
    python3 scripts/categorize_marketplace.py
"""

from __future__ import annotations

import json
from pathlib import Path

REGISTRY = Path(__file__).parent.parent / "registry" / "apps.json"

# Explicit overrides — highest priority
KNOWN: dict[str, str] = {
    # Developer
    "frappe": "Developer Tools",
    "builder": "Developer Tools",
    "insights": "Developer Tools",
    "print_designer": "Developer Tools",
    "studio": "Developer Tools",
    # Applications
    "erpnext": "Applications",
    "hrms": "Applications",
    "crm": "Applications",
    "helpdesk": "Applications",
    "wiki": "Applications",
    "drive": "Applications",
    "lms": "Applications",
    "gameplan": "Applications",
    "health": "Applications",
    "education": "Applications",
    "non_profit": "Applications",
    "lending": "Applications",
    "agriculture": "Applications",
    # Extensions
    "payments": "Extensions",
    # Integrations
    "ecommerce_integrations": "Integrations",
    "frappe_bigquery": "Integrations",
}

# Cloud category → new category.
# Order matters: Integrations is checked before Developer so apps tagged with both
# (e.g. a WhatsApp integration also tagged Developer) go to Integrations.
CLOUD_TO_NEW: dict[str, str] = {
    "Localization": "Compliance",
    "Integrations": "Integrations",
    "CRM Integration": "Integrations",
    "Communication": "Integrations",
    "Storage": "Integrations",
    "Developer": "Developer Tools",
    "Themes": "Utilities",
    "Utilities": "Utilities",
    "Utility": "Utilities",
    "Productivity": "Utilities",
    "Mobile": "Utilities",
    # Everything else (Accounting, Business, Extension, Compliance, etc.) → Extensions
}

LOCALISATION_KEYWORDS = [
    "india", "egypt", "uae", "nigeria", "kenya", "mexico", "brazil", "saudi",
    "qatar", "bahrain", "kuwait", "oman", "jordan", "pakistan", "indonesia",
    "vietnam", "thailand", "malaysia", "singapore", "switzerland", "germany",
    "france", "italy", "portugal", "turkey", "iran", "morocco", "ethiopia",
    "ghana", "tanzania", "zambia", "rwanda", "burundi", "angola", "cameroon",
    "chile", "colombia", "argentina", "peru", "ecuador", "myanmar", "nepal",
    "israel", "algeria", "tunisia", "south_africa", "zimbabwe", "botswana",
    "mauritius", "localization", "localisation",
]
INTEGRATION_KEYWORDS = [
    "whatsapp", "telegram", "slack", "twilio", "stripe", "razorpay", "paypal",
    "shopify", "woocommerce", "amazon", "google", "microsoft", "salesforce",
    "hubspot", "mailchimp", "sendgrid", "zoom", "teams", "discord", "github",
    "gitlab", "jira", "trello", "quickbooks", "xero", "tally", "sap",
    "aws", "azure", "gcp", "connector", "webhook", "zapier", "bigquery",
    "razorpayx", "paytm", "phonepe",
]


def classify(app: dict) -> str:
    name = app["name"]
    combined = " ".join(filter(None, [name, app.get("title"), app.get("description")])).lower()
    cloud_cats = app.get("categories") or []

    if name in KNOWN:
        return KNOWN[name]

    for cloud_cat, new_cat in CLOUD_TO_NEW.items():
        if cloud_cat in cloud_cats:
            return new_cat

    if cloud_cats:
        return "Extensions"

    # No cloud categories — keyword fallback
    if any(kw in combined for kw in LOCALISATION_KEYWORDS):
        return "Compliance"
    if any(kw in combined for kw in INTEGRATION_KEYWORDS):
        return "Integrations"
    return "Extensions"


def main() -> None:
    apps = json.loads(REGISTRY.read_text())
    for app in apps:
        app["category"] = classify(app)

    REGISTRY.write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n")

    from collections import Counter
    dist = Counter(a["category"] for a in apps)
    print(f"Wrote {len(apps)} apps to {REGISTRY}")
    for cat in ["Applications", "Extensions", "Integrations", "Compliance", "Developer Tools", "Utilities"]:
        print(f"  {dist[cat]:3d}  {cat}")


if __name__ == "__main__":
    main()
