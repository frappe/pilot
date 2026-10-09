from scripts.cloud_settings_simulator import create_simulator


def test_simulated_metadata_and_billing_flow():
    client = create_simulator("http://127.0.0.1:8124").test_client()
    token = client.put("/latest/api/token").text
    metadata = client.get(
        "/latest/meta-data/attributes/pilot-central", headers={"X-metadata-token": token}
    ).json
    headers = {"X-Pilot-Token": metadata["central_auth_token"]}
    prefix = "/api/method/central.billing.api.billing_api."

    def call(method, data=None):
        response = client.post(prefix + method, json=data or {}, headers=headers)
        assert response.status_code == 200
        return response.json["message"]

    assert not call("get_billing_summary")["profile_complete"]
    call("save_billing_profile", {"legal_name": "Sample Company"})
    assert call("get_billing_summary")["profile_complete"]
    checkout = call("create_payment_method_checkout")
    assert not call("confirm_payment_method_checkout", checkout)["active"]
    client.post("/checkout/" + checkout["reference"])
    assert call("confirm_payment_method_checkout", checkout)["active"]
    assert call("get_billing_summary")["payment_method"]
    call("remove_payment_method")
    assert call("get_billing_summary")["payment_method"] is None
    client.post("/reset")
    assert not call("get_billing_summary")["profile_complete"]


def test_simulation_rejects_unknown_calls_and_can_fail():
    client = create_simulator("http://127.0.0.1:8124").test_client()
    assert client.get("/latest/meta-data/attributes/pilot-central").status_code == 401
    prefix = "/api/method/central.billing.api.billing_api."
    assert client.get(prefix + "get_billing_summary").status_code == 401
    token = client.put("/latest/api/token").text
    headers = {"X-Pilot-Token": token}
    assert client.post(prefix + "change_plan", headers=headers).status_code == 404
    client.post("/failure")
    assert client.get(prefix + "get_billing_summary", headers=headers).status_code == 503
