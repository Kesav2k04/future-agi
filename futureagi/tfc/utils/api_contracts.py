from functools import wraps

import structlog
from django.conf import settings
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.response import Response

logger = structlog.get_logger(__name__)


def _serializer_class(serializer):
    if serializer is None:
        return None
    if isinstance(serializer, openapi.Response):
        return _serializer_class(serializer.schema)
    if isinstance(serializer, serializers.ListSerializer):
        return type(serializer)
    if isinstance(serializer, serializers.Serializer):
        return type(serializer)
    if isinstance(serializer, type) and issubclass(
        serializer, serializers.BaseSerializer
    ):
        return serializer
    return None


def _validate_response(view_name, serializer, response, strict):
    serializer_class = _serializer_class(serializer)
    if serializer_class is None:
        return

    validator = serializer_class(data=response.data)
    if not validator.is_valid(raise_exception=strict):
        logger.warning(
            "API response does not match declared serializer.",
            view_func=view_name,
            status_code=response.status_code,
            serializer_class=serializer_class.__name__,
            validation_errors=validator.errors,
        )


def validate_request_data(request, serializer_class):
    """Validate a function-based view request body with a declared serializer."""

    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def validate_query_params(request, serializer_class):
    """Validate a function-based view query string with a declared serializer."""

    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def validated_request(
    request_serializer=None,
    *,
    query_serializer=None,
    responses=None,
    strict_request_validation=True,
    strict_response_validation=False,
    **swagger_kwargs,
):
    """Document and validate a DRF view method from the same serializers.

    The serializer declared for Swagger is also the runtime validator. New
    endpoints should prefer this over a doc-only ``swagger_auto_schema`` plus
    ad-hoc ``request.data`` parsing.
    """

    def decorator(view_func):
        swagger_options = dict(swagger_kwargs)
        if request_serializer is not None:
            swagger_options["request_body"] = request_serializer
        if query_serializer is not None:
            swagger_options["query_serializer"] = query_serializer
        if responses is not None:
            swagger_options["responses"] = responses

        @swagger_auto_schema(**swagger_options)
        @wraps(view_func)
        def wrapper(self, request, *args, **kwargs):
            request.validated_data = {}
            request.validated_query_data = {}

            if query_serializer is not None:
                serializer = query_serializer(data=request.query_params)
                serializer.is_valid(raise_exception=True)
                request.validated_query_data = serializer.validated_data

            if request_serializer is not None:
                serializer = request_serializer(data=request.data)
                is_valid = serializer.is_valid(
                    raise_exception=strict_request_validation
                )
                if not is_valid and settings.DEBUG:
                    logger.warning(
                        "API request does not match declared serializer.",
                        view_func=view_func.__name__,
                        serializer_class=request_serializer.__name__,
                        validation_errors=serializer.errors,
                    )
                request.validated_data = serializer.validated_data

            response = view_func(self, request, *args, **kwargs)

            if not responses or not isinstance(response, Response):
                return response

            response_serializer = responses.get(response.status_code)
            if response_serializer is None:
                if strict_response_validation:
                    raise serializers.ValidationError(
                        f"Undocumented response status {response.status_code} "
                        f"for {view_func.__name__}."
                    )
                if settings.DEBUG:
                    logger.warning(
                        "API response status is not declared.",
                        view_func=view_func.__name__,
                        status_code=response.status_code,
                        declared_status_codes=sorted(responses),
                    )
                return response

            if strict_response_validation or settings.DEBUG:
                _validate_response(
                    view_func.__name__,
                    response_serializer,
                    response,
                    strict_response_validation,
                )

            return response

        return wrapper

    return decorator
