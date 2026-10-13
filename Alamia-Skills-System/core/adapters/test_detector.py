"""
Universal test runner detector.
Detects pytest, pest, phpunit, jest, vitest, cargo test, go test, etc.
"""
from pathlib import Path
from typing import Any, Dict, List
import json
from .base import BaseDetector


class TestDetector(BaseDetector):
    """Detects test runners, commands, and test suites."""

    def detect(self, project_root: Path) -> Dict[str, Any]:
        runner = "unknown"
        command = ""
        test_dirs: List[str] = []

        # 1. Look for test directories
        for d in ["tests", "test", "spec", "specs", "__tests__"]:
            if (project_root / d).is_dir():
                test_dirs.append(d)

        # 2. PHP testing (Pest / PHPUnit)
        if (project_root / "composer.json").exists():
            if (project_root / "tests" / "Pest.php").exists() or (project_root / "pest.php").exists():
                runner = "pest"
                command = "./vendor/bin/pest"
            elif (project_root / "phpunit.xml").exists() or (project_root / "phpunit.xml.dist").exists():
                runner = "phpunit"
                command = "./vendor/bin/phpunit"

        # 3. Python testing (pytest, unittest)
        if runner == "unknown":
            py_test_indicators = [
                project_root / "pytest.ini",
                project_root / "conftest.py",
                project_root / "tests" / "conftest.py",
                project_root / "setup.cfg",
                project_root / ".pytest_cache",
            ]
            if any(p.exists() for p in py_test_indicators) or (project_root / "tests").exists():
                # Check if pytest is indicated or standard
                runner = "pytest"
                command = "pytest"

        # 4. Node testing (vitest, jest, playwight, mocha)
        pkg_file = project_root / "package.json"
        if runner == "unknown" and pkg_file.exists():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
                scripts = pkg_data.get("scripts", {})
                if "test" in scripts:
                    command = "npm test"
                    test_script = scripts["test"].lower()
                    if "vitest" in test_script:
                        runner = "vitest"
                    elif "jest" in test_script:
                        runner = "jest"
                    elif "playwright" in test_script:
                        runner = "playwright"
                    else:
                        runner = "npm-test"
            except Exception:
                pass

        # 5. Rust testing
        if runner == "unknown" and (project_root / "Cargo.toml").exists():
            runner = "cargo"
            command = "cargo test"

        # 6. Go testing
        if runner == "unknown" and (project_root / "go.mod").exists():
            runner = "go"
            command = "go test ./..."

        return {
            "runner": runner,
            "command": command,
            "test_dirs": test_dirs,
            "has_tests": len(test_dirs) > 0 or bool(command),
        }
