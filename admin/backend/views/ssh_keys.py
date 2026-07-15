from __future__ import annotations

from flask import Blueprint, jsonify, request

from admin.backend.api_contract import error_response
from pilot.core.ssh_keys import (
    AuthorizedKeysStore,
    InvalidSSHKeyError,
    LastSSHKeyError,
    SSHKey,
    SSHKeyAlreadyExistsError,
    SSHKeyNotFoundError,
)

ssh_keys_bp = Blueprint("ssh_keys", __name__)


def _serialize(key: SSHKey) -> dict:
    return {"fingerprint": key.fingerprint, "type": key.key_type, "comment": key.comment}


@ssh_keys_bp.route("/", methods=["GET"])
def list_keys():
    return jsonify({"keys": [_serialize(key) for key in AuthorizedKeysStore().list()]})


@ssh_keys_bp.route("/", methods=["POST"])
def add_key():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    public_key = data.get("public_key", "")
    if not isinstance(public_key, str):
        return error_response("invalid_ssh_key", "Public key must be a string.", 422)
    public_key = public_key.strip()
    if not public_key:
        return error_response("invalid_ssh_key", "A public key is required.", 422)
    try:
        key = AuthorizedKeysStore().add(public_key)
    except SSHKeyAlreadyExistsError:
        return error_response("ssh_key_already_exists", "That key is already authorized.", 409)
    except InvalidSSHKeyError as error:
        return error_response("invalid_ssh_key", str(error), 422)
    return jsonify({"ok": True, "key": _serialize(key)})


@ssh_keys_bp.route("/", methods=["DELETE"])
def remove_key():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    fingerprint = data.get("fingerprint", "")
    if not isinstance(fingerprint, str):
        return error_response("invalid_fingerprint", "Fingerprint must be a string.", 422)
    fingerprint = fingerprint.strip()
    if not fingerprint:
        return error_response("invalid_fingerprint", "A fingerprint is required.", 422)
    try:
        AuthorizedKeysStore().remove(fingerprint)
    except SSHKeyNotFoundError:
        return error_response("ssh_key_not_found", "SSH key was not found.", 404)
    except LastSSHKeyError as error:
        return error_response("ssh_key_removal_rejected", str(error), 409)
    return jsonify({"ok": True})
