"""
Asynchronous OpenRouter API Client with Dynamic Free Models Fetcher & Fallback Cascade
Handles chat completions, tool-calling, and live model discovery from OpenRouter.
"""

import time
import logging
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Default free models cascade (used as immediate fallback if network fetch fails)
DEFAULT_FREE_MODELS = [
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "thinkingmachines/inkling:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-2.5-2.6b:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free"
]

# In-memory cache for live free models
_cached_free_models: List[Dict[str, Any]] = []
_last_models_fetch_time: float = 0.0
_CACHE_TTL_SECONDS: float = 600.0  # 10 minutes cache


async def fetch_openrouter_free_models(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches the latest free models from OpenRouter's public API (https://openrouter.ai/models?variant=free).
    Excludes non-chat models (embeddings, rerankers, moderation) and caches results for 10 minutes.
    """
    global _cached_free_models, _last_models_fetch_time
    now = time.time()

    if not force_refresh and _cached_free_models and (now - _last_models_fetch_time < _CACHE_TTL_SECONDS):
        return _cached_free_models

    free_list: List[Dict[str, Any]] = []

    # Always ensure openrouter/free is offered as the top default option
    free_list.append({
        "id": "openrouter/free",
        "name": "OpenRouter Free Router (Auto)",
        "context_length": 262144
    })

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(OPENROUTER_MODELS_URL)
            if resp.status_code == 200:
                raw_data = resp.json().get("data", [])
                for item in raw_data:
                    mid = item.get("id", "")
                    pricing = item.get("pricing", {})
                    try:
                        prompt_price = float(pricing.get("prompt", 1))
                        comp_price = float(pricing.get("completion", 1))
                    except (ValueError, TypeError):
                        prompt_price = 1.0
                        comp_price = 1.0

                    # Check if free
                    is_free = (":free" in mid) or (prompt_price == 0.0 and comp_price == 0.0)
                    if not is_free:
                        continue

                    # Filter out embedding, rerank, and moderation models
                    mid_lower = mid.lower()
                    if any(bad in mid_lower for bad in ("embed", "rerank", "moderation", "guard", "whisper", "safety")):
                        continue

                    # Clean model name
                    raw_name = item.get("name") or mid
                    # Strip redundant (free) suffix if present for clean UI display
                    clean_name = raw_name.replace("(free)", "").strip()

                    # Avoid duplicate openrouter/free
                    if mid == "openrouter/free":
                        continue

                    free_list.append({
                        "id": mid,
                        "name": clean_name,
                        "context_length": item.get("context_length", 0)
                    })

                _cached_free_models = free_list
                _last_models_fetch_time = now
                logger.info(f"Successfully fetched {len(free_list)} free chat models from OpenRouter.")
                return free_list

    except Exception as e:
        logger.warning(f"Could not fetch models from OpenRouter API: {e}. Using fallback free list.")

    # Fallback to static list if network error
    if not _cached_free_models:
        for mid in DEFAULT_FREE_MODELS:
            free_list.append({
                "id": mid,
                "name": mid.split("/")[-1].replace(":free", "").replace("-", " ").title(),
                "context_length": 128000
            })
        _cached_free_models = free_list

    return _cached_free_models


class OpenRouterClient:
    """Async HTTP client for OpenRouter API with tool calling and fallback resilience."""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.default_model = default_model or settings.OPENROUTER_MODEL or DEFAULT_FREE_MODELS[0]

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def set_model(self, model_name: str) -> None:
        """Sets the active model."""
        if model_name:
            self.default_model = model_name
            settings.OPENROUTER_MODEL = model_name

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        preferred_model: Optional[str] = None,
        timeout: float = 35.0
    ) -> Dict[str, Any]:
        """
        Sends chat completion request to OpenRouter.
        Tries preferred model, then cascades through available free models on transient errors.
        """
        if not self.is_configured:
            raise ValueError("OPENROUTER_API_KEY is not configured in settings or environment.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/codebysnorlax/Video-Optmizer-Bot",
            "X-Title": "Telegram Video Optimizer Bot",
            "Content-Type": "application/json"
        }

        # Build model cascade list starting with user's preferred / default model
        models_to_try = []
        if preferred_model:
            models_to_try.append(preferred_model)
        if self.default_model and self.default_model not in models_to_try:
            models_to_try.append(self.default_model)

        # Append cached/known free models to cascade
        known_models = [m["id"] for m in _cached_free_models] or DEFAULT_FREE_MODELS
        for m in known_models:
            if m not in models_to_try:
                models_to_try.append(m)

        last_error = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for model_name in models_to_try:
                payload: Dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": 0.2
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"

                try:
                    logger.debug(f"Calling OpenRouter model: {model_name}...")
                    response = await client.post(OPENROUTER_CHAT_URL, headers=headers, json=payload)

                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get("choices") or []
                        if choices and len(choices) > 0:
                            return choices[0].get("message", {})
                        logger.warning(f"OpenRouter model {model_name} returned 200 with empty choices: {data}")

                    # If rate limited (429) or service unavailable (502, 503, 404), try next model in cascade
                    if response.status_code in (429, 502, 503, 404):
                        logger.warning(f"OpenRouter model {model_name} failed with status {response.status_code}: {response.text}. Trying fallback model...")
                        last_error = f"HTTP {response.status_code}: {response.text}"
                        continue
                    else:
                        response.raise_for_status()

                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    logger.warning(f"Network/timeout error with model {model_name}: {e}. Trying fallback...")
                    last_error = str(e)
                    continue
                except httpx.HTTPStatusError as e:
                    logger.error(f"HTTP error for model {model_name}: {e}")
                    last_error = str(e)
                    if response.status_code == 400:
                        raise e

        raise RuntimeError(f"All OpenRouter models in cascade failed. Last error: {last_error}")


# Singleton client
ai_client = OpenRouterClient()
