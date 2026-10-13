"""
Universal technology stack detector.
Inspects project manifests to determine languages, package managers, and frameworks.
"""
from pathlib import Path
from typing import Any, Dict, List
import json
from .base import BaseDetector


class StackDetector(BaseDetector):
    """Detects languages, frameworks, and tooling in a project-agnostic manner."""

    def detect(self, project_root: Path) -> Dict[str, Any]:
        languages: List[str] = []
        frameworks: List[str] = []
        package_managers: List[str] = []
        manifests_found: List[str] = []

        # 1. PHP / Composer
        composer_file = project_root / "composer.json"
        if composer_file.exists():
            languages.append("php")
            package_managers.append("composer")
            manifests_found.append("composer.json")
            try:
                data = json.loads(composer_file.read_text(encoding="utf-8"))
                deps = {**data.get("require", {}), **data.get("require-dev", {})}
                if "laravel/framework" in deps:
                    frameworks.append("laravel")
                if "symfony/framework-bundle" in deps or "symfony/symfony" in deps:
                    frameworks.append("symfony")
            except Exception:
                pass

        # 2. Node / JavaScript / TypeScript
        pkg_file = project_root / "package.json"
        if pkg_file.exists():
            languages.append("javascript")
            manifests_found.append("package.json")
            if (project_root / "tsconfig.json").exists():
                languages.append("typescript")
            if (project_root / "pnpm-lock.yaml").exists():
                package_managers.append("pnpm")
            elif (project_root / "yarn.lock").exists():
                package_managers.append("yarn")
            elif (project_root / "bun.lockb").exists():
                package_managers.append("bun")
            else:
                package_managers.append("npm")

            try:
                data = json.loads(pkg_file.read_text(encoding="utf-8"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                for name, fw in [
                    ("next", "nextjs"),
                    ("react", "react"),
                    ("vue", "vue"),
                    ("svelte", "svelte"),
                    ("express", "express"),
                    ("fastify", "fastify"),
                    ("nest", "nestjs"),
                ]:
                    if any(name in dep for dep in deps):
                        frameworks.append(fw)
            except Exception:
                pass

        # 3. Python
        py_manifests = ["pyproject.toml", "requirements.txt", "Pipfile", "setup.py"]
        found_py_manifests = [m for m in py_manifests if (project_root / m).exists()]
        if found_py_manifests or list(project_root.glob("*.py")):
            languages.append("python")
            manifests_found.extend(found_py_manifests)
            if (project_root / "poetry.lock").exists():
                package_managers.append("poetry")
            elif (project_root / "Pipfile.lock").exists():
                package_managers.append("pipenv")
            else:
                package_managers.append("pip")

            # Check framework signatures
            reqs_text = ""
            for mf in found_py_manifests:
                try:
                    reqs_text += (project_root / mf).read_text(encoding="utf-8").lower() + "\n"
                except Exception:
                    pass

            for sig, fw in [
                ("fastapi", "fastapi"),
                ("django", "django"),
                ("flask", "flask"),
                ("langgraph", "langgraph"),
                ("langchain", "langchain"),
            ]:
                if sig in reqs_text:
                    frameworks.append(fw)

        # 4. Rust
        cargo_file = project_root / "Cargo.toml"
        if cargo_file.exists():
            languages.append("rust")
            package_managers.append("cargo")
            manifests_found.append("Cargo.toml")

        # 5. Go
        go_mod = project_root / "go.mod"
        if go_mod.exists():
            languages.append("go")
            package_managers.append("go")
            manifests_found.append("go.mod")

        # 6. Container / Orchestration detection
        containers = []
        if (project_root / "Dockerfile").exists():
            containers.append("docker")
        if (project_root / "docker-compose.yml").exists() or (project_root / "docker-compose.yaml").exists():
            containers.append("docker-compose")

        return {
            "primary_language": languages[0] if languages else "unknown",
            "languages": list(dict.fromkeys(languages)),
            "frameworks": list(dict.fromkeys(frameworks)),
            "package_managers": list(dict.fromkeys(package_managers)),
            "manifests": manifests_found,
            "containers": containers,
        }
