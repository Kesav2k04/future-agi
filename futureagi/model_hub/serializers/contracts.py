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
