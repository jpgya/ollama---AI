from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULTS: dict[str, Any] = {
    "ollama_url": "http://127.0.0.1:11434",
    "start_ollama_if_stopped": True,
    "web_host": "127.0.0.1",
    "web_port": 8765,
    "web_search_enabled": True,
    "web_search_timeout_seconds": 12,
    "model": "qwen2.5:7b",
    "context_length": 4096,
    "max_tokens": 1200,
    "request_timeout_seconds": 180,
    "project_root": ".",
    "data_root": "../factory-data",
    "task_file": "./tasks.json",
    "max_consecutive_errors": 3,
    "retry_limit": 2,
    "retry_backoff_seconds": 5,
    "auto_recovery_delay_seconds": 60,
    "max_auto_recovery_delay_seconds": 900,
    "idle_seconds": 60,
    "experiment_timeout_seconds": 300,
    "max_memory_percent": 82,
    "max_load_per_cpu": 2.5,
    "min_free_disk_gb": 1,
    "max_context_files": 30,
    "max_context_bytes": 120000,
    "max_project_bytes": 500000000,
    "max_generated_file_bytes": 100000,
    "require_macos_sandbox": True,
    "allow_test_execution": True,
    "quality": {
        "require_tests": True,
        "require_security_scan": True,
        "block_on_security_findings": True,
    },
}

POSITIVE_INTEGER_FIELDS = (
    "context_length",
    "max_tokens",
    "request_timeout_seconds",
    "max_consecutive_errors",
    "auto_recovery_delay_seconds",
    "max_auto_recovery_delay_seconds",
    "experiment_timeout_seconds",
    "max_context_files",
    "max_context_bytes",
    "max_project_bytes",
    "max_generated_file_bytes",
)


@dataclass(frozen=True)
class FactoryConfig:
    values: dict[str, Any]
    base_dir: Path

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def resolve(self, key: str) -> Path:
        path = Path(self.values[key]).expanduser()
        return (self.base_dir / path).resolve() if not path.is_absolute() else path.resolve()


def load_config(path: Path) -> FactoryConfig:
    path = path.expanduser().resolve()
    try:
        user_values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"設定ファイルがありません: {path}。config.example.json を config.json にコピーしてください。"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"設定ファイルのJSONが不正です: {exc}") from exc
    if not isinstance(user_values, dict):
        raise ValueError("設定ファイルの最上位はJSONオブジェクトである必要があります。")

    values = {**DEFAULTS, **user_values}
    quality = user_values.get("quality", {})
    if not isinstance(quality, dict):
        raise ValueError("quality はJSONオブジェクトである必要があります。")
    values["quality"] = {**DEFAULTS["quality"], **quality}
    for key in ("require_tests", "require_security_scan", "block_on_security_findings"):
        if not isinstance(values["quality"][key], bool):
            raise ValueError(f"quality.{key} は真偽値で指定してください。")
        if not values["quality"][key]:
            raise ValueError(f"quality.{key} は必須ゲートのため false にできません。")

    for key in POSITIVE_INTEGER_FIELDS:
        if not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] <= 0:
            raise ValueError(f"{key} は1以上の整数で指定してください。")
    if (
        not isinstance(values["idle_seconds"], int)
        or isinstance(values["idle_seconds"], bool)
        or values["idle_seconds"] <= 0
    ):
        raise ValueError("idle_seconds は1以上の整数で指定してください。")
    for key in ("retry_limit", "retry_backoff_seconds"):
        if not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] < 0:
            raise ValueError(f"{key} は0以上の整数で指定してください。")
    if values["max_auto_recovery_delay_seconds"] < values["auto_recovery_delay_seconds"]:
        raise ValueError(
            "max_auto_recovery_delay_seconds は auto_recovery_delay_seconds 以上にしてください。"
        )
    if not 0 < float(values["max_memory_percent"]) <= 100:
        raise ValueError("max_memory_percent は0より大きく100以下にしてください。")
    if float(values["max_load_per_cpu"]) <= 0 or float(values["min_free_disk_gb"]) < 0:
        raise ValueError("負荷および空きディスクの閾値が不正です。")
    if not isinstance(values["model"], str) or not values["model"].strip():
        raise ValueError("model を空にできません。")
    if not isinstance(values["ollama_url"], str):
        raise ValueError("Ollama URL はローカルホストを指定してください。")
    parsed_url = urlsplit(values["ollama_url"])
    try:
        ollama_port = parsed_url.port
    except ValueError as exc:
        raise ValueError("Ollama URL のポート番号が不正です。") from exc
    if (
        parsed_url.scheme != "http"
        or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in ("", "/")
    ):
        raise ValueError("Ollama URL は認証情報なしのローカルHTTPホストを指定してください。")
    if ollama_port is not None and not 1 <= ollama_port <= 65535:
        raise ValueError("Ollama URL のポートは1〜65535で指定してください。")
    if not isinstance(values["start_ollama_if_stopped"], bool):
        raise ValueError("start_ollama_if_stopped は真偽値で指定してください。")
    if values["web_host"] not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Web UIはローカルホストにのみバインドできます。")
    if (
        not isinstance(values["web_port"], int)
        or isinstance(values["web_port"], bool)
        or not 1024 <= values["web_port"] <= 65535
    ):
        raise ValueError("web_port は1024〜65535の整数で指定してください。")
    if not isinstance(values["web_search_enabled"], bool):
        raise ValueError("web_search_enabled は真偽値で指定してください。")
    if (
        not isinstance(values["web_search_timeout_seconds"], int)
        or isinstance(values["web_search_timeout_seconds"], bool)
        or not 3 <= values["web_search_timeout_seconds"] <= 30
    ):
        raise ValueError("web_search_timeout_seconds は3〜30秒で指定してください。")
    return FactoryConfig(values=values, base_dir=path.parent)
