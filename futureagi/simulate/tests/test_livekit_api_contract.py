import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (
        _repo_root() / "api_contracts" / "openapi" / "swagger.json"
    ).open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_schema(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]


def _response_schema(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]


def _schema_ref_name(schema):
    return schema["$ref"].rsplit("/", 1)[-1]


def test_livekit_mutations_have_explicit_body_contracts():
    expected = {
        ("POST", "/simulate/api/livekit/transcripts/{call_id}/"): (
            "LiveKitTranscriptsRequest"
        ),
        ("PATCH", "/simulate/api/livekit/call-execution/{call_id}/"): (
            "LiveKitCallExecutionUpdateRequest"
        ),
        ("POST", "/simulate/api/livekit/temporal-signal/"): (
            "LiveKitTemporalSignalRequest"
        ),
        ("POST", "/simulate/api/livekit/validate-credentials/"): (
            "ValidateLiveKitCredentialsRequest"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _schema_ref_name(_body_schema(_operation(path, method))) == (
            definition_name
        )

    webhook_body = _body_schema(
        _operation("/simulate/api/livekit/webhook/", "POST")
    )
    assert webhook_body["type"] == "object"


def test_livekit_endpoints_have_explicit_response_contracts():
    expected_refs = {
        ("PATCH", "/simulate/api/livekit/call-execution/{call_id}/"): (
            "LiveKitOkResponse"
        ),
        ("POST", "/simulate/api/livekit/temporal-signal/"): (
            "LiveKitOkResponse"
        ),
        ("GET", "/simulate/api/livekit/listener-token/{call_id}/"): (
            "LiveKitListenerTokenResponse"
        ),
        ("POST", "/simulate/api/livekit/validate-credentials/"): (
            "ValidateLiveKitCredentialsResponse"
        ),
        ("POST", "/simulate/api/livekit/webhook/"): "LiveKitOkResponse",
    }
    expected_objects = {
        ("GET", "/simulate/api/livekit/call-config/{call_id}/", "200"),
        ("POST", "/simulate/api/livekit/transcripts/{call_id}/", "201"),
        ("GET", "/simulate/api/livekit/phone-resolution/{phone_number}/", "200"),
    }

    for (method, path), definition_name in expected_refs.items():
        assert _schema_ref_name(_response_schema(_operation(path, method))) == (
            definition_name
        )

    for method, path, status_code in expected_objects:
        assert (
            _response_schema(_operation(path, method), status_code)["type"]
            == "object"
        )


def test_livekit_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/api/livekit/call-config/{call_id}/",
        "/simulate/api/livekit/transcripts/{call_id}/",
        "/simulate/api/livekit/phone-resolution/{phone_number}/",
        "/simulate/api/livekit/call-execution/{call_id}/",
        "/simulate/api/livekit/temporal-signal/",
        "/simulate/api/livekit/listener-token/{call_id}/",
        "/simulate/api/livekit/validate-credentials/",
        "/simulate/api/livekit/webhook/",
    }
    report = _debt_report()

    body_debt = {
        item["path"] for item in report["mutation_endpoints_without_body_schema"]
    }
    response_debt = {
        item["path"] for item in report["operations_without_response_schema"]
    }

    assert body_debt.isdisjoint(covered_paths)
    assert response_debt.isdisjoint(covered_paths)
