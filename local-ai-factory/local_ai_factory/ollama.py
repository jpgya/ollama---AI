from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import FactoryConfig


TASK_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["title", "description"],
    "additionalProperties": False,
}

FILE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["files"],
    "additionalProperties": False,
}


class OllamaError(RuntimeError):
    pass


def describe_http_error(error: urllib.error.HTTPError, model: str) -> str:
    try:
        body = error.read(2048).decode("utf-8", errors="replace").strip()
    finally:
        error.close()
    lowered = body.lower()
    if error.code == 404 and "model" in lowered and (
        "not found" in lowered or "pull" in lowered
    ):
        return (
            f"Ollamaにモデル「{model}」がありません。"
            f"ターミナルで `ollama pull {model}` を実行するか、"
            f"config.json の model をインストール済みモデルに変更してください。"
            + (f" 詳細: {body}" if body else "")
        )
    detail = f"HTTP {error.code}"
    if body:
        detail += f": {body}"
    return (
        f"Ollama APIがエラーを返しました ({detail})。"
        "設定の ollama_url とOllamaの稼働状態を確認してください。"
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaError(f"Ollamaがリダイレクトを返しました。安全のため拒否します: {newurl}")


class OllamaClient:
    def __init__(self, config: FactoryConfig):
        self.config = config
        self.base_url = config.ollama_url.rstrip("/")

    def _request(
        self, endpoint: str, payload: dict[str, Any] | None = None,
        *, timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirectHandler()
            )
            with opener.open(
                request, timeout=timeout_seconds or self.config.request_timeout_seconds
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OllamaError(describe_http_error(exc, self.config.model)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(
                f"Ollamaに接続できません ({self.base_url})。Ollamaを起動し、"
                f"モデル「{self.config.model}」を確認してください。詳細: {exc}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollamaから不正な応答を受信しました: {exc}") from exc
        if not isinstance(result, dict):
            raise OllamaError("Ollamaの応答形式が不正です。")
        if "error" in result:
            raise OllamaError(f"Ollamaエラー: {result['error']}")
        return result

    def models(self, *, timeout_seconds: int | None = None) -> list[dict[str, Any]]:
        result = self._request("/api/tags", timeout_seconds=timeout_seconds)
        models = result.get("models")
        if not isinstance(models, list):
            raise OllamaError("Ollamaのモデル一覧が不正です。")
        return [model for model in models if isinstance(model, dict)]

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
        response_format: str | dict[str, Any] = "json",
    ) -> str:
        result = self._request(
            "/api/generate",
            {
                "model": self.config.model,
                "prompt": prompt,
                "system": system,
                "format": response_format,
                "stream": False,
                "keep_alive": "5m",
                "options": {
                    "num_ctx": self.config.context_length,
                    "num_predict": max_tokens or self.config.max_tokens,
                    "temperature": 0.2,
                },
            },
            timeout_seconds=timeout_seconds,
        )
        response = result.get("response")
        if not isinstance(response, str) or not response.strip():
            raise OllamaError("Ollamaから空の生成結果が返りました。")
        return response
