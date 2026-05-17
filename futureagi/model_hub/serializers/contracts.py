from rest_framework import serializers

from model_hub.serializers.performance_report import PerformanceReportSerializer


class ModelHubEmptyRequestSerializer(serializers.Serializer):
    pass


class ModelHubJSONResponseSerializer(serializers.Serializer):
    status = serializers.JSONField(required=False)
    message = serializers.CharField(required=False, allow_blank=True)
    result = serializers.JSONField(required=False)
    data = serializers.JSONField(required=False)
    error = serializers.JSONField(required=False)
    detail = serializers.JSONField(required=False)


class ModelHubPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = serializers.ListField(child=serializers.JSONField())


class ModelHubErrorResponseSerializer(serializers.Serializer):
    status = serializers.JSONField(required=False)
    message = serializers.JSONField(required=False)
    error = serializers.JSONField(required=False)
    detail = serializers.JSONField(required=False)


MODEL_HUB_ERROR_RESPONSES = {
    400: ModelHubErrorResponseSerializer,
    403: ModelHubErrorResponseSerializer,
    404: ModelHubErrorResponseSerializer,
    409: ModelHubErrorResponseSerializer,
    500: ModelHubErrorResponseSerializer,
}


class AIEvalWriterRequestSerializer(serializers.Serializer):
    description = serializers.CharField()
    output_format = serializers.ChoiceField(
        choices=["prompt", "messages"],
        required=False,
        default="prompt",
    )


class AIEvalWriterResultSerializer(serializers.Serializer):
    prompt = serializers.CharField()


class AIEvalWriterResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AIEvalWriterResultSerializer()


class CustomAIModelCreateRequestSerializer(serializers.Serializer):
    model_provider = serializers.CharField()
    model_name = serializers.CharField()
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)
    config_json = serializers.JSONField(required=False, default=dict)
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CustomAIModelUpdateRequestSerializer(serializers.Serializer):
    model_name = serializers.CharField(required=False, allow_blank=True)
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)


class CustomAIModelDefaultMetricRequestSerializer(serializers.Serializer):
    metric_id = serializers.UUIDField()


class CustomAIModelBaselineRequestSerializer(serializers.Serializer):
    environment = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    model_version = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class CustomAIModelEditRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    model_name = serializers.CharField(required=False, allow_blank=True)
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)
    config_json = serializers.JSONField(required=False, default=dict)
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CustomAIModelCreateResponseDataSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class CustomAIModelCreateResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = CustomAIModelCreateResponseDataSerializer()


class CustomMetricMutationRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    model_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    prompt = serializers.CharField(required=False, allow_blank=True)
    metric_type = serializers.CharField(required=False, allow_blank=True)
    evaluation_type = serializers.CharField(required=False, allow_blank=True)
    datasets = serializers.JSONField(required=False)


class CustomMetricTestRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField()


class CustomMetricTestResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    prompts = serializers.JSONField(required=False)


class CustomMetricListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    evaluation_type = serializers.CharField()


class CustomMetricListResponseSerializer(serializers.Serializer):
    metrics = CustomMetricListItemSerializer(many=True)


class MetricTagOptionSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.CharField()


class EmbeddingModelOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class KnowledgeBaseEmbeddingModelsResponseSerializer(serializers.Serializer):
    status = serializers.IntegerField()
    result = EmbeddingModelOptionSerializer(many=True)


class LegacyKnowledgeBaseMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    kb_id = serializers.UUIDField(required=False)
    files = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class LegacyKnowledgeBaseFilesRequestSerializer(serializers.Serializer):
    kb_id = serializers.UUIDField()
    search = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    sort = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    page_number = serializers.IntegerField(required=False, default=0)
    page_size = serializers.IntegerField(required=False, default=10)


class OptimizeDatasetMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)
    model = serializers.UUIDField(required=False)
    optimize_type = serializers.CharField(required=False, allow_blank=True)
    environment = serializers.CharField(required=False, allow_blank=True)
    version = serializers.CharField(required=False, allow_blank=True)
    metrics = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetKnowledgeBaseRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    knowledge_base_metrics = serializers.JSONField(required=False)
    knowledge_base_filters = serializers.JSONField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetOperationRequestSerializer(serializers.Serializer):
    filters = serializers.JSONField(required=False)
    order = serializers.JSONField(required=False)
    page_number = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    columns = serializers.JSONField(required=False)
    prompt_template = serializers.CharField(required=False, allow_blank=True)
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class PerformanceQueryRequestSerializer(serializers.Serializer):
    datasets = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    breakdown = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    agg_by = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)


class PerformanceDetailsRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    page = serializers.IntegerField(required=False, default=1)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)


class PerformanceExportRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    metric = serializers.JSONField(required=False)


class PerformanceTagDistributionRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    agg_by = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)
    graph_type = serializers.CharField(required=False, allow_blank=True)


class PerformanceDetailsResponseSerializer(serializers.Serializer):
    result = serializers.ListField(child=serializers.JSONField())
    processing_count = serializers.IntegerField()
    count = serializers.IntegerField()
    is_next = serializers.BooleanField()
    page = serializers.IntegerField()


class PerformanceReportPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = PerformanceReportSerializer(many=True)
    total_pages = serializers.IntegerField(required=False)
    current_page = serializers.IntegerField(required=False)


class VectorDBColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    sub_type = serializers.CharField()
    api_key = serializers.CharField()
    collection_name = serializers.CharField(required=False, allow_blank=True)
    url = serializers.CharField(required=False, allow_blank=True)
    search_type = serializers.CharField(required=False, allow_blank=True)
    key = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False)
    index_name = serializers.CharField(required=False, allow_blank=True)
    top_k = serializers.IntegerField(required=False)
    namespace = serializers.CharField(required=False, allow_blank=True)
    embedding_config = serializers.JSONField(required=False)
    concurrency = serializers.IntegerField(required=False, default=5)
    query_key = serializers.CharField(required=False, allow_blank=True)
    vector_length = serializers.IntegerField(required=False)


class ExtractJsonColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    json_key = serializers.CharField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    concurrency = serializers.IntegerField(required=False, default=5)


class ClassifyColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    labels = serializers.ListField(child=serializers.CharField())
    language_model_id = serializers.CharField(required=False, default="gpt-4o")
    concurrency = serializers.IntegerField(required=False, default=5)
    new_column_name = serializers.CharField(required=False, allow_blank=True)


class ExtractEntitiesRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    instruction = serializers.CharField()
    language_model_id = serializers.CharField(required=False, default="gpt-4")
    concurrency = serializers.IntegerField(required=False, default=5)
    new_column_name = serializers.CharField(required=False, allow_blank=True)


class AddApiColumnRequestSerializer(serializers.Serializer):
    column_name = serializers.CharField()
    config = serializers.JSONField()
    concurrency = serializers.IntegerField(required=False, default=5)


class PythonCodeColumnRequestSerializer(serializers.Serializer):
    code = serializers.CharField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    concurrency = serializers.IntegerField(required=False, default=5)


class ConditionalColumnRequestSerializer(serializers.Serializer):
    config = serializers.ListField(child=serializers.JSONField())
    new_column_name = serializers.CharField()
    concurrency = serializers.IntegerField(required=False, default=5)


class RerunOperationRequestSerializer(serializers.Serializer):
    operation_type = serializers.CharField()
    config = serializers.JSONField(required=False)


class PreviewDatasetOperationRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField(required=False)
    json_key = serializers.CharField(required=False, allow_blank=True)
    labels = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    instruction = serializers.CharField(required=False, allow_blank=True)
    language_model_id = serializers.CharField(required=False, allow_blank=True)
    config = serializers.JSONField(required=False)
    code = serializers.CharField(required=False, allow_blank=True)


class ExperimentFeedbackSubmitRequestSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(
        choices=[
            "retune",
            "recalculate_row",
            "recalculate_dataset",
            "retune_recalculate",
        ]
    )
    feedback_id = serializers.UUIDField()
    user_eval_metric_id = serializers.UUIDField()
    value = serializers.JSONField(required=False)
    explanation = serializers.CharField(required=False, allow_blank=True)
