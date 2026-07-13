from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify

from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench
from pilot.core.diagnostics import DiagnosticReport, DiagnosticRunner

diagnostics_bp = Blueprint("diagnostics", __name__)


@diagnostics_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        checks = DiagnosticRunner(Bench(config, bench_root)).run()
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(json.loads(DiagnosticReport(config.name, checks).to_json()))
