"""Coworker agent package.

Split out of the former monolithic ``coworker/agents.py``:

* ``core`` — shared types, tool-arg schemas, message/context helpers;
* ``model_defaults`` — repetition penalty and LLM construction;
* ``prompts`` — system / phase / title prompts;
* ``middleware`` — phase gating, HITL approval, compaction, loop guards;
* ``graph`` — workspace tool set + ``create_agent`` graph builder;
* ``runtime`` — streaming runtimes + the runtime registry.

Import DAG (acyclic): ``model_defaults ← core ← {prompts, middleware, graph} ← runtime``.
"""

from .core import AgentStreamRuntime  # noqa: F401
from .runtime import AgentRuntimeRegistry  # noqa: F401
