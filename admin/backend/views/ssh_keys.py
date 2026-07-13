from __future__ import annotations

from flask import Blueprint, jsonify, request

from pilot.core.ssh_keys import AuthorizedKeysStore, SSHKey, SSHKeyError

ssh_keys_bp = Blueprint("ssh_keys", __name__)


def _serialize(key: SSHKey) -> dict:
    return {"fingerprint": key.fingerprint, "type": key.key_type, "comment": key.comment}


@ssh_keys_bp.route("/", methods=["GET"])
def list_keys():
    return jsonify({"keys": [_serialize(key) for key in AuthorizedKeysStore().list()]})


@ssh_keys_bp.route("/", methods=["POST"])
def add_key():
    public_key = (request.get_json(silent=True) or {}).get("public_key", "").strip()
    if not public_key:
        return jsonify({"ok": False, "error": "A public key is required."}), 400
    try:
        key = AuthorizedKeysStore().add(public_key)
    except SSHKeyError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "key": _serialize(key)})


@ssh_keys_bp.route("/", methods=["DELETE"])
def remove_key():
    fingerprint = (request.get_json(silent=True) or {}).get("fingerprint", "").strip()
    if not fingerprint:
        return jsonify({"ok": False, "error": "A fingerprint is required."}), 400
    try:
        AuthorizedKeysStore().remove(fingerprint)
    except SSHKeyError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})
