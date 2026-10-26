"""
Asynchronous OpenRouter API Client with Model Fallback Cascade
Handles chat completions and tool-calling with zero native C/Rust dependencies.
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free models cascade for OpenRouter (tried in sequence upon failure/rate-limit)
DEFAULT_FREE_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "mistralai/mistral-small-24b-instruct-2501:free"
]


class OpenRouterClient:
    """Async HTTP client for OpenRouter API with tool calling and fallback resilience."""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.default_model = default_model or settings.OPENROUTER_MODEL or DEFAULT_FREE_MODELS[0]

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

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

        # Build model cascade list
        models_to_try = []
        if preferred_model:
            models_to_try.append(preferred_model)
        if self.default_model not in models_to_try:
            models_to_try.append(self.default_model)
        for m in DEFAULT_FREE_MODELS:
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
                    response = await client.post(OPENROUTER_URL, headers=headers, json=payload)

                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get("choices", [])
                        if choices:
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
                    # For client errors other than 429/404 (e.g. 400 Bad Request), don't loop endlessly
                    if response.status_code == 400:
                        raise e

        raise RuntimeError(f"All OpenRouter models in cascade failed. Last error: {last_error}")


# Singleton client
ai_client = OpenRouterClient()
