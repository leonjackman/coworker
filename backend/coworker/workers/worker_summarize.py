"""子代理结果摘要逻辑。"""

from typing import Any


# 摘要 prompt 模板（语言自适应）
_SUMMARY_PROMPTS: dict[str, str] = {
    "zh": (
        "请将以下输出精简摘要为 500-1500 字的关键结论。\n"
        "保留最重要的事实、数据和分析，去掉冗余描述。\n"
        "只输出摘要，不要额外说明。\n\n"
        "---\n\n{content}"
    ),
    "en": (
        "Summarize the key findings from the following output in 500-1500 words.\n"
        "Preserve the most important facts, data, and analysis; "
        "remove redundant descriptions.\n"
        "Output only the summary, no extra explanations.\n\n"
        "---\n\n{content}"
    ),
}


async def summarize_result(
    content: str,
    max_chars: int,
    language: str,
    data_dir: Any | None,
    primary_llm: Any,
) -> str:
    """对超长输出做轻量摘要。

    - 不超过 max_chars：直接返回
    - 超过 max_chars：用 summarizer 候选链摘要，失败则规则截断
    """
    from coworker.agent.middleware import _summarizer_candidates

    if len(content) <= max_chars:
        return content

    # 获取 summarizer 候选模型链（复用现有的 _summarizer_candidates）
    candidates = _summarizer_candidates(data_dir, primary_llm)
    if not candidates:
        # 无可用模型 → 规则截断
        return _rule_truncate(content, max_chars)

    prompt = _SUMMARY_PROMPTS.get(language, _SUMMARY_PROMPTS["en"]).format(
        content=content
    )

    for model in candidates:
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda m=model, p=prompt: m.invoke(p, max_tokens=4096, temperature=0),
            )
            summary = str(getattr(response, "content", "") or "").strip()
            if summary and len(summary) > 50:
                return summary
        except Exception:  # noqa: BLE001
            continue

    # 所有模型失败 → 规则截断
    return _rule_truncate(content, max_chars)


def _rule_truncate(content: str, max_chars: int) -> str:
    """规则截断（摘要失败时的 fallback）。"""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n\n[子代理输出已截断（共 {len(content)} 字符）]"
