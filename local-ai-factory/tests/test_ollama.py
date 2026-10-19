import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from local_ai_factory.config import load_config
from local_ai_factory.ollama import OllamaClient, OllamaError


def test_ollama_unavailable_has_actionable_error(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "ollama_url": "http://127.0.0.1:1",
            "request_timeout_seconds": 1,
            "project_root": ".",
            "data_root": "data",
        }),
        encoding="utf-8",
    )
    client = OllamaClient(load_config(config_file))
    with pytest.raises(OllamaError, match="Ollamaを起動"):
        client.models()


def test_missing_model_404_recommends_installing_or_selecting_a_model():
    from local_ai_factory.ollama import describe_http_error

    error = HTTPError(
        "http://127.0.0.1:11434/api/generate",
        404,
        "Not Found",
        {},
        io.BytesIO(b"""{"error":"model 'missing-model' not found, try pulling it first"}"""),
    )
    message = describe_http_error(error, "missing-model")
    assert "ollama pull missing-model" in message
    assert "config.json" in message


def test_generation_requests_json_and_can_limit_task_tokens(tmp_path: Path):
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received.update(json.loads(self.rfile.read(length)))
            body = json.dumps({"response": '{"title":"task"}'}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "ollama_url": f"http://127.0.0.1:{server.server_port}",
            "project_root": ".",
            "data_root": "data",
        }),
        encoding="utf-8",
    )
    try:
        client = OllamaClient(load_config(config_path))
        schema = {"type": "object", "required": ["title"]}
        assert client.generate(
            "suggest a task", max_tokens=128, response_format=schema
        ) == '{"title":"task"}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert received["format"] == schema
    assert received["options"]["num_predict"] == 128
