"""
futureagi/tracer/selectors.py

Selector functions for observability DB lookups.
All ORM queries must go through selectors — never inline in Celery tasks or services.
"""

import logging
from typing import Optional

from tracer.models.observability_provider import ObservabilityProvider
from tracer.services.observability_providers import ObservabilityService

logger = logging.getLogger(__name__)


def get_observability_provider(
    project_id: int, provider_name: str
) -> Optional[ObservabilityProvider]:
    """Return the active ObservabilityProvider for a project/provider pair, or None."""
    return ObservabilityProvider.objects.filter(
        project_id=project_id, provider=provider_name, is_active=True
    ).first()


def get_agent_api_key(project_id: int, provider_name: str) -> Optional[str]:
    """
    Resolve the agent API key for a given project and provider.

    Navigates: ObservabilityProvider -> AgentDefinition -> api_key
    Returns None (with log warning) if any link in the chain is missing.
    """
    provider = get_observability_provider(project_id, provider_name)
    if not provider:
        return None
    try:
        agent = ObservabilityService._get_agent_definition(provider)
        if not agent:
            return None
        return ObservabilityService._validate_agent_api_key(agent, provider, provider_name)
    except Exception:
        logger.warning(
            "get_agent_api_key: failed to resolve api_key for project=%s provider=%s",
            project_id, provider_name, exc_info=True,
        )
        return None
