"""
futureagi/tracer/utils/vapi_recording.py

Authenticated Vapi recording helpers.
Downloads recording bytes via Bearer-authenticated Vapi API endpoints
(GET /call/{id}/mono-recording etc.), following 302 redirects to signed URLs.
"""

import logging
from typing import Optional

import requests

from tracer.constants.external_endpoints import (
    VAPI_CALL_MONO_RECORDING_URL,
    VAPI_CALL_STEREO_RECORDING_URL,
)

logger = logging.getLogger(__name__)


def download_recording_via_auth(
    endpoint_url: str, call_id: str, api_key: str
) -> Optional[bytes]:
    """
    Fetch a Vapi call recording via Bearer-authenticated redirect.

    Args:
        endpoint_url: One of VAPI_CALL_MONO_RECORDING_URL etc.
        call_id: The Vapi call ID (from raw_log.id).
        api_key: The Vapi API key (resolved via Selector).

    Returns:
        Raw bytes of the recording, or None on any HTTP error (fail-open).

    Logs errors without exposing the API key.
    """
    url = endpoint_url.format(call_id=call_id)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        logger.error(
            "Vapi recording download failed for call_id=%s: %s",
            call_id, exc, exc_info=True,
        )
        return None
