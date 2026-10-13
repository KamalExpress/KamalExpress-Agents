"""
Universal agent and AI infrastructure detector.
Inspects existing agent configurations (.agents, .claude, .cursor, .gemini).
"""
from pathlib import Path
from typing import Any, Dict, List
from .base import BaseDetector


class AgentDetector(BaseDetector):
    """Detects existing agent configurations, rules, and skills."""

    def detect(self, project_root: Path) -> Dict[str, Any]:
        platforms: List[str] = []
        installed_skills: List[str] = []
        rule_files: List[str] = []

        # 1. Antigravity / Open Agent Standard (.agents/)
        agents_dir = project_root / ".agents"
        if agents_dir.is_dir():
            platforms.append("agents_standard")
            skills_dir = agents_dir / "skills"
            if skills_dir.is_dir():
                for p in skills_dir.iterdir():
                    if p.is_dir() and (p / "SKILL.md").exists():
                        installed_skills.append(p.name)

        # 2. Claude Code (.claude/ or CLAUDE.md)
        if (project_root / "CLAUDE.md").exists():
            platforms.append("claude_code")
            rule_files.append("CLAUDE.md")
        if (project_root / ".claude").is_dir():
            platforms.append("claude_code")

        # 3. Cursor (.cursor/ or .cursorrules)
        if (project_root / ".cursorrules").exists():
            platforms.append("cursor")
            rule_files.append(".cursorrules")
        if (project_root / ".cursor").is_dir():
            platforms.append("cursor")

        # 4. Gemini / Antigravity Global Rules
        for r in ["GEMINI.md", "AGENTS.md"]:
            if (project_root / r).exists():
                rule_files.append(r)

        return {
            "has_agent_infrastructure": len(platforms) > 0 or len(installed_skills) > 0,
            "detected_platforms": list(dict.fromkeys(platforms)),
            "installed_skills": installed_skills,
            "rule_files": rule_files,
        }
