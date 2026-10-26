"""
Modular Skills Registry & Dynamic Recipe Loader
Parses markdown skills and injects domain-specific recipes into AI agent context.
"""

import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Base skills directory (defaults to project root / skills)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SKILLS_DIR = BASE_DIR / "skills"


@dataclass
class Skill:
    name: str
    description: str
    content: str
    triggers: List[str] = field(default_factory=list)
    references: Dict[str, str] = field(default_factory=dict)


class SkillRegistry:
    """Discovers, indexes, and dynamically extracts skill recipes for the AI agent."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
        self._skills: Dict[str, Skill] = {}
        self.load_skills()

    def load_skills(self) -> None:
        """Loads and indexes all skills in the skills directory."""
        self._skills.clear()
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory does not exist: {self.skills_dir}")
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                raw_text = skill_md.read_text(encoding="utf-8")
                name, description, triggers, body = self._parse_frontmatter(raw_text, skill_dir.name)

                # Also load any reference markdown files in references/ subdirectory
                refs = {}
                ref_dir = skill_dir / "references"
                if ref_dir.exists() and ref_dir.is_dir():
                    for ref_file in ref_dir.glob("*.md"):
                        try:
                            refs[ref_file.stem] = ref_file.read_text(encoding="utf-8")
                        except Exception as e:
                            logger.warning(f"Failed to load reference {ref_file}: {e}")

                self._skills[name] = Skill(
                    name=name,
                    description=description,
                    content=body,
                    triggers=triggers,
                    references=refs
                )
                logger.debug(f"Loaded skill: {name} ({len(triggers)} triggers, {len(refs)} references)")
            except Exception as e:
                logger.error(f"Error loading skill at {skill_dir}: {e}")

    @staticmethod
    def _parse_frontmatter(text: str, default_name: str) -> Tuple[str, str, List[str], str]:
        """Extracts YAML frontmatter (name, description, triggers) and body."""
        name = default_name
        description = ""
        triggers = []
        body = text

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2].strip()

                # Parse simple YAML lines
                for line in frontmatter.splitlines():
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()

                # Extract trigger words from description (e.g. Triggers on '...', '...')
                match = re.search(r"triggers on\s+([^\.]+)", description, re.IGNORECASE)
                if match:
                    raw_triggers = match.group(1)
                    # Find all quoted or comma-separated items
                    extracted = re.findall(r"'([^']+)'|\"([^\"]+)\"|([a-zA-Z0-9_\-]{3,})", raw_triggers)
                    for item in extracted:
                        val = (item[0] or item[1] or item[2]).strip().lower()
                        if val and len(val) > 2:
                            triggers.append(val)

        return name, description, triggers, body

    def get_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> List[str]:
        return list(self._skills.keys())

    def get_relevant_skills(self, user_prompt: str, max_chars: int = 6000) -> str:
        """
        Dynamically extracts and compiles relevant skill sections based on query keywords.
        Keeps prompt token usage low while injecting high-accuracy instructions.
        """
        lower_prompt = user_prompt.lower()
        matched_sections: List[str] = []

        # Keywords for matching
        for skill in self._skills.values():
            is_matched = False
            # Check skill name and description
            if skill.name in lower_prompt or any(t in lower_prompt for t in skill.triggers):
                is_matched = True

            # Check individual sections or sub-keywords
            relevant_snippets = []
            if is_matched:
                relevant_snippets.append(f"### Skill: {skill.name.upper()}\n{skill.content}")
            else:
                # Sub-section extraction if specific keywords match
                for section in skill.content.split("\n## "):
                    if not section.strip():
                        continue
                    sec_title = section.split("\n", 1)[0].lower()
                    # Check if section title keywords match prompt
                    words = [w for w in re.findall(r"[a-zA-Z0-9]{3,}", sec_title)]
                    if any(w in lower_prompt for w in words):
                        relevant_snippets.append(f"### From {skill.name.upper()}:\n## {section}")

            # Check references (e.g. recipes.md) if query asks for specific recipe
            for ref_name, ref_content in skill.references.items():
                for recipe_block in ref_content.split("\n## "):
                    block_title = recipe_block.split("\n", 1)[0].lower()
                    words = [w for w in re.findall(r"[a-zA-Z0-9]{3,}", block_title)]
                    if any(w in lower_prompt for w in words):
                        relevant_snippets.append(f"### Recipe ({skill.name}/{ref_name}):\n## {recipe_block}")

            if relevant_snippets:
                matched_sections.extend(relevant_snippets)

        if not matched_sections:
            # Fallback: Provide brief summary of all available skills
            summary = ["### Available Knowledge Skills:"]
            for skill in self._skills.values():
                summary.append(f"- **{skill.name}**: {skill.description[:120]}...")
            return "\n".join(summary)

        compiled = "\n\n---\n\n".join(matched_sections)
        if len(compiled) > max_chars:
            compiled = compiled[:max_chars] + "\n\n[... truncated for prompt efficiency ...]"
        return compiled

    @staticmethod
    def check_memory_safety(
        duration_sec: float,
        width: Optional[int],
        height: Optional[int],
        is_reverse: bool = False,
        is_termux: bool = True
    ) -> Tuple[bool, str]:
        """
        Enforces mobile host memory safety rules (especially for Nokia 6.1 Plus Snapdragon 636).
        Returns (is_safe, warning_or_error_message).
        """
        if not is_reverse:
            return True, ""

        # Reversal RAM safety validation
        w = width or 1920
        h = height or 1080
        total_pixels = w * h

        # If on mobile/termux and duration is long or resolution is high
        if is_termux or total_pixels >= 1920 * 1080:
            if duration_sec > 30.0:
                return (
                    False,
                    f"Video duration is {int(duration_sec)}s. Inverting/reversing frames uncompresses all frames into system RAM, "
                    f"which requires ~{int(duration_sec * 0.15)} GB RAM and will crash your device. "
                    f"Please trim the video to under 30 seconds before reversing."
                )
            if total_pixels > 1280 * 720 and duration_sec > 15.0:
                return (
                    True,
                    f"High resolution ({w}x{h}) reverse requires automatic downscaling to 720p HD to prevent device Out-Of-Memory."
                )

        return True, ""


# Global singleton instance
skill_registry = SkillRegistry()
