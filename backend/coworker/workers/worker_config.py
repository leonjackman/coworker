"""数据模型：Worker 运行配置、任务简报、结果。"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class WorkerConfig:
    """Worker 运行配置。"""

    timeout: int = 600                      # 超时（秒）
    max_output_chars: int = 2000            # 结果截断/摘要阈值
    max_concurrent: int = 4                 # 最大并发数
    language: str = "zh"                    # 摘要语言（zh/en）
    max_depth: int = 3                      # 委派深度上限（引擎级兜底：任何 spawn 路径超过此深度直接拒绝运行）
    memory_manager: Any = None              # 可选：子代理 memory（多 agent 模式用）
    memory_rel: str = ""                    # 可选：子代理 memory 相对路径

    @classmethod
    def for_single_agent(
        cls, language: str = "zh", max_concurrent: int | None = None
    ) -> "WorkerConfig":
        """单 agent 模式的默认配置。"""
        return cls(
            timeout=600,
            max_output_chars=2000,
            max_concurrent=max_concurrent or 4,
            language=language,
            max_depth=3,
        )

    @classmethod
    def for_delegation(
        cls,
        memory_manager: Any | None = None,
        memory_rel: str = "",
        max_depth: int | None = None,
        **kwargs: Any,
    ) -> "WorkerConfig":
        """多 agent 委派模式的默认配置。"""
        return cls(
            timeout=600,
            max_output_chars=2000,
            max_concurrent=4,
            language=kwargs.pop("language", "zh"),
            max_depth=max_depth or 3,
            memory_manager=memory_manager,
            memory_rel=memory_rel,
        )


@dataclass
class TaskBrief:
    """给 Worker 的任务简报。"""

    task: str                               # 任务描述（必需）
    context: str = ""                       # 额外上下文
    expected_output: str = ""               # 期望输出格式
    constraints: list[str] = field(default_factory=list)  # 约束条件


@dataclass
class WorkerResult:
    """Worker 执行结果。"""

    content: str                            # 最终输出（已摘要/截断）
    raw_length: int = 0                     # 原始长度
    was_truncated: bool = False             # 是否被截断
    artifacts: list[str] = field(default_factory=list)  # 产出文件路径（future）
    structured: Optional[dict] = None       # 可选结构化输出（future）
    success: bool = True                    # 是否成功
    error: str = ""                         # 错误信息
