import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from local_ai_factory import web as web_module
from local_ai_factory.config import load_config
from local_ai_factory.ollama import OllamaError
from local_ai_factory.web import (
    FactoryHTTPServer,
    FactoryWebApp,
    ensure_config_file,
    migrate_default_project,
)


@pytest.fixture
def local_app(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(tmp_path / "project"),
            "data_root": str(tmp_path / "factory-data"),
            "web_search_enabled": False,
        }),
        encoding="utf-8",
    )
    (tmp_path / "project").mkdir()
    app = FactoryWebApp(load_config(config_path), config_path)
    app.ollama_models = lambda: ([], "Ollama is stopped")
    server = FactoryHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield app, opener, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_dashboard_and_status_api(local_app):
    app, opener, base_url = local_app
    app.store.add_event(
        "error", app.config.model, "temporary failure", {"retry_in_seconds": 60}
    )
    with opener.open(base_url + "/") as response:
        assert response.status == 200
        assert "AI と話す" in response.read().decode("utf-8")
        assert response.headers["Content-Security-Policy"]
    request = urllib.request.Request(
        base_url + "/api/status", headers={"Origin": base_url}
    )
    with opener.open(request) as response:
        snapshot = json.loads(response.read())
    assert snapshot["ollama"]["available"] is False
    assert snapshot["knowledge"]["count"] == 0
    assert snapshot["recent_events"][0]["message"] == "temporary failure"


def test_api_rejects_cross_site_origin(local_app):
    _, opener, base_url = local_app
    request = urllib.request.Request(
        base_url + "/api/status", headers={"Origin": "https://attacker.example"}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 403


def test_api_rejects_different_local_port_origin(local_app):
    _, opener, base_url = local_app
    request = urllib.request.Request(
        base_url + "/api/status", headers={"Origin": "http://127.0.0.1:9999"}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 403


def test_search_requires_explicitly_enabled_configuration(local_app):
    _, opener, base_url = local_app
    request = urllib.request.Request(
        base_url + "/api/search",
        data=json.dumps({"query": "test"}).encode(),
        headers={"Content-Type": "application/json", "Origin": base_url},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 403


def test_chat_streams_local_ollama_tokens(tmp_path: Path):
    class FakeOllamaHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = (
                b'{"message":{"content":"Local "},"done":false}\n'
                b'{"message":{"content":"answer."},"done":true}\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    ollama_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    ollama_thread = threading.Thread(target=ollama_server.serve_forever, daemon=True)
    ollama_thread.start()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "project_root": str(tmp_path / "project"),
            "data_root": str(tmp_path / "factory-data"),
            "ollama_url": f"http://127.0.0.1:{ollama_server.server_port}",
        }),
        encoding="utf-8",
    )
    (tmp_path / "project").mkdir()
    app = FactoryWebApp(load_config(config_path), config_path)
    server = FactoryHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/chat",
            data=json.dumps({
                "message": "こんにちは", "history": [], "web_search": False,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{server.server_port}",
            },
            method="POST",
        )
        with opener.open(request, timeout=5) as response:
            events = response.read().decode("utf-8")
        assert '"token": "Local "' in events
        assert '"token": "answer."' in events
        assert '"done": true' in events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        ollama_server.shutdown()
        ollama_server.server_close()
        ollama_thread.join(timeout=5)


def test_startup_reuses_an_already_running_ollama(local_app):
    app, _, _ = local_app
    app.factory.ollama.models = lambda **kwargs: []
    result = app.start_ollama_if_needed()
    assert result == {"started": False, "reason": "Ollamaは既に稼働しています"}
    assert app.ollama_process is None


def test_startup_launches_installed_ollama_when_stopped(local_app, monkeypatch):
    app, _, _ = local_app
    app.factory.ollama.models = Mock(side_effect=OllamaError("connection refused"))
    process = Mock()
    process.pid = 4321
    popen = Mock(return_value=process)
    monkeypatch.setattr(web_module.shutil, "which", lambda _: "/usr/local/bin/ollama")
    monkeypatch.setattr(web_module.subprocess, "Popen", popen)

    result = app.start_ollama_if_needed()

    assert result == {"started": True, "pid": 4321, "address": "127.0.0.1:11434"}
    assert app.ollama_process is process
    assert app.factory.ollama.models.call_args.kwargs["timeout_seconds"] == 2
    assert popen.call_args.args[0] == ["/usr/local/bin/ollama", "serve"]
    options = popen.call_args.kwargs
    assert options["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert options["start_new_session"] is True


def test_missing_config_is_bootstrapped_without_overwriting_existing_config(tmp_path: Path):
    example = tmp_path / "config.example.json"
    config = tmp_path / "config.json"
    example.write_text('{"model":"qwen2.5-coder:3b"}\n', encoding="utf-8")
    assert ensure_config_file(config) is True
    assert config.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
    config.write_text('{"model":"user-choice"}\n', encoding="utf-8")
    assert ensure_config_file(config) is False
    assert '"user-choice"' in config.read_text(encoding="utf-8")


def test_default_project_config_is_migrated_without_overwriting_custom_settings(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({
            "project_root": "../your-project",
            "data_root": "./factory-data",
            "model": "custom-model",
            "web_port": 9000,
        }),
        encoding="utf-8",
    )

    assert migrate_default_project(config) is True
    values = json.loads(config.read_text(encoding="utf-8"))
    assert values["project_root"] == "."
    assert values["data_root"] == "../factory-data"
    assert values["model"] == "custom-model"
    assert values["web_port"] == 9000
    assert migrate_default_project(config) is False


def test_factory_worker_does_not_start_without_a_real_project(local_app):
    app, _, _ = local_app
    app.factory.project_root.rmdir()
    assert app.snapshot()["project"]["configured"] is False
    with pytest.raises(RuntimeError, match="project_root"):
        app.start_worker()
    assert app.worker is None
