from rest_framework import serializers

from model_hub.models.eval_groups import EvalGroup
from model_hub.schema.eval_group import PageType
from tracer.serializers.filters import StrictInputSerializer


class EvalGroupSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = EvalGroup
        fields = [
            "id",
            "name",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
            "description",
            "created_by",
            "is_sample",
        ]
        read_only_fields = ["organization", "workspace"]

    def get_created_by(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.created_by:
            return obj.created_by.name
        return obj.organization.name if obj.organization else "Future-agi Built"


class ApplyEvalGroupRequestSerializer(StrictInputSerializer):
    eval_group_id = serializers.UUIDField()
    filters = serializers.DictField(
        child=serializers.JSONField(),
        required=False,
        default=dict,
    )
    page_id = serializers.ChoiceField(choices=[page.value for page in PageType])
    mapping = serializers.DictField(child=serializers.JSONField())
    deselected_evals = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    params = serializers.DictField(
        child=serializers.JSONField(),
        required=False,
        default=dict,
    )
