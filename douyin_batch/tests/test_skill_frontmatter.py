"""
Validate the SKILL.md files in the workspace.

A SKILL.md must:
* live in ``.trae/skills/<name>/SKILL.md``
* have a YAML frontmatter with ``name`` and ``description``
* have a non-empty body

This test is intentionally lenient about the frontmatter parser
so it works on any Python version without a PyYAML dependency.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = ROOT / ".trae" / "skills"


FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z",
    re.DOTALL,
)


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser: only handles ``key: value`` and ``key: "value"``."""
    out: dict = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # strip surrounding quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


class TestSkillFrontmatter(unittest.TestCase):
    """Every SKILL.md must have a valid frontmatter + non-empty body."""

    def _all_skills(self):
        if not SKILLS_DIR.exists():
            return []
        return sorted(p for p in SKILLS_DIR.glob("*/SKILL.md"))

    def test_at_least_one_skill(self):
        self.assertTrue(self._all_skills(),
                        f"no SKILL.md found under {SKILLS_DIR}")

    def test_each_skill_has_required_fields(self):
        for path in self._all_skills():
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                m = FRONTMATTER_RE.match(text)
                self.assertIsNotNone(
                    m, f"{path} is missing ---...--- frontmatter"
                )
                meta, body = m.group(1), m.group(2)
                parsed = _parse_simple_yaml(meta)
                self.assertIn("name", parsed, f"{path} missing 'name'")
                self.assertIn("description", parsed,
                              f"{path} missing 'description'")
                self.assertTrue(parsed["name"].strip(),
                                f"{path} empty 'name'")
                self.assertTrue(parsed["description"].strip(),
                                f"{path} empty 'description'")
                # Description must mention when to invoke (helps the model)
                self.assertIn("Invoke", parsed["description"],
                              f"{path} description must include 'Invoke'")
                # Body must be non-trivial
                self.assertGreater(len(body.strip()), 200,
                                   f"{path} body is too short")

    def test_skill_directory_name_matches_name_field(self):
        for path in self._all_skills():
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                m = FRONTMATTER_RE.match(text)
                parsed = _parse_simple_yaml(m.group(1))
                self.assertEqual(
                    parsed["name"], path.parent.name,
                    f"{path} name={parsed['name']!r} != dir {path.parent.name!r}",
                )


if __name__ == "__main__":
    unittest.main()
