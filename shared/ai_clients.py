"""
AI provider clients for Claude, GPT, and Gemini.
"""
import os
from typing import Any, Dict, List, Optional

import httpx
from httpx import AsyncClient

from shared.logging import get_logger

logger = get_logger(__name__)


class AnthropicClient:
    """Claude API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client: Optional[AsyncClient] = None
        self.base_url = "https://api.anthropic.com"
        self.default_model = "claude-3-opus-20240229"

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            timeout=120.0
        )
        logger.info("Connected to Anthropic API")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7,
        system: Optional[str] = None
    ) -> Optional[str]:
        """Generate completion."""
        try:
            data = {
                "model": model or self.default_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            if system:
                data["system"] = system

            response = await self.client.post("/v1/messages", json=data)
            
            if response.status_code == 200:
                result = response.json()
                return result["content"][0]["text"]
            else:
                logger.error(f"Anthropic API error: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error calling Anthropic API: {e}")
            return None


class OpenAIClient:
    """OpenAI GPT API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client: Optional[AsyncClient] = None
        self.base_url = "https://api.openai.com/v1"
        self.default_model = "gpt-4-turbo-preview"

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            timeout=120.0
        )
        logger.info("Connected to OpenAI API")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7
    ) -> Optional[str]:
        """Generate completion."""
        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": model or self.default_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"OpenAI API error: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")
            return None


class GeminiClient:
    """Google Gemini API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client: Optional[AsyncClient] = None
        self.base_url = "https://generativelanguage.googleapis.com"
        self.default_model = "gemini-pro"

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            params={"key": self.api_key},
            timeout=120.0
        )
        logger.info("Connected to Gemini API")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()

    async def complete(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7
    ) -> Optional[str]:
        """Generate completion."""
        try:
            response = await self.client.post(
                f"/v1beta/models/{model or self.default_model}:generateContent",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": temperature
                    }
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                return result["candidates"][0]["content"]["parts"][0]["text"]
            else:
                logger.error(f"Gemini API error: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error calling Gemini API: {e}")
            return None


class AIClientManager:
    """Manager for AI clients with budget tracking."""

    def __init__(self):
        self.anthropic: Optional[AnthropicClient] = None
        self.openai: Optional[OpenAIClient] = None
        self.gemini: Optional[GeminiClient] = None
        self.daily_budget_usd = float(os.getenv("MODEL_DAILY_BUDGET_USD", "25"))
        self.usage_usd = 0.0
        
        # Approximate costs per 1K tokens (as of 2024)
        self.costs_per_1k = {
            "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
            "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
            "gemini-pro": {"input": 0.00025, "output": 0.0005}
        }

    async def connect(self) -> None:
        """Initialize all configured AI clients."""
        if api_key := os.getenv("ANTHROPIC_API_KEY"):
            self.anthropic = AnthropicClient(api_key)
            await self.anthropic.connect()
            
        if api_key := os.getenv("OPENAI_API_KEY"):
            self.openai = OpenAIClient(api_key)
            await self.openai.connect()
            
        if api_key := os.getenv("GOOGLE_API_KEY"):
            self.gemini = GeminiClient(api_key)
            await self.gemini.connect()

    async def disconnect(self) -> None:
        """Disconnect all clients."""
        if self.anthropic:
            await self.anthropic.disconnect()
        if self.openai:
            await self.openai.disconnect()
        if self.gemini:
            await self.gemini.disconnect()

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a completion."""
        if model not in self.costs_per_1k:
            return 0.01  # Default conservative estimate
            
        costs = self.costs_per_1k[model]
        input_cost = (input_tokens / 1000) * costs["input"]
        output_cost = (output_tokens / 1000) * costs["output"]
        return input_cost + output_cost

    def check_budget(self, estimated_cost: float) -> bool:
        """Check if we're within budget."""
        if self.usage_usd + estimated_cost > self.daily_budget_usd:
            logger.warning(f"Budget exceeded: {self.usage_usd + estimated_cost:.2f} > {self.daily_budget_usd}")
            return False
        return True

    def track_usage(self, cost: float) -> None:
        """Track usage against budget."""
        self.usage_usd += cost
        logger.info(f"AI usage: ${self.usage_usd:.2f} / ${self.daily_budget_usd:.2f}")

    async def complete_with_claude(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Optional[str]:
        """Complete using Claude with budget tracking."""
        if not self.anthropic:
            logger.error("Anthropic client not configured")
            return None
            
        # Rough token estimation
        estimated_tokens = sum(len(m["content"]) for m in messages) // 4
        estimated_cost = self.estimate_cost(
            kwargs.get("model", self.anthropic.default_model),
            estimated_tokens,
            kwargs.get("max_tokens", 4000)
        )
        
        if not self.check_budget(estimated_cost):
            return None
            
        result = await self.anthropic.complete(messages, **kwargs)
        
        if result:
            self.track_usage(estimated_cost)
            
        return result

    async def complete_with_gpt(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Optional[str]:
        """Complete using GPT with budget tracking."""
        if not self.openai:
            logger.error("OpenAI client not configured")
            return None
            
        # Rough token estimation
        estimated_tokens = sum(len(m["content"]) for m in messages) // 4
        estimated_cost = self.estimate_cost(
            kwargs.get("model", self.openai.default_model),
            estimated_tokens,
            kwargs.get("max_tokens", 4000)
        )
        
        if not self.check_budget(estimated_cost):
            return None
            
        result = await self.openai.complete(messages, **kwargs)
        
        if result:
            self.track_usage(estimated_cost)
            
        return result

    async def complete_with_gemini(
        self,
        prompt: str,
        **kwargs
    ) -> Optional[str]:
        """Complete using Gemini with budget tracking."""
        if not self.gemini:
            logger.error("Gemini client not configured")
            return None
            
        # Rough token estimation
        estimated_tokens = len(prompt) // 4
        estimated_cost = self.estimate_cost(
            kwargs.get("model", self.gemini.default_model),
            estimated_tokens,
            kwargs.get("max_tokens", 4000)
        )
        
        if not self.check_budget(estimated_cost):
            return None
            
        result = await self.gemini.complete(prompt, **kwargs)
        
        if result:
            self.track_usage(estimated_cost)
            
        return result