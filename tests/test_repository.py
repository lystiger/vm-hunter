"""The repository's own conventions: skills are discoverable and env names are documented."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_skill_names_itself_and_says_when_to_use_it():
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skills
    for path in skills:
        front = path.read_text(encoding="utf-8").split("---")[1]
        assert re.search(rf"^name: {path.parent.name}$", front, re.M), path
        assert re.search(r"^description: .{20,}", front, re.M), path


def test_both_agent_clis_see_the_same_skills():
    for link in (".claude/skills", ".agents/skills"):
        assert (ROOT / link).resolve() == (ROOT / "skills").resolve()


def test_env_example_lists_names_without_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*", name), line
            assert value == "", line
