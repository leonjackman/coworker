"""Provider max-output-token cap + goal LLM construction tests.

Guards the 3b5bffff audit fixes:
- every provider resolves an effective per-request output cap (default 8192);
- known/custom values persist and clamp;
- the streaming runtimes build a goal LLM (temperature 0.3) and a normal LLM,
  both carrying the cap and a repetition penalty on self-hosted endpoints.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agents import GOAL_TEMPERATURE, _provider_llm_kwargs  # noqa: E402
from coworker.providers import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_MAX,
    MAX_OUTPUT_TOKENS_MIN,
    ProviderEntry,
    ProviderManager,
)


def _entry(**kwargs) -> ProviderEntry:
    base = dict(
        id="p1", name="p1", provider_type="custom", base_url="http://192.168.1.100:8000/v1",
        api_key="", model="qwen3.6-35b", enabled=True,
    )
    base.update(kwargs)
    return ProviderEntry(**base)


def test_public_provider_defaults_to_8192(tmp_path: Path):
    pm = ProviderManager(tmp_path / "providers.json", tmp_path)
    pub = pm.public_provider(_entry(max_output_tokens=0))
    assert pub["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS == 8192


def test_public_provider_reflects_custom_value(tmp_path: Path):
    pm = ProviderManager(tmp_path / "providers.json", tmp_path)
    pub = pm.public_provider(_entry(max_output_tokens=16384))
    assert pub["max_output_tokens"] == 16384


def test_add_provider_persists_and_clamps(tmp_path: Path):
    pm = ProviderManager(tmp_path / "providers.json", tmp_path)
    created = pm.add_provider(
        name="vllm", provider_type="custom", base_url="http://127.0.0.1:8000/v1",
        model="m", max_output_tokens=2_000_000,
    )
    assert created["max_output_tokens"] == MAX_OUTPUT_TOKENS_MAX
    created2 = pm.add_provider(
        name="neg", provider_type="custom", base_url="http://127.0.0.1:8001/v1",
        model="m", max_output_tokens=-5,
    )
    # Negative clamps to 0 → treated as unset → effective default.
    assert created2["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_update_provider_sets_and_resets(tmp_path: Path):
    pm = ProviderManager(tmp_path / "providers.json", tmp_path)
    created = pm.add_provider(
        name="vllm", provider_type="custom", base_url="http://127.0.0.1:8000/v1",
        model="m", max_output_tokens=8192,
    )
    pid = created["id"]
    updated = pm.update_provider(pid, max_output_tokens=32768)
    assert updated["max_output_tokens"] == 32768
    # 0 explicitly resets back to the default.
    updated = pm.update_provider(pid, max_output_tokens=0)
    assert updated["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_provider_llm_kwargs_applies_cap_and_local_penalty():
    # Self-hosted (private IP) provider → cap + repetition penalty.
    kwargs = _provider_llm_kwargs("qwen3.6-35b", _entry(max_output_tokens=0), 0, "http://192.168.1.100:8000/v1")
    assert kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert kwargs["repetition_penalty"] == 1.05
    # Cloud provider → cap still applies, but NO repetition penalty (OpenAI/DeepSeek reject it).
    cloud = _entry(base_url="https://api.openai.com/v1", provider_type="openai", max_output_tokens=128000)
    kwargs = _provider_llm_kwargs("gpt-5.1", cloud, 0, "https://api.openai.com/v1")
    assert kwargs["max_tokens"] == 128000
    assert kwargs["repetition_penalty"] is None


def test_goal_llm_uses_higher_temperature():
    # §2c: goal rounds sample at GOAL_TEMPERATURE instead of greedy 0.
    assert GOAL_TEMPERATURE == 0.3
    normal = _provider_llm_kwargs("qwen3.6-35b", _entry(), 0, "http://192.168.1.100:8000/v1")
    goal = _provider_llm_kwargs("qwen3.6-35b", _entry(), GOAL_TEMPERATURE, "http://192.168.1.100:8000/v1")
    assert normal["temperature"] == 0
    assert goal["temperature"] == 0.3


def test_reasoning_create_passes_max_tokens():
    from coworker.agents import ReasonPreservingChatOpenAI

    llm = ReasonPreservingChatOpenAI.create(model="m", temperature=0, api_key="k", base_url="http://127.0.0.1:1/v1", max_tokens=4096)
    assert llm.max_tokens == 4096
    llm_unset = ReasonPreservingChatOpenAI.create(model="m", temperature=0, api_key="k", base_url="http://127.0.0.1:1/v1")
    assert llm_unset.max_tokens is None
