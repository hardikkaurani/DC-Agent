"""AI Provider Factory with fallback support."""

import logging

from dc_agent.config import AIProviderType, Settings, get_settings
from dc_agent.providers.base import AIProviderError, IAIProvider
from dc_agent.providers.gemini import GeminiProvider
from dc_agent.providers.ollama import OllamaProvider

logger = logging.getLogger(__name__)


def get_ai_provider(
    provider_type: AIProviderType | str | None = None,
    settings: Settings | None = None,
) -> IAIProvider:
    """Instantiate and return the configured AI provider."""
    cfg = settings or get_settings()
    target_type = provider_type or cfg.ai_provider

    if isinstance(target_type, str):
        try:
            target_type = AIProviderType(target_type.lower())
        except ValueError as err:
            raise AIProviderError(f"Unsupported AI provider type: {target_type}") from err

    if target_type == AIProviderType.GEMINI:
        try:
            return GeminiProvider(settings=cfg)
        except Exception as e:
            logger.warning("Failed to initialize Gemini provider: %s", e)
            # Check if user requested fallback to Ollama
            if cfg.ollama_base_url:
                logger.info("Attempting fallback to Ollama provider...")
                try:
                    return OllamaProvider(settings=cfg)
                except Exception as oe:
                    logger.warning("Ollama fallback also failed: %s", oe)
            raise e

    if target_type == AIProviderType.OLLAMA:
        return OllamaProvider(settings=cfg)

    raise AIProviderError(f"Unsupported AI provider type: {target_type}")
