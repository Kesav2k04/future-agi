from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status


def _assert_field_error(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


def _mock_completion(content):
    """Build a litellm.completion return value carrying `content`."""
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


@pytest.mark.django_db
class TestAIEvalWriterContracts:
    def test_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "prompt",
                "outputFormat": "legacy camel alias",
            },
            format="json",
        )

        _assert_field_error(response, "outputFormat")

    def test_rejects_invalid_output_format_before_model_call(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "json",
            },
            format="json",
        )

        _assert_field_error(response, "output_format")


@pytest.mark.django_db
class TestAIEvalWriterGeneration:
    """Behavior of the generation path (litellm mocked — no network)."""

    def test_test_data_format_returns_generated_json(self, auth_client):
        # The new output_format must be accepted end-to-end and its result
        # returned verbatim. This is the regression that strict request-contract
        # enforcement broke when the enum lacked "test_data".
        generated = '{"output": "Revenue grew 40%.", "context": "Flat YoY."}'
        with patch("litellm.completion", return_value=_mock_completion(generated)):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {
                    "description": "Generate a failing case for variables: output, context",
                    "output_format": "test_data",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["prompt"] == generated

    def test_strips_markdown_fence_from_model_output(self, auth_client):
        fenced = '```json\n{"output": "x", "context": "y"}\n```'
        with patch("litellm.completion", return_value=_mock_completion(fenced)):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "test data please", "output_format": "test_data"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["prompt"] == '{"output": "x", "context": "y"}'

    def test_prompt_format_still_works(self, auth_client):
        # Regression guard for the pre-existing default format after adding the
        # new branch.
        generated = "You are an expert evaluator. Check {{output}} against {{ground_truth}}."
        with patch("litellm.completion", return_value=_mock_completion(generated)):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "check factual accuracy"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["prompt"] == generated
