"""Skills package for the Coworker agent.

Implements the Agent Skills open standard (agentskills.io): a skill is a
directory containing ``SKILL.md`` with YAML frontmatter. Only ``name`` and
``description`` are required; the rest of the file is the skill body loaded
on demand.

Layout:

- :mod:`coworker.skills.skills` -- skill entry model, frontmatter parsing, validation
- :mod:`coworker.skills.skill_discovery` -- multi-root scanning + diagnostics
- :mod:`coworker.skills.skill_manager` -- list/get/scan/toggle persisted to JSON
- :mod:`coworker.skills.skill_middleware` -- injects skills into the agent graph

Skills are discovered from the workspace (``.agents/skills``, ``.coworker/skills``)
and the user config directory (``~/.agents/skills``, ``~/.coworker/skills``).
Only the skill catalog (name + description + location + version) is injected
into the system prompt; the full ``SKILL.md`` body is read on demand via the
existing ``read_file`` tool (progressive disclosure).
"""
