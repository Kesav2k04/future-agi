from pathlib import Path

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request


class _DemoRequestSerializer(serializers.Serializer):
    name = serializers.CharField()


class _DemoResultSerializer(serializers.Serializer):
    name = serializers.CharField()


class _DemoQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1)


class _DemoResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = _DemoResultSerializer()


class _DemoView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
        strict_response_validation=True,
    )
    def post(self, request):
        return Response(
            {"status": True, "result": {"name": request.validated_data["name"]}}
        )


class _BadResponseView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
        strict_response_validation=True,
    )
    def post(self, request):
        return Response({"status": True, "result": {"wrong": "shape"}})


class _ListResponseView(APIView):
    @validated_request(
        responses={200: _DemoResultSerializer(many=True)},
        strict_response_validation=True,
    )
    def get(self, request):
        return Response([{"name": "Future AGI"}])


class _QueryView(APIView):
    @validated_request(query_serializer=_DemoQuerySerializer)
    def get(self, request):
        return Response({"page": request.validated_query_data["page"]})


class _BadListResponseView(APIView):
    @validated_request(
        responses={200: _DemoResultSerializer(many=True)},
        strict_response_validation=True,
    )
    def get(self, request):
        return Response([{"wrong": "shape"}])


def _swagger():
    import json

    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    import json

    repo_root = Path(__file__).resolve().parents[3]
    with (
        repo_root
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _body_ref(path, method):
    body_param = next(
        parameter
        for parameter in _swagger()["paths"][path][method]["parameters"]
        if parameter.get("in") == "body"
    )
    return body_param["schema"].get("$ref")


def _response_ref(path, method, status_code="200"):
    response = _swagger()["paths"][path][method]["responses"][status_code]
    return response.get("schema", {}).get("$ref")


def test_validated_request_uses_declared_serializer_at_runtime():
    factory = APIRequestFactory()

    response = _DemoView.as_view()(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 200
    assert response.data == {"status": True, "result": {"name": "Future AGI"}}


def test_validated_request_rejects_invalid_body():
    factory = APIRequestFactory()

    response = _DemoView.as_view()(factory.post("/", {"wrong": "shape"}))

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["result"] == "name: This field is required."
    assert response.data["message"] == "name: This field is required."
    assert response.data["details"] == {"name": ["This field is required."]}


def test_validated_request_rejects_invalid_query_with_error_envelope():
    factory = APIRequestFactory()

    response = _QueryView.as_view()(factory.get("/", {"page": "zero"}))

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["result"] == "page: A valid integer is required."
    assert response.data["message"] == "page: A valid integer is required."
    assert response.data["details"] == {"page": ["A valid integer is required."]}


def test_validated_request_can_strictly_validate_responses():
    factory = APIRequestFactory()

    response = _BadResponseView.as_view()(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 400
    assert "name" in response.data["result"]


def test_validated_request_can_validate_many_response_serializers():
    factory = APIRequestFactory()

    response = _ListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 200
    assert response.data == [{"name": "Future AGI"}]


def test_validated_request_rejects_invalid_many_responses():
    factory = APIRequestFactory()

    response = _BadListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 400
    assert "name" in response.data[0]


def test_core_management_endpoints_have_runtime_backed_contracts():
    expected_responses = {
        ("/health/", "get"): "#/definitions/HealthCheckResponse",
        ("/api/deployment-info/", "get"): "#/definitions/DeploymentInfoResponse",
        ("/api/public/health", "get"): "#/definitions/LangfuseHealthResponse",
        ("/api/public/traces", "get"): "#/definitions/LangfuseTracesResponse",
        ("/call-websocket/", "post"): "#/definitions/CallWebsocketResponse",
    }
    for (path, method), expected_ref in expected_responses.items():
        assert _response_ref(path, method) == expected_ref

    assert _body_ref("/call-websocket/", "post") == "#/definitions/CallWebsocketRequest"
    assert (
        _response_ref("/call-websocket/", "post", "400")
        == "#/definitions/CallWebsocketErrorResponse"
    )


def test_management_api_auto_schema_adds_default_error_contracts():
    assert (
        _response_ref("/model-hub/annotation-queues/", "get", "default")
        == "#/definitions/ManagementAPIErrorResponse"
    )

    assert (
        _response_ref("/call-websocket/", "post", "400")
        == "#/definitions/CallWebsocketErrorResponse"
    )
    assert (
        _response_ref("/call-websocket/", "post", "default")
        == "#/definitions/ManagementAPIErrorResponse"
    )


def test_management_api_has_no_missing_error_response_contract_debt():
    report = _debt_report()

    assert report["summary"]["operations_without_error_response_schema"] == 0
