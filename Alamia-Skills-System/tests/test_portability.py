"""
Portability and Project-Agnostic Verification Tests for Alamia-Skills-System.
Validates detection and reconciliation across Laravel, Node, Fresh, and Python stacks.
"""
import tempfile
import json
from pathlib import Path
import sys

# Add core to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from engine import ProjectStateEngine
from reconciler import ProjectReconciler


def test_laravel_discovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create mock Laravel project
        composer_data = {
            "name": "company/laravel-app",
            "require": {"php": "^8.2", "laravel/framework": "^11.0"},
            "require-dev": {"pestphp/pest": "^3.0"}
        }
        (root / "composer.json").write_text(json.dumps(composer_data), encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "Pest.php").write_text("<?php // pest test", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "handoffs").mkdir()
        (root / "docs" / "handoffs" / "01-sprint-handoff.md").write_text(
            "# Sprint 01 Handoff\n\n## Next Session Objectives\n- [ ] Migrate Auth to Sanctum\n",
            encoding="utf-8"
        )

        engine = ProjectStateEngine(root)
        state = engine.inspect()

        assert state["stack"]["primary_language"] == "php"
        assert "laravel" in state["stack"]["frameworks"]
        assert state["stack"]["package_managers"] == ["composer"]
        assert state["testing"]["runner"] == "pest"
        assert state["testing"]["command"] == "./vendor/bin/pest"
        assert state["memory"]["has_memory_system"] is True
        assert state["memory"]["handoff_dir"] == "docs/handoffs"
        assert "Migrate Auth to Sanctum" in state["memory"]["latest_handoff"]["pending_items"][0]
        print("[PASS] Laravel project discovery verified successfully!")


def test_node_nextjs_discovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create mock Next.js / TypeScript project
        pkg_data = {
            "name": "my-web-app",
            "scripts": {"test": "vitest run"},
            "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
            "devDependencies": {"vitest": "^1.0.0"}
        }
        (root / "package.json").write_text(json.dumps(pkg_data), encoding="utf-8")
        (root / "tsconfig.json").write_text("{}", encoding="utf-8")
        (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")

        engine = ProjectStateEngine(root)
        state = engine.inspect()

        assert "typescript" in state["stack"]["languages"]
        assert "nextjs" in state["stack"]["frameworks"]
        assert "pnpm" in state["stack"]["package_managers"]
        assert state["testing"]["runner"] == "vitest"
        assert state["testing"]["command"] == "npm test"
        print("[PASS] Next.js / TypeScript discovery verified successfully!")


def test_fresh_project_case_a():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        engine = ProjectStateEngine(root)
        state = engine.inspect()

        assert state["case_classification"]["case"] == "Case A"
        print("[PASS] Fresh project (Case A) verified successfully!")


def test_reconciler_idempotency():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

        reconciler = ProjectReconciler(root)
        res1 = reconciler.reconcile()
        assert res1["final_case"]["case"] == "Case D"
        assert (root / ".alamia" / "project.json").exists()

        # Run second time
        res2 = reconciler.reconcile()
        assert res2["final_case"]["case"] == "Case D"
        assert ".alamia/project.json is already up to date." in res2["actions_taken"]
        print("[PASS] Reconciler idempotency verified successfully!")


if __name__ == "__main__":
    test_laravel_discovery()
    test_node_nextjs_discovery()
    test_fresh_project_case_a()
    test_reconciler_idempotency()
    print("\nALL PORTABILITY TESTS PASSED!")
