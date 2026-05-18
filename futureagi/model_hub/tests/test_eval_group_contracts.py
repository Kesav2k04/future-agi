import uuid

import pytest
from rest_framework import status

from model_hub.serializers.eval_group import ApplyEvalGroupRequestSerializer


class TestApplyEvalGroupContracts:
    def test_apply_eval_group_accepts_canonical_payload(self):
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "eval_group_id": str(uuid.uuid4()),
                "page_id": "DATASET",
                "filters": {"dataset_id": str(uuid.uuid4())},
                "mapping": {"hypothesis": "input", "reference": "expected"},
                "params": {"k": 3},
                "deselected_evals": [str(uuid.uuid4())],
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["page_id"] == "DATASET"

    def test_apply_eval_group_rejects_legacy_aliases(self):
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "evalGroupId": str(uuid.uuid4()),
                "pageId": "DATASET",
                "filters": {"dataset_id": str(uuid.uuid4())},
                "mapping": {"hypothesis": "input", "reference": "expected"},
            }
        )

        assert not serializer.is_valid()
        assert "evalGroupId" in serializer.errors
        assert "pageId" in serializer.errors


@pytest.mark.integration
@pytest.mark.api
def test_apply_eval_group_api_rejects_legacy_aliases(auth_client):
    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/",
        {
            "evalGroupId": str(uuid.uuid4()),
            "pageId": "DATASET",
            "filters": {},
            "mapping": {},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
