import json
from pathlib import Path

import pytest

from local_ai_factory.config import load_config
from local_ai_factory.factory import Factory
from local_ai_factory.store import Store


def test_config_rejects_non_local_ollama(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ollama_url": "https://example.com"}), encoding="utf-8")
    with pytest.raises(ValueError, match="ローカルHTTP"):
        load_config(path)


def test_config_rejects_localhost_prefix_bypass(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"ollama_url": "http://localhost.attacker.example:11434"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ローカルHTTP"):
        load_config(path)


def test_config_prevents_zero_interval_spin(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"idle_seconds": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="idle_seconds"):
        load_config(path)


def test_store_deduplicates_artifact_hash(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert store.add_artifact("one", "same", "rejected", tmp_path / "one", {})
    assert not store.add_artifact("two", "same", "approved", tmp_path / "two", {})


def test_task_attempts_and_report(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.upsert_task("t1", "title", "description")
    assert store.pending_task(2)["id"] == "t1"
    store.task_update("t1", "running")
    assert store.list_tasks()[0]["attempts"] == 1
    assert store.report()["total"] == 0


def test_dry_run_does_not_create_data_directory(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    data_root = tmp_path / "factory-data"
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "project_root": str(project),
            "data_root": str(data_root),
            "require_macos_sandbox": False,
        }),
        encoding="utf-8",
    )
    factory = Factory(load_config(config_file), dry_run=True)
    assert factory.once()["status"] == "dry_run"
    assert not data_root.exists()
