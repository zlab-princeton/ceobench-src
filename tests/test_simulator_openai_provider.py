"""Regression tests for simulator-side OpenAI provider wiring."""

from types import SimpleNamespace

import pytest

from saas_bench.config import BenchmarkConfig
from saas_bench.customer_llm import (
    _call_openai_responses,
    judge_agent_social_post,
)
from saas_bench.server_entry import (
    _apply_simulator_llm_config,
    _create_simulator_openai_client,
)


def _openai_simulator_config() -> BenchmarkConfig:
    config = BenchmarkConfig()
    config.social_post_llm_provider = "openai"
    config.social_post_llm_model = "social-deployment"
    config.enterprise_llm_provider = "openai"
    config.enterprise_llm_model = "enterprise-deployment"
    return config


def test_simulator_openai_config_accepts_azure_credentials(monkeypatch):
    config = _openai_simulator_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

    meta = _apply_simulator_llm_config(config)

    assert meta["social_post_llm_provider"] == "openai"
    assert meta["social_post_llm_model"] == "social-deployment"
    assert meta["enterprise_llm_provider"] == "openai"
    assert meta["enterprise_llm_model"] == "enterprise-deployment"


def test_simulator_openai_client_uses_azure_when_endpoint_is_set(monkeypatch):
    config = _openai_simulator_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

    client = _create_simulator_openai_client(config)

    assert client.__class__.__name__ == "AzureOpenAI"


def test_simulator_openai_client_uses_openai_compatible_base_url(monkeypatch):
    config = _openai_simulator_config()
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example.test/v1")

    client = _create_simulator_openai_client(config)

    assert client.__class__.__name__ == "OpenAI"
    assert str(client.base_url).rstrip("/") == "https://llm.example.test/v1"


class _FakeResponses:
    def __init__(self, *, reject_reasoning: bool = False):
        self.reject_reasoning = reject_reasoning
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_reasoning and "reasoning" in kwargs:
            raise RuntimeError("unknown parameter: reasoning")
        return SimpleNamespace(
            output_text="SCORE: 0.7\nREASON: useful and specific",
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


class _FakeOpenAIClient:
    def __init__(self, *, reject_reasoning: bool = False):
        self.responses = _FakeResponses(reject_reasoning=reject_reasoning)


def test_openai_responses_retry_without_reasoning_when_endpoint_rejects_it():
    client = _FakeOpenAIClient(reject_reasoning=True)

    text, input_tokens, output_tokens = _call_openai_responses(
        client,
        model="custom-model",
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=50,
        reasoning_effort="low",
    )

    assert text == "SCORE: 0.7\nREASON: useful and specific"
    assert input_tokens == 11
    assert output_tokens == 7
    assert len(client.responses.calls) == 2
    assert "reasoning" in client.responses.calls[0]
    assert "reasoning" not in client.responses.calls[1]


def test_judge_agent_social_post_supports_openai_provider(monkeypatch):
    monkeypatch.delenv("BOSSBENCH_LLM_REPLAY_DB", raising=False)
    config = BenchmarkConfig()
    config.social_post_llm_provider = "openai"
    config.social_post_llm_model = "social-model"
    client = _FakeOpenAIClient()

    effect, reasoning, input_tokens, output_tokens = judge_agent_social_post(
        client,
        config,
        post_content="We shipped a focused reliability fix today.",
        group_id="developers",
        group_description="API developers",
        group_social_tone="pragmatic",
        subscriber_count=100,
        mrr=1000.0,
        recent_agent_posts=[],
    )

    assert effect == pytest.approx(0.7)
    assert "useful and specific" in reasoning
    assert input_tokens == 11
    assert output_tokens == 7
    assert client.responses.calls[0]["model"] == "social-model"
