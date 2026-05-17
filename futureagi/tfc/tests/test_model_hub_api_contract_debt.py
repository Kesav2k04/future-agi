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


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    schema = responses[status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and schema.get("items", {}).get("$ref"):
        return f"{schema['items']['$ref'].rsplit('/', 1)[-1]}[]"
    raise AssertionError(f"Unexpected response schema: {schema}")


def _response_has_schema(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    return "schema" in responses[status_code]


def test_model_hub_ai_writer_and_custom_model_apis_stay_out_of_contract_debt():
    report = _debt_report()
    protected_paths = {
        "/model-hub/ai-eval-writer/",
        "/model-hub/columns/{column_id}/operation-config/",
        "/model-hub/columns/{column_id}/rerun-operation/",
        "/model-hub/custom-models/",
        "/model-hub/custom-models/list/",
        "/model-hub/custom-models/{id}/",
        "/model-hub/custom_models/create/",
        "/model-hub/custom_models/edit/",
        "/model-hub/custom_models/update-baseline/{id}/",
        "/model-hub/custom-metric/all/{model_id}/",
        "/model-hub/custom-metric/create/",
        "/model-hub/custom-metric/tag-options/{metric_id}/",
        "/model-hub/custom-metric/test/",
        "/model-hub/custom-metric/update/",
        "/model-hub/custom-metric/{model_id}/",
        "/model-hub/datasets/{dataset_id}/add-api-column/",
        "/model-hub/datasets/{dataset_id}/add_vector_db_column/",
        "/model-hub/datasets/{dataset_id}/classify-column/",
        "/model-hub/datasets/{dataset_id}/conditional-column/",
        "/model-hub/datasets/{dataset_id}/extract-entities/",
        "/model-hub/datasets/{dataset_id}/preview/{operation_type}/",
        "/model-hub/develops/{dataset_id}/extract-json-column/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        "/model-hub/kb/",
        "/model-hub/kb/supported-embedding-models",
        "/model-hub/kb/supported_embedding_models/",
        "/model-hub/kb/{id}/",
        "/model-hub/knowledge-base/",
        "/model-hub/knowledge-base/files/",
        "/model-hub/knowledge-base/get/",
        "/model-hub/knowledge-base/list/",
        "/model-hub/optimize-dataset/kb/{optim_id}/",
        "/model-hub/optimize-dataset/knowledge-base/",
        "/model-hub/optimize-dataset/{model_id}/",
        "/model-hub/optimize-dataset/{model_id}/column-config/",
        "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/{optimization_id}/",
        "/model-hub/optimisation/create/",
        "/model-hub/optimisation/update/{id}/",
        "/model-hub/performance/detail/{id}/",
        "/model-hub/performance/export/{id}/",
        "/model-hub/performance/options/{model_id}/",
        "/model-hub/performance/report/{model_id}/",
        "/model-hub/performance/report/{model_id}/{report_id}/",
        "/model-hub/performance/tag-distribution/{model_id}/",
        "/model-hub/performance/{id}/",
    }

    body_gaps = {
        item["path"]
        for item in report["mutation_endpoints_without_body_schema"]
        if item["group"] == "model-hub"
    }
    response_gaps = {
        item["path"]
        for item in report["operations_without_response_schema"]
        if item["group"] == "model-hub"
    }

    assert protected_paths.isdisjoint(body_gaps)
    assert protected_paths.isdisjoint(response_gaps)


def test_model_hub_ai_writer_and_custom_model_mutations_have_request_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterRequest",
        ("POST", "/model-hub/columns/{column_id}/rerun-operation/"): (
            "RerunOperationRequest"
        ),
        ("POST", "/model-hub/custom-models/{id}/"): (
            "CustomAIModelUpdateRequest"
        ),
        ("POST", "/model-hub/custom_models/create/"): (
            "CustomAIModelCreateRequest"
        ),
        ("PATCH", "/model-hub/custom_models/edit/"): "CustomAIModelEditRequest",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "CustomAIModelBaselineRequest"
        ),
        ("POST", "/model-hub/custom-metric/create/"): (
            "CustomMetricMutationRequest"
        ),
        ("POST", "/model-hub/custom-metric/test/"): "CustomMetricTestRequest",
        ("POST", "/model-hub/custom-metric/update/"): (
            "CustomMetricMutationRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add-api-column/"): (
            "AddApiColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add_vector_db_column/"): (
            "VectorDBColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/classify-column/"): (
            "ClassifyColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/conditional-column/"): (
            "ConditionalColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/extract-entities/"): (
            "ExtractEntitiesRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"): (
            "PreviewDatasetOperationRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/extract-json-column/"): (
            "ExtractJsonColumnRequest"
        ),
        ("POST", "/model-hub/experiments/v2/{experiment_id}/feedback/"): "Feedback",
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        ): "ExperimentFeedbackSubmitRequest",
        ("POST", "/model-hub/kb/"): "KnowledgeBaseCreate",
        ("PUT", "/model-hub/kb/{id}/"): "KnowledgeBase",
        ("POST", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseMutationRequest"
        ),
        ("PATCH", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseMutationRequest"
        ),
        ("POST", "/model-hub/knowledge-base/files/"): (
            "LegacyKnowledgeBaseFilesRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/knowledge-base/"): (
            "OptimizeDatasetKnowledgeBaseRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/"): (
            "OptimizeDatasetMutationRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "OptimizeDatasetOperationRequest"
        ),
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        ("POST", "/model-hub/optimisation/create/"): "OptimizationDataset",
        ("PUT", "/model-hub/optimisation/create/"): "OptimizationDataset",
        ("POST", "/model-hub/optimisation/update/{id}/"): "OptimizationDataset",
        ("PUT", "/model-hub/optimisation/update/{id}/"): "OptimizationDataset",
        ("POST", "/model-hub/performance/detail/{id}/"): (
            "PerformanceDetailsRequest"
        ),
        ("POST", "/model-hub/performance/export/{id}/"): (
            "PerformanceExportRequest"
        ),
        ("POST", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportCreate"
        ),
        ("POST", "/model-hub/performance/tag-distribution/{model_id}/"): (
            "PerformanceTagDistributionRequest"
        ),
        ("POST", "/model-hub/performance/{id}/"): "PerformanceQueryRequest",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_model_hub_ai_writer_and_custom_model_endpoints_have_response_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterResponse",
        ("GET", "/model-hub/columns/{column_id}/operation-config/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/columns/{column_id}/rerun-operation/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/custom-models/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/list/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom_models/create/"): (
            "CustomAIModelCreateResponse"
        ),
        ("GET", "/model-hub/custom_models/edit/"): "ModelHubJSONResponse",
        ("PATCH", "/model-hub/custom_models/edit/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/custom-metric/all/{model_id}/"): (
            "CustomMetricListResponse"
        ),
        ("POST", "/model-hub/custom-metric/create/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/custom-metric/tag-options/{metric_id}/"): (
            "MetricTagOption[]"
        ),
        ("POST", "/model-hub/custom-metric/test/"): "CustomMetricTestResponse",
        ("POST", "/model-hub/custom-metric/update/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/custom-metric/{model_id}/"): "ModelHubPaginatedResponse",
        ("POST", "/model-hub/datasets/{dataset_id}/add-api-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add_vector_db_column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/classify-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/conditional-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/extract-entities/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/extract-json-column/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/experiments/v2/{experiment_id}/feedback/"): (
            "ModelHubJSONResponse"
        ),
        (
            "GET",
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        ): "ModelHubJSONResponse",
        ("GET", "/model-hub/kb/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/kb/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/kb/supported-embedding-models"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/supported_embedding_models/"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/{id}/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/kb/{id}/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("PATCH", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/knowledge-base/files/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/get/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/list/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/optimize-dataset/kb/{optim_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/knowledge-base/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/"): (
            "ModelHubPaginatedResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "ModelHubJSONResponse"
        ),
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        ("GET", "/model-hub/optimize-dataset/{model_id}/{optimization_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimisation/create/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/optimisation/create/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/optimisation/update/{id}/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/optimisation/update/{id}/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/performance/detail/{id}/"): (
            "PerformanceDetailsResponse"
        ),
        ("GET", "/model-hub/performance/options/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportPaginatedResponse"
        ),
        ("POST", "/model-hub/performance/report/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/performance/tag-distribution/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_model_hub_performance_endpoints_with_dynamic_payloads_have_response_contracts():
    expected = [
        ("POST", "/model-hub/performance/{id}/"),
        ("POST", "/model-hub/performance/export/{id}/"),
    ]

    for method, path in expected:
        assert _response_has_schema(_operation(path, method))


def test_model_hub_performance_report_detail_exposes_supported_methods_only():
    assert set(_swagger()["paths"]["/model-hub/performance/report/{model_id}/{report_id}/"]) == {
        "delete",
        "parameters",
    }
