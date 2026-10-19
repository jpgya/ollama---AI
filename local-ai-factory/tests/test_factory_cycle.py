import json
from pathlib import Path

import pytest

from local_ai_factory import factory as factory_module
from local_ai_factory.config import load_config
from local_ai_factory.factory import Factory


class FakeOllama:
    def __init__(self, generated: dict[str, str]):
        self.generated = json.dumps({"files": generated})
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
        response_format: str | dict | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.system_prompts.append(system)
        return self.generated


@pytest.mark.parametrize(
    ("test_contents", "expected_status", "expected_folder"),
    [
        ("def test_add():\n    assert add(1, 2) == 3\n", "approved", "approved"),
        ("def test_add():\n    assert add(1, 2) == 4\n", "rejected", "rejected"),
    ],
)
def test_cycle_saves_candidate_only_after_quality_result(
    tmp_path: Path, test_contents: str, expected_status: str, expected_folder: str
):
    project = tmp_path / "source"
    project.mkdir()
    (project / "src.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    data_root = tmp_path / "factory-data"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(data_root),
            "task_file": str(tmp_path / "no-tasks.json"),
            "require_macos_sandbox": False,
            "retry_limit": 0,
            "experiment_timeout_seconds": 30,
        }),
        encoding="utf-8",
    )
    factory = Factory(load_config(config_path))
    fake_ollama = FakeOllama({
        "src.py": "def add(left, right):\n    return left + right\n",
        "test_add.py": f"from src import add\n\n{test_contents}",
    })
    factory.ollama = fake_ollama
    factory.knowledge.add({
        "title": "Reviewed Python practice",
        "snippet": "Use focused tests for arithmetic helpers.",
        "url": "https://example.org/python",
    })

    result = factory.once("Add a tested addition function")

    assert "Reviewed Python practice" in fake_ollama.prompts[0]
    assert result["status"] == expected_status
    artifact = Path(result["artifact_path"])
    assert artifact.parent.name == expected_folder
    metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["evaluation"]["approved"] is (expected_status == "approved")
    assert (project / "src.py").read_text(encoding="utf-8") == (
        "def add(left, right):\n    return left + right\n"
    )


def test_loop_recovers_automatically_after_repeated_errors(tmp_path: Path, monkeypatch):
    project = tmp_path / "source"
    project.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(tmp_path / "factory-data"),
            "max_consecutive_errors": 2,
            "auto_recovery_delay_seconds": 60,
            "max_auto_recovery_delay_seconds": 120,
        }),
        encoding="utf-8",
    )
    factory = Factory(load_config(config_path))
    states: list[dict] = []
    sleep_calls: list[int] = []
    original_set_state = factory._set_state

    def record_state(status: str, **extra):
        original_set_state(status, **extra)
        states.append(json.loads(factory.state_path.read_text(encoding="utf-8")))

    factory._set_state = record_state
    factory._acquire_lock = lambda: None
    factory._release_lock = lambda: None
    factory.status = lambda: json.loads(factory.state_path.read_text(encoding="utf-8"))
    attempts = 0

    def fail_twice_then_stop():
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("temporary Ollama failure")
        factory.stop_requested = True
        return {"status": "approved"}

    factory.once = fail_twice_then_stop
    monkeypatch.setattr(factory_module.time, "sleep", sleep_calls.append)

    factory.run_loop()

    recovery = next(state for state in states if state.get("reason") == "自動回復待ち")
    assert recovery["status"] == "idle"
    assert recovery["current_task"] is None
    assert recovery["retry_in_seconds"] == 60
    assert attempts == 3
    assert factory.status()["status"] == "stopped"
    assert sleep_calls.count(1) == 65


def test_task_generation_uses_a_task_specific_json_prompt(tmp_path: Path):
    project = tmp_path / "source"
    project.mkdir()
    (project / "README.md").write_text("# A small project\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(tmp_path / "factory-data"),
        }),
        encoding="utf-8",
    )
    factory = Factory(load_config(config_path))

    class TaskOllama:
        system_prompt = ""

        def generate(
            self,
            prompt: str,
            *,
            system: str = "",
            timeout_seconds: int | None = None,
            max_tokens: int | None = None,
            response_format: str | dict | None = None,
        ) -> str:
            self.system_prompt = system
            return json.dumps({
                "title": "Document project setup",
                "description": "Add a concise setup section to the README and verify its commands.",
            })

    ollama = TaskOllama()
    factory.ollama = ollama
    factory._generate_task()

    task = factory.store.list_tasks()[0]
    assert task["title"] == "Document project setup"
    assert "files" not in ollama.system_prompt
    assert '"title"' in ollama.system_prompt
