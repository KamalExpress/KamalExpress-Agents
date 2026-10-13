"""
Idempotent Reconciler for Alamia-Skills-System.
Takes the discovered state, creates/updates .alamia/project.json,
and reconciles lifecycle skills in .agents/skills without destructive overwrites.
"""
from pathlib import Path
from typing import Any, Dict
import json
import shutil
import sys

try:
    from .engine import ProjectStateEngine, CORE_LIFECYCLE_GROUPS
except ImportError:
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from engine import ProjectStateEngine, CORE_LIFECYCLE_GROUPS


class ProjectReconciler:
    """Reconciles the project's Alamia configuration and lifecycle skills."""

    def __init__(self, project_root: Path = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.engine = ProjectStateEngine(self.project_root)
        self.system_root = Path(__file__).resolve().parent.parent

    def reconcile(self) -> Dict[str, Any]:
        """Perform idempotent reconciliation."""
        state = self.engine.inspect()
        actions_taken = []

        # 1. Ensure .alamia/ directory and write .alamia/project.json
        alamia_dir = self.project_root / ".alamia"
        alamia_dir.mkdir(exist_ok=True)
        config_path = alamia_dir / "project.json"

        profile = {
            "version": "0.1.0",
            "project_name": state["project_name"],
            "stack": state["stack"],
            "testing": state["testing"],
            "memory": {
                "has_memory_system": state["memory"]["has_memory_system"],
                "handoff_dir": state["memory"]["handoff_dir"],
                "architecture_dir": state["memory"]["architecture_dir"],
                "worklogs": state["memory"]["worklogs"],
                "latest_handoff_file": (
                    state["memory"]["latest_handoff"]["file"]
                    if state["memory"]["latest_handoff"]
                    else None
                ),
            },
            "lifecycle": {
                "groups": CORE_LIFECYCLE_GROUPS,
            },
        }

        # Check if profile changed or is new
        existing_profile_str = config_path.read_text(encoding="utf-8") if config_path.exists() else None
        new_profile_str = json.dumps(profile, indent=2)

        if existing_profile_str != new_profile_str:
            config_path.write_text(new_profile_str, encoding="utf-8")
            actions_taken.append("Updated .alamia/project.json with detected project profile.")
        else:
            actions_taken.append(".alamia/project.json is already up to date.")

        # 2. Reconcile Lifecycle Skills in .agents/skills/
        agents_skills_dir = self.project_root / ".agents" / "skills"
        agents_skills_dir.mkdir(parents=True, exist_ok=True)

        lifecycle_source_dir = self.system_root / "lifecycle"
        for group in CORE_LIFECYCLE_GROUPS:
            target_group_dir = agents_skills_dir / group
            target_skill_file = target_group_dir / "SKILL.md"
            source_skill_file = lifecycle_source_dir / group / "SKILL.md"

            if not target_skill_file.exists():
                target_group_dir.mkdir(parents=True, exist_ok=True)
                if source_skill_file.exists():
                    shutil.copy2(source_skill_file, target_skill_file)
                    actions_taken.append(f"Scaffolded lifecycle skill: {group}")
                else:
                    # Fallback default if not yet copied to lifecycle dir
                    target_skill_file.write_text(
                        f"---\nname: {group}\ndescription: Universal {group} lifecycle capability.\n---\n\n# {group.title()} Lifecycle\n",
                        encoding="utf-8"
                    )
                    actions_taken.append(f"Created fallback lifecycle skill: {group}")
            else:
                actions_taken.append(f"Preserved existing lifecycle skill: {group}")

        # 3. Reconcile .agents/skills.json to expose atomic skills
        vendor_repo_dir = self.system_root / "vendor" / "mattpocock-skills"
        vendor_skills_dir = vendor_repo_dir / "skills"

        # If vendor skills are missing from Alamia-Skills-System, auto-pull them on demand
        if not vendor_skills_dir.exists():
            try:
                vendor_repo_dir.parent.mkdir(parents=True, exist_ok=True)
                import subprocess
                subprocess.run(
                    ["git", "clone", "--depth", "1", "https://github.com/mattpocock/skills.git", str(vendor_repo_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                actions_taken.append("Auto-pulled Matt Pocock atomic skills from GitHub into Alamia-Skills-System/vendor/.")
            except Exception as e:
                actions_taken.append(f"Notice: Could not auto-pull atomic skills ({e}). Skipping vendor registration.")

        skills_config_path = self.project_root / ".agents" / "skills.json"
        rel_vendor_path = None
        if vendor_skills_dir.exists():
            try:
                rel_vendor_path = str(vendor_skills_dir.relative_to(self.project_root)).replace("\\", "/")
            except ValueError:
                # If Alamia-Skills-System is outside project root or on a different drive
                rel_vendor_path = str(vendor_skills_dir).replace("\\", "/")

        if rel_vendor_path:
            skills_cfg = {
                "entries": [
                    {"path": f"{rel_vendor_path}/engineering"},
                    {"path": f"{rel_vendor_path}/productivity"},
                ]
            }
            new_cfg_str = json.dumps(skills_cfg, indent=2)
            existing_cfg_str = skills_config_path.read_text(encoding="utf-8") if skills_config_path.exists() else None

            if existing_cfg_str != new_cfg_str:
                skills_config_path.write_text(new_cfg_str, encoding="utf-8")
                actions_taken.append("Configured .agents/skills.json to expose Matt Pocock atomic skills.")
            else:
                actions_taken.append(".agents/skills.json is already up to date.")

        # Re-inspect to get final state
        final_state = self.engine.inspect()

        return {
            "initial_case": state["case_classification"],
            "final_case": final_state["case_classification"],
            "actions_taken": actions_taken,
            "profile": profile,
            "git": final_state["git"],
            "memory": final_state["memory"],
            "testing": final_state["testing"],
        }

    def format_report(self, result: Dict[str, Any]) -> str:
        """Format a concise, beautiful markdown report for the developer."""
        prof = result["profile"]
        git = result["git"]
        mem = result["memory"]
        test = result["testing"]
        case = result["final_case"]

        lines = [
            f"# Alamia-Skills-System: Bootstrap & Reconciliation Report",
            f"",
            f"**Project:** `{prof['project_name']}` | **Status:** `{case['case']}` ({case['description']})",
            f"",
            f"---",
            f"",
            f"## 1. Discovered Stack & Environment",
            f"- **Primary Language:** `{prof['stack']['primary_language']}` (All: `{', '.join(prof['stack']['languages']) or 'None'}`)",
            f"- **Frameworks:** `{', '.join(prof['stack']['frameworks']) or 'None detected'}`",
            f"- **Package Manager:** `{', '.join(prof['stack']['package_managers']) or 'None detected'}`",
            f"- **Containers:** `{', '.join(prof['stack']['containers']) or 'None'}`",
            f"",
            f"## 2. Testing Configuration",
            f"- **Test Runner:** `{test['runner']}`",
            f"- **Test Command:** `{test['command'] or 'None'}`",
            f"- **Test Dirs:** `{', '.join(test['test_dirs']) or 'None'}`",
            f"",
            f"## 3. Project Memory & Continuity",
        ]

        if mem["has_memory_system"]:
            lines.append(f"- **Handoff Directory:** `{mem['handoff_dir'] or 'None'}`")
            lines.append(f"- **Architecture Directory:** `{mem['architecture_dir'] or 'None'}`")
            if mem.get("latest_handoff"):
                lh = mem["latest_handoff"]
                lines.append(f"- **Latest Handoff:** [{lh['file']}](file:///{self.project_root}/{lh['file']})")
                lines.append(f"  *Title:* **{lh['title']}**")
                if lh.get("pending_items"):
                    lines.append(f"  *Active Pending Objectives:*")
                    for it in lh["pending_items"]:
                        lines.append(f"    - {it}")
        else:
            lines.append("- *No existing session memory detected. Standard lightweight defaults ready.*")

        lines.extend([
            f"",
            f"## 4. Git & Working Tree",
            f"- **Branch:** `{git['branch']}`",
            f"- **Commit:** `{git['latest_commit']}`",
            f"- **Dirty Working Tree:** `{'Yes (' + str(git['uncommitted_lines']) + ' files modified/untracked)' if git['is_dirty'] else 'Clean'}`",
            f"",
            f"## 5. Lifecycle Capabilities Reconciled",
            f"- Available lifecycle commands: `{', '.join(prof['lifecycle']['groups'])}`",
            f"",
            f"## 6. Actions Taken",
        ])
        for act in result["actions_taken"]:
            lines.append(f"- {act}")

        lines.extend([
            f"",
            f"---",
            f"**Ready.** You can now say `implement`, `qa`, `architecture-review`, or `session-end`.",
        ])

        return "\n".join(lines)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    reconciler = ProjectReconciler()
    res = reconciler.reconcile()
    print(reconciler.format_report(res))
