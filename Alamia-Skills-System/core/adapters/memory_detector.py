"""
Universal project memory and context detector.
Discovers session handoffs, architecture baselines, and project worklogs across arbitrary conventions.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
import re
from .base import BaseDetector


class MemoryDetector(BaseDetector):
    """Detects project session memory, handoffs, and architectural specifications."""

    def detect(self, project_root: Path) -> Dict[str, Any]:
        handoff_dir: Optional[Path] = None
        architecture_dir: Optional[Path] = None
        worklogs: List[str] = []

        # 1. Discover session handoff directory
        potential_handoff_dirs = [
            project_root / ".ai" / "transient" / "handoffs",
            project_root / ".ai" / "handoffs",
            project_root / "docs" / "handoffs",
            project_root / "docs" / "sessions",
            project_root / "handoffs",
            project_root / ".scratch" / "handoffs",
            project_root / ".notes" / "handoffs",
        ]

        for p in potential_handoff_dirs:
            if p.is_dir() and any(p.glob("*.md")):
                handoff_dir = p
                break

        # 2. Discover architecture directory
        potential_arch_dirs = [
            project_root / ".ai" / "permanent" / "architecture",
            project_root / ".ai" / "architecture",
            project_root / "docs" / "architecture",
            project_root / "docs" / "specs",
            project_root / "docs" / "design",
            project_root / "architecture",
            project_root / "specs",
        ]

        for p in potential_arch_dirs:
            if p.is_dir() and any(p.glob("*.md")):
                architecture_dir = p
                break

        # 3. Discover worklog / roadmap files
        for wl in ["CHANGELOG.md", "ROADMAP.md", "TODO.md", "ARCHITECTURE.md"]:
            if (project_root / wl).is_file():
                worklogs.append(wl)

        # 4. Extract latest handoff info if handoff directory was found
        latest_handoff_info = None
        if handoff_dir:
            md_files = sorted(
                list(handoff_dir.glob("*.md")),
                key=lambda f: f.name.lower()
            )
            if md_files:
                latest_file = md_files[-1]
                latest_handoff_info = self._parse_handoff_file(latest_file, project_root)

        return {
            "has_memory_system": handoff_dir is not None or architecture_dir is not None,
            "handoff_dir": str(handoff_dir.relative_to(project_root)).replace("\\", "/") if handoff_dir else None,
            "architecture_dir": str(architecture_dir.relative_to(project_root)).replace("\\", "/") if architecture_dir else None,
            "worklogs": worklogs,
            "latest_handoff": latest_handoff_info,
        }

    def _parse_handoff_file(self, file_path: Path, root: Path) -> Dict[str, Any]:
        """Generic parser for a markdown handoff file."""
        rel_path = str(file_path.relative_to(root)).replace("\\", "/")
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return {"file": rel_path, "title": file_path.stem, "pending_items": []}

        # Extract first header
        title = file_path.stem
        header_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if header_match:
            title = header_match.group(1).strip()

        # Extract pending items / next objectives generically
        # Prioritize "Pending Work", "Next Steps", "Next Session Objectives", "TODO"
        # Avoid matching "Objectives Achieved"
        pending_items: List[str] = []
        section_match = re.search(
            r"##\s+.*(?:Pending Work|Next Session|Next Steps|Pending|TODO|Outstanding).*\n([\s\S]*?)(?=\n##|\Z)",
            content,
            re.IGNORECASE,
        )
        if not section_match:
            section_match = re.search(
                r"##\s+.*(?:Next|Action Items).*\n([\s\S]*?)(?=\n##|\Z)",
                content,
                re.IGNORECASE,
            )

        if section_match:
            section_text = section_match.group(1)
            # Find bullet points or numbered items
            items = re.findall(r"^(?:[\*\-]\s+|\d+\.\s+)(.+)$", section_text, re.MULTILINE)
            pending_items = [it.strip() for it in items if it.strip()][:6]

        return {
            "file": rel_path,
            "title": title,
            "pending_items": pending_items,
        }
