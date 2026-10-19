import json
import shutil
import sys
from pathlib import Path

import pytest

from local_ai_factory.quality import (
    CandidateError,
    apply_files,
    parse_generation,
    security_scan,
    validate_relative_path,
)
from local_ai_factory.config import load_config
from local_ai_factory.quality import evaluate


def test_parse_generation_accepts_json_fence():
    assert parse_generation('```json\n{"files":{"src/a.py":"print(1)"}}\n```') == {
        "src/a.py": "print(1)"
    }


@pytest.mark.parametrize("path", ["../outside", "/tmp/x", "a/../../b", "a\\\\b"])
def test_rejects_unsafe_paths(path):
    with pytest.raises(CandidateError):
        validate_relative_path(path)


def test_apply_files_prevents_escape(tmp_path: Path):
    with pytest.raises(CandidateError):
        apply_files(tmp_path, {"../outside": "bad"}, 100)


def test_rejects_generated_forbidden_directory():
    with pytest.raises(CandidateError):
        parse_generation(json.dumps({"files": {".git/config": "bad"}}))


def test_security_scan_flags_process_and_dynamic_execution(tmp_path: Path):
    (tmp_path / "candidate.py").write_text("import subprocess\neval('1 + 1')\n", encoding="utf-8")
    findings = security_scan(tmp_path)
    assert len(findings) >= 2


def test_missing_tests_are_incomplete_not_approved(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(tmp_path),
            "data_root": str(tmp_path / "data"),
            "require_macos_sandbox": False,
        }),
        encoding="utf-8",
    )
    result = evaluate(tmp_path, load_config(config_path))
    assert result["tests"]["status"] == "incomplete"
    assert result["approved"] is False


def test_context_omits_secret_like_files_and_values(tmp_path: Path):
    from local_ai_factory.quality import build_context

    (tmp_path / "app.py").write_text("API_KEY = 'super-secret-value-123'\n", encoding="utf-8")
    (tmp_path / "visible.py").write_text("def safe():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=not-for-model\n", encoding="utf-8")
    context = build_context(tmp_path, 10, 10000)
    assert "super-secret-value-123" not in context
    assert "not-for-model" not in context
    assert "def safe" in context


@pytest.mark.parametrize(
    ("test_body", "expected_status", "expected_approved"),
    [
        ("def test_pass():\n    assert 2 + 2 == 4\n", "passed", True),
        ("def test_fail():\n    assert 2 + 2 == 5\n", "failed", False),
    ],
)
def test_quality_gate_requires_successful_test_execution(
    tmp_path: Path, test_body: str, expected_status: str, expected_approved: bool
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_smoke.py").write_text(test_body, encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(tmp_path / "factory-data"),
            "require_macos_sandbox": False,
            "experiment_timeout_seconds": 30,
        }),
        encoding="utf-8",
    )
    result = evaluate(project, load_config(config_path), {"test_smoke.py"})
    assert result["tests"]["status"] == expected_status
    assert result["approved"] is expected_approved


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="macOS sandbox-exec is unavailable",
)
def test_required_macos_sandbox_runs_test_suite(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_smoke.py").write_text(
        "def test_smoke():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(tmp_path / "factory-data"),
            "require_macos_sandbox": True,
            "experiment_timeout_seconds": 30,
        }),
        encoding="utf-8",
    )

    result = evaluate(project, load_config(config_path), {"test_smoke.py"})

    assert result["tests"]["status"] == "passed", result["tests"]["detail"]
    assert result["approved"] is True
