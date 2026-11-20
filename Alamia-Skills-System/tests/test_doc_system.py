"""
Tests for universal documentation scaffolder and indexing engine.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest
from core.doc_scaffolder import DocScaffolder
from core.doc_engine import DocEngine


def test_doc_scaffolder_initializes_structure(tmp_path: Path):
    """Verify DocScaffolder creates canonical directories and starter templates."""
    scaffolder = DocScaffolder(tmp_path)
    res = scaffolder.scaffold()

    assert res["success"] is True
    assert (tmp_path / "docs" / "README.md").is_file()
    assert (tmp_path / ".agents" / "AGENTS.md").is_file()
    assert (tmp_path / "docs" / "permanent" / "architecture" / "01-system-architecture.md").is_file()
    assert (tmp_path / "docs" / "transient" / "sprint" / "00-current-state.md").is_file()
    assert (tmp_path / "docs" / "indexes" / "repository.md").is_file()
    assert (tmp_path / "docs" / "permanent" / "standards" / "01-coding-standards.md").is_file()
    assert (tmp_path / "scripts" / "sync_docs.py").is_file()

    # Verify Master Index contains Bootstrap Read Order
    readme_content = (tmp_path / "docs" / "README.md").read_text(encoding="utf-8")
    assert "The AI Bootstrap Read Order" in readme_content
    assert "permanent/architecture/01-system-architecture.md" in readme_content


def test_doc_scaffolder_is_idempotent(tmp_path: Path):
    """Running scaffold twice must not overwrite modified user content."""
    scaffolder = DocScaffolder(tmp_path)
    scaffolder.scaffold()

    custom_text = "# Custom User Edited System Architecture"
    arch_file = tmp_path / "docs" / "permanent" / "architecture" / "01-system-architecture.md"
    arch_file.write_text(custom_text, encoding="utf-8")

    # Run again without overwrite
    res2 = scaffolder.scaffold(overwrite=False)
    assert res2["success"] is True
    assert arch_file.read_text(encoding="utf-8") == custom_text


def test_doc_engine_scans_and_syncs(tmp_path: Path):
    """Verify DocEngine indexes markdown files and outputs docs_data.js."""
    scaffolder = DocScaffolder(tmp_path)
    scaffolder.scaffold()

    engine = DocEngine(tmp_path)
    sync_res = engine.sync()

    assert sync_res["success"] is True
    assert sync_res["total_documents"] >= 4

    data_file = tmp_path / "docs" / "docs_data.js"
    assert data_file.is_file()

    js_code = data_file.read_text(encoding="utf-8")
    assert "window.DOCS_DATA =" in js_code

    # Extract JSON part
    json_str = js_code.replace("window.DOCS_DATA =", "").strip()
    if json_str.endswith(";"):
        json_str = json_str[:-1].strip()
    
    # Strip any comment lines before parsing
    lines = [l for l in json_str.splitlines() if not l.strip().startswith("//")]
    cleaned_json = "\n".join(lines)

    parsed = json.loads(cleaned_json)
    assert isinstance(parsed, list)
    titles = [d["title"] for d in parsed]
    assert any("Architecture" in t for t in titles)
    assert any("Sprint" in t for t in titles)


def test_doc_engine_disambiguates_duplicate_ids(tmp_path: Path):
    """Multiple README.md or same-stem markdown files must receive unique IDs."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "README.md").write_text("# Master Docs Index\nContent", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Root Project Overview\nContent", encoding="utf-8")
    
    sub_dir = tmp_path / "module-a"
    sub_dir.mkdir(parents=True)
    (sub_dir / "README.md").write_text("# Module A Guide\nContent", encoding="utf-8")

    engine = DocEngine(tmp_path)
    scanned = engine.scan_documents()

    ids = [d["id"] for d in scanned]
    # All IDs must be strictly unique
    assert len(ids) == len(set(ids)), f"Duplicate IDs detected: {ids}"
    assert "master-index" in ids
    assert "project-overview" in ids
    assert any("module-a" in i for i in ids)
