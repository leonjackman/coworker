"""Tests for the Coworker skills subsystem (discovery, manager, middleware)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworker.skills.skill_discovery import SkillScanner
from coworker.skills.skill_manager import SKILLS_CONFIG_FILENAME, SkillManager
from coworker.skills.skills import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    format_skills_prompt,
    load_skill_from_file,
    parse_frontmatter,
    validate_name,
)


def write_skill(root: Path, name: str, description: str = "Does the thing when asked", **extra: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append("# Body")
    lines.append("1. step one")
    lines.append("2. step two")
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return skill_file


@pytest.fixture()
def skill_root(tmp_path: Path) -> Path:
    root = tmp_path / ".coworker" / "skills"
    root.mkdir(parents=True)
    return root


@pytest.fixture()
def manager(tmp_path: Path, skill_root: Path) -> SkillManager:
    return SkillManager(
        tmp_path,
        tmp_path,
        scanner=SkillScanner(workspace_root=tmp_path, user_skills_dir=tmp_path),
    )


class TestFrontmatter:
    def test_parses_frontmatter_and_body(self):
        content = "---\nname: foo\ndescription: bar\n---\n\nBody here"
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter["name"] == "foo"
        assert body == "Body here"

    def test_no_frontmatter_returns_empty(self):
        frontmatter, body = parse_frontmatter("# Just a heading\n")
        assert frontmatter == {}
        assert "heading" in body

    def test_malformed_yaml_is_lenient(self):
        frontmatter, body = parse_frontmatter("---\nname: [unclosed\n---\n\nbody")
        assert frontmatter == {}


class TestValidation:
    def test_valid_name(self):
        assert validate_name("git-release") == []

    def test_name_rules(self):
        assert validate_name("") != []
        assert validate_name("Upper") != []
        assert validate_name("has--double") != []
        assert validate_name("-leading") != []
        assert validate_name("trailing-") != []
        assert validate_name("x" * (MAX_NAME_LENGTH + 1)) != []

    def test_description_too_long_rejected(self, skill_root: Path):
        from coworker.skills.skills import load_skill_from_file

        skill_file = write_skill(skill_root, "long", description="x" * (MAX_DESCRIPTION_LENGTH + 1))
        entry, diagnostics = load_skill_from_file(skill_file, "test")
        assert entry is None
        assert any("description exceeds" in d.message for d in diagnostics)


class TestDiscovery:
    def test_finds_skill(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        result = manager.refresh()
        assert [s.name for s in result.skills] == ["my-skill"]
        assert result.skills[0].source == "coworker-project"
        assert result.skills[0].enabled is True

    def test_missing_description_is_invalid(self, skill_root: Path, manager: SkillManager):
        (skill_root / "bad").mkdir()
        (skill_root / "bad" / "SKILL.md").write_text("---\nname: bad\n---\n\nbody\n", encoding="utf-8")
        result = manager.refresh()
        assert result.skills == []
        assert any(d.type == "invalid" and d.name == "bad" for d in result.diagnostics)

    def test_single_file_md_is_not_a_skill_in_agents_roots(self, tmp_path: Path, manager: SkillManager):
        # Root-level .md files are not skills under the strict Agent Skills layout.
        (tmp_path / ".coworker" / "skills" / "note.md").write_text("---\nname: note\ndescription: x\n---\n\nbody\n", encoding="utf-8")
        result = manager.refresh()
        assert all(s.name != "note" for s in result.skills)

    def test_duplicate_physical_root_deduped(self, tmp_path: Path, skill_root: Path):
        write_skill(skill_root, "my-skill")
        m = SkillManager(tmp_path, tmp_path, scanner=SkillScanner(workspace_root=tmp_path, user_skills_dir=tmp_path))
        result = m.refresh()
        assert len([s for s in result.skills if s.name == "my-skill"]) == 1

    def test_ignores_hidden_and_node_modules(self, skill_root: Path, manager: SkillManager):
        (skill_root / ".hidden").mkdir()
        (skill_root / ".hidden" / "SKILL.md").write_text("---\nname: hidden\ndescription: x\n---\n\nbody\n", encoding="utf-8")
        (skill_root / "node_modules").mkdir()
        (skill_root / "node_modules" / "SKILL.md").write_text("---\nname: nm\ndescription: x\n---\n\nbody\n", encoding="utf-8")
        result = manager.refresh()
        assert result.skills == []

    def test_nested_skill_dir_found(self, skill_root: Path, manager: SkillManager):
        (skill_root / "category").mkdir()
        write_skill(skill_root / "category", "nested-skill")
        result = manager.refresh()
        assert [s.name for s in result.skills] == ["nested-skill"]

    def test_max_bytes_enforced(self, skill_root: Path):
        scanner = SkillScanner(workspace_root=skill_root.parent.parent, user_skills_dir=skill_root.parent.parent, max_bytes=16)
        skill_dir = skill_root / "big"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("x" * 64, encoding="utf-8")
        result = scanner.scan()
        assert any(d.type == "invalid" for d in result.diagnostics)


class TestManager:
    def test_toggle_enabled_persists(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        manager.refresh()
        updated = manager.set_enabled("my-skill", False)
        assert updated is not None and updated.enabled is False
        assert manager.list(enabled_only=True) == []
        assert "my-skill" in json.loads((manager.data_dir / SKILLS_CONFIG_FILENAME).read_text())["disabled"]

    def test_set_enabled_updates_cached_list(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        manager.refresh()
        manager.set_enabled("my-skill", False)
        assert [s.name for s in manager.list(enabled_only=True)] == []
        manager.set_enabled("my-skill", True)
        assert [s.name for s in manager.list(enabled_only=True)] == ["my-skill"]

    def test_permission_deny_filters_injection(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        manager.refresh()
        manager.set_permission("my-skill", "deny")
        assert manager.injection_list() == []
        assert manager.list()  # still discoverable, just not injected

    def test_invalid_permission_rejected(self, manager: SkillManager):
        with pytest.raises(ValueError):
            manager.set_permission("nope", "banana")

    def test_unknown_skill_returns_none(self, manager: SkillManager):
        assert manager.get("ghost") is None
        assert manager.set_enabled("ghost", False) is None

    def test_read_body_returns_markdown(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        manager.refresh()
        body, base_dir = manager.read_body("my-skill")
        assert "step one" in body
        assert base_dir.endswith("my-skill")

    def test_read_body_works_even_when_disabled(self, skill_root: Path, manager: SkillManager):
        # Management preview must still read a disabled skill's body;
        # agent-facing gating happens in injection_list / the /skill command.
        write_skill(skill_root, "my-skill")
        manager.refresh()
        manager.set_enabled("my-skill", False)
        body, _ = manager.read_body("my-skill")
        assert body is not None
        assert "step one" in body
        assert manager.injection_list() == []


class TestPromptFormat:
    def test_available_skills_xml(self, skill_root: Path, manager: SkillManager):
        write_skill(skill_root, "my-skill")
        manager.refresh()
        prompt = format_skills_prompt(manager.injection_list())
        assert "<available_skills>" in prompt
        assert "<name>my-skill</name>" in prompt
        assert "read_file tool" in prompt
        assert "</available_skills>" in prompt

    def test_empty_list_renders_empty(self):
        assert format_skills_prompt([]) == ""


class TestLoadSkillFromFile:
    def test_escape_handled(self, skill_root: Path):
        skill_file = write_skill(skill_root, "esc", description='a & b < c > "d"')
        entry, diagnostics = load_skill_from_file(skill_file, "test")
        assert entry is not None
        assert not diagnostics
