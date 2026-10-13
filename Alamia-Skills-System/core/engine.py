"""
Core Project State Engine for Alamia-Skills-System.
Coordinates project discovery, git inspection, case classification, and state reporting.
"""
from pathlib import Path
from typing import Any, Dict, List
import subprocess
import json
import sys

try:
    from .adapters import (
        StackDetector,
        TestDetector,
        MemoryDetector,
        AgentDetector,
    )
except ImportError:
    # Support direct execution via `python path/to/engine.py`
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from adapters import (
        StackDetector,
        TestDetector,
        MemoryDetector,
        AgentDetector,
    )

CORE_LIFECYCLE_GROUPS = [
    "bootstrap",
    "implement",
    "qa",
    "architecture-review",
    "session-end",
]


class ProjectStateEngine:
    """State engine that inspects any repository and computes its lifecycle profile."""

    def __init__(self, project_root: Path = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.stack_detector = StackDetector()
        self.test_detector = TestDetector()
        self.memory_detector = MemoryDetector()
        self.agent_detector = AgentDetector()

    def inspect(self) -> Dict[str, Any]:
        """Run full discovery and produce the project state."""
        stack_info = self.stack_detector.detect(self.project_root)
        test_info = self.test_detector.detect(self.project_root)
        memory_info = self.memory_detector.detect(self.project_root)
        agent_info = self.agent_detector.detect(self.project_root)
        git_info = self._get_git_info()

        # Classify Project Case (A, B, C, D, or E)
        classification = self._classify_state(stack_info, agent_info)

        # Check existing Alamia configuration
        alamia_config_path = self.project_root / ".alamia" / "project.json"
        has_alamia_config = alamia_config_path.exists()
        configured_profile = None
        if has_alamia_config:
            try:
                configured_profile = json.loads(alamia_config_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "project_name": self.project_root.name,
            "project_root": str(self.project_root).replace("\\", "/"),
            "case_classification": classification,
            "git": git_info,
            "stack": stack_info,
            "testing": test_info,
            "memory": memory_info,
            "agent_infrastructure": agent_info,
            "alamia_configured": has_alamia_config,
            "configured_profile": configured_profile,
            "missing_lifecycle_skills": [
                s for s in CORE_LIFECYCLE_GROUPS if s not in agent_info["installed_skills"]
            ],
        }

    def _get_git_info(self) -> Dict[str, Any]:
        """Inspect Git repository state safely."""
        git_dir = self.project_root / ".git"
        if not git_dir.exists():
            return {"is_git": False, "branch": None, "commit": None, "dirty": False}

        try:
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"],
                cwd=self.project_root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            commit = subprocess.check_output(
                ["git", "log", "-1", "--oneline"],
                cwd=self.project_root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            return {
                "is_git": True,
                "branch": branch or "HEAD detached",
                "latest_commit": commit,
                "is_dirty": bool(status),
                "uncommitted_lines": len(status.splitlines()) if status else 0,
            }
        except Exception:
            return {"is_git": True, "branch": "unknown", "latest_commit": "unknown", "is_dirty": False}

    def _classify_state(self, stack_info: Dict[str, Any], agent_info: Dict[str, Any]) -> Dict[str, str]:
        """
        Classifies project into Cases A through E:
        - Case A: Fresh project
        - Case B: Existing project with no skill infrastructure
        - Case C: Partially configured project
        - Case D: Configured & healthy project
        - Case E: Existing but outdated infrastructure
        """
        installed = set(agent_info["installed_skills"])
        required = set(CORE_LIFECYCLE_GROUPS)

        is_fresh = not stack_info["manifests"] and not stack_info["languages"]

        if is_fresh and not installed:
            return {
                "case": "Case A",
                "description": "Fresh project with no existing code or agent infrastructure.",
            }

        if not installed:
            return {
                "case": "Case B",
                "description": "Existing project with established code but no skill infrastructure.",
            }

        missing = required - installed
        if missing and installed:
            return {
                "case": "Case C",
                "description": f"Partially configured project (missing skills: {', '.join(sorted(missing))}).",
            }

        # If all core skills are present, check if config matches
        alamia_config = self.project_root / ".alamia" / "project.json"
        if not alamia_config.exists():
            return {
                "case": "Case C",
                "description": "Lifecycle skills present, but project profile (.alamia/project.json) is not yet reconciled.",
            }

        return {
            "case": "Case D",
            "description": "Configured project with active skill infrastructure and reconciled profile.",
        }


if __name__ == "__main__":
    engine = ProjectStateEngine()
    state = engine.inspect()
    print(json.dumps(state, indent=2))
