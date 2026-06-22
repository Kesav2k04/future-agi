from rest_framework import serializers


JSON_VALUE_SCHEMA = {
    "x-json-value": True,
    "description": "Any valid JSON value.",
}


class JsonValueField(serializers.JSONField):
    """Arbitrary JSON value field for response data with mixed JSON shapes."""

    class Meta:
        swagger_schema_fields = JSON_VALUE_SCHEMA


class StringOrObjectField(serializers.JSONField):
    """Field that accepts either a plain string or a JSON object.

    Emits x-string-or-object in the OpenAPI schema, which the contract
    generator maps to z.union([z.string(), z.object({}).passthrough()]).
    """

    class Meta:
        swagger_schema_fields = {
            "x-string-or-object": True,
            "description": "String or JSON object.",
        }
