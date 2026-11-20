"""
Universal Documentation Indexing & Synchronization Engine.
Project-agnostic engine that scans markdown specifications across a repository,
extracts structural metadata, and emits `docs/docs_data.js` for instant
in-browser runtime rendering without requiring a running web server.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional


class DocEngine:
    """Universal documentation scanner and in-browser indexer."""

    def __init__(self, project_root: Optional[Path] = None):
        self.root = Path(project_root or Path.cwd()).resolve()
        self.docs_dir = self.root / "docs"
        self.data_file = self.docs_dir / "docs_data.js"

    def scan_documents(self) -> List[Dict[str, Any]]:
        """
        Scans all Markdown files in `docs/` and primary repository specifications.
        Excludes build directories, node_modules, .git, and venv.
        """
        exclude_dirs = {
            ".git", "node_modules", ".venv", "venv", "__pycache__",
            ".pytest_cache", ".gemini", "dist", "build", "vendor"
        }

        discovered_docs: List[Dict[str, Any]] = []
        seen_paths = set()
        used_ids: set[str] = set()

        # 1. First priority: Everything in docs/
        if self.docs_dir.exists():
            for root, dirs, files in os.walk(self.docs_dir):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                for f in sorted(files):
                    if f.endswith(".md"):
                        p = Path(root) / f
                        if p in seen_paths:
                            continue
                        seen_paths.add(p)
                        doc_info = self._process_doc_file(p, is_inside_docs=True, used_ids=used_ids)
                        if doc_info:
                            discovered_docs.append(doc_info)

        # 2. Second priority: Root markdown specifications and specs
        candidate_paths = [
            self.root / "README.md",
            self.root / "RUNNING.md",
            self.root / "CHANGELOG.md",
            self.root / "ARCHITECTURE.md",
        ]
        # Also check module specifications (e.g. standalone_workers/*.md)
        for cand in self.root.glob("*/*.md"):
            if not any(ex in cand.parts for ex in exclude_dirs):
                candidate_paths.append(cand)

        for p in candidate_paths:
            if p.exists() and p not in seen_paths:
                seen_paths.add(p)
                doc_info = self._process_doc_file(p, is_inside_docs=False, used_ids=used_ids)
                if doc_info:
                    discovered_docs.append(doc_info)

        # Sort logically: Overview/README first, then Architecture, Standards, Handoffs, etc.
        category_weights = {
            "Overview": 1,
            "Permanent": 2,
            "Architecture": 3,
            "Standards": 4,
            "Adr": 5,
            "Glossary": 6,
            "Workers": 7,
            "Transient": 8,
            "Sprint": 9,
            "Handoffs": 10,
            "Backlog": 11,
            "Lessons": 12,
            "History": 13,
            "Indexes": 14,
            "Operations": 15,
            "Skills System": 16,
            "General": 20,
        }

        discovered_docs.sort(
            key=lambda d: (
                category_weights.get(d["category"], 50),
                d["title"].lower()
            )
        )

        return discovered_docs

    def _process_doc_file(self, full_path: Path, is_inside_docs: bool, used_ids: set[str]) -> Optional[Dict[str, Any]]:
        """Extracts structural metadata and markdown content from a file."""
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        rel_path = str(full_path.relative_to(self.root)).replace("\\", "/")
        lines = content.splitlines()

        # Extract title from first # header or filename
        title = full_path.stem.replace("-", " ").replace("_", " ").title()
        description = ""
        title_found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and not title_found:
                title = stripped[2:].strip()
                title_found = True
            elif stripped.startswith("> ") and not description:
                description = stripped[2:].strip()

        # Determine logical category
        category = "General"
        if is_inside_docs:
            rel_to_docs = full_path.relative_to(self.docs_dir).parts
            if len(rel_to_docs) == 1:
                if full_path.stem.lower() in ["readme", "index"]:
                    category = "Overview"
                else:
                    category = "General"
            elif len(rel_to_docs) > 1:
                cat_first = rel_to_docs[0].capitalize()
                if cat_first.lower() in ["permanent", "transient"] and len(rel_to_docs) > 2:
                    category = rel_to_docs[1].capitalize()
                else:
                    category = cat_first
        else:
            if full_path == self.root / "README.md":
                category = "Overview"
            elif "alamia" in rel_path.lower():
                category = "Skills System"
            elif "worker" in rel_path.lower():
                category = "Workers"
            elif "run" in full_path.stem.lower():
                category = "Operations"
            elif full_path.name.lower() == "readme.md":
                category = full_path.parent.name.replace("-", " ").replace("_", " ").title()
            else:
                parent_name = full_path.parent.name.replace("-", " ").replace("_", " ").title()
                category = parent_name if parent_name not in ["", ".", self.root.name] else "General"

        # Generate unique document ID to prevent collision across same-named files
        parent_slug = full_path.parent.name.lower().replace(" ", "-").replace("_", "-")
        if full_path == self.docs_dir / "README.md" or full_path == self.docs_dir / "index.md":
            base_id = "master-index"
        elif full_path == self.root / "README.md":
            base_id = "project-overview"
        elif full_path.stem.lower() in ["readme", "index"]:
            # Subdirectory readmes/indexes are prefixed with their folder name
            base_id = f"{parent_slug}-{full_path.stem.lower()}" if parent_slug else full_path.stem.lower()
        else:
            base_id = full_path.stem.lower().replace(" ", "-").replace("_", "-")

        doc_id = base_id
        if doc_id in used_ids:
            if parent_slug and parent_slug != doc_id and not doc_id.startswith(parent_slug):
                doc_id = f"{parent_slug}-{base_id}"
            else:
                rel_parts = [p.lower().replace(" ", "-").replace("_", "-") for p in full_path.relative_to(self.root).with_suffix("").parts]
                doc_id = "-".join(rel_parts)

        # Final uniqueness guarantee
        candidate = doc_id
        counter = 2
        while candidate in used_ids:
            candidate = f"{doc_id}-{counter}"
            counter += 1
        doc_id = candidate
        used_ids.add(doc_id)
        return {
            "id": doc_id,
            "title": title,
            "category": category,
            "path": rel_path,
            "description": description or f"Technical reference: {title}",
            "markdown": content,
            "line_count": len(lines),
        }

    def sync(self) -> Dict[str, Any]:
        """
        Scans all documents and writes `docs/docs_data.js`.
        """
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        docs = self.scan_documents()

        js_content = (
            "// Auto-generated by Alamia DocEngine - Do not edit directly\n"
            "window.DOCS_DATA = " + json.dumps(docs, indent=2, ensure_ascii=False) + ";\n"
        )

        self.data_file.write_text(js_content, encoding="utf-8")
        return {
            "success": True,
            "total_documents": len(docs),
            "output_file": str(self.data_file.relative_to(self.root)).replace("\\", "/"),
            "documents": [
                {"id": d["id"], "title": d["title"], "category": d["category"], "path": d["path"]}
                for d in docs
            ]
        }


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    engine = DocEngine(target)
    res = engine.sync()
    print(f"[SUCCESS] Synchronized {res['total_documents']} documents into {res['output_file']}")
