from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

from .config import FactoryConfig


class CandidateError(ValueError):
    pass


FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "system", "popen", "Popen", "urlopen",
    "fork", "posix_spawn", "startfile",
}
FORBIDDEN_PREFIXES = ("exec", "spawn")
SECRET_FILE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials", "secrets.json",
}
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
    "build", "dist", "target", "factory-data",
}


def validate_relative_path(name: str) -> Path:
    candidate = Path(name)
    if (
        not name
        or not candidate.parts
        or candidate.is_absolute()
        or any(part in ("..", "") for part in candidate.parts)
        or name.startswith(("/", "~"))
        or "\\" in name
    ):
        raise CandidateError(f"不正な相対ファイルパスです: {name!r}")
    return candidate


def parse_generation(text: str) -> dict[str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CandidateError(f"生成結果をJSONとして解釈できません: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise CandidateError("生成結果は {\"files\": {相対パス: 内容}} 形式である必要があります。")
    files: dict[str, str] = {}
    for name, content in payload["files"].items():
        path = validate_relative_path(name)
        if not isinstance(content, str):
            raise CandidateError(f"{name}: ファイル内容は文字列である必要があります。")
        if len(content.encode("utf-8")) > 100_000:
            raise CandidateError(f"{name}: 生成ファイルが上限を超えています。")
        if any(part in SKIP_DIRS for part in path.parts) or _is_sensitive(name):
            raise CandidateError(f"保護対象ディレクトリには書き込めません: {name}")
        files[name] = content
    if not files:
        raise CandidateError("生成結果に変更ファイルがありません。")
    return files


def _is_sensitive(name: str) -> bool:
    lower = name.lower()
    return Path(name).name.lower().startswith(".env") or Path(name).name.lower() in SECRET_FILE_NAMES or any(
        token in lower for token in (".pem", ".key", "secret", "credential")
    )


def build_context(root: Path, max_files: int, max_bytes: int) -> str:
    entries: list[str] = []
    total = 0
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in SKIP_DIRS
            and not (name.startswith(".") and name != ".github")
            and not (current / name).is_symlink()
        )
        paths.extend(current / name for name in filenames)
    for path in sorted(paths):
        relative = path.relative_to(root)
        if _is_sensitive(relative.as_posix()) or path.is_symlink():
            continue
        try:
            raw = path.read_bytes()
            decoded = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if b"\0" in raw or len(raw) > 30_000:
            continue
        if (
            any(marker in decoded for marker in ("-----BEGIN PRIVATE KEY-----", "AKIA", "ghp_"))
            or re.search(
                r"(?im)^\s*(?:password|passwd|api[_-]?key|secret|token|private[_-]?key)\s*[:=]\s*\S{8,}",
                decoded,
            )
        ):
            continue
        block = f"\n--- {relative.as_posix()} ---\n{decoded}\n"
        size = len(block.encode("utf-8"))
        if len(entries) >= max_files or total + size > max_bytes:
            break
        entries.append(block)
        total += size
    return "".join(entries) or "(読み込めるプロジェクトファイルはありません)"


def copy_project(source: Path, destination: Path, max_bytes: int) -> None:
    if not source.is_dir():
        raise CandidateError(f"対象プロジェクトが存在しません: {source}")

    total_bytes = 0
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(directory)
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_DIRS
            and name != ".git"
            and not _is_sensitive(name)
            and not (current / name).is_symlink()
        ]
        for name in filenames:
            path = current / name
            if _is_sensitive(name) or path.is_symlink():
                continue
            try:
                total_bytes += path.stat().st_size
            except OSError as exc:
                raise CandidateError(f"プロジェクトファイルを確認できません: {path}: {exc}") from exc
            if total_bytes > max_bytes:
                raise CandidateError(
                    f"対象プロジェクトのコピー対象が上限 {max_bytes} bytes を超えました。"
                )

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in SKIP_DIRS or name == ".git"}
        ignored.update(name for name in names if _is_sensitive(name))
        ignored.update(name for name in names if (Path(directory) / name).is_symlink())
        return ignored

    shutil.copytree(source, destination, ignore=ignore, symlinks=False)


def apply_files(root: Path, files: dict[str, str], max_bytes: int) -> None:
    for name, content in files.items():
        relative = validate_relative_path(name)
        if len(content.encode("utf-8")) > max_bytes:
            raise CandidateError(f"{name}: 生成ファイルが設定上限を超えています。")
        target = (root / relative).resolve()
        if not target.is_relative_to(root.resolve()):
            raise CandidateError(f"プロジェクト外への書き込みを拒否しました: {name}")
        if target.exists() and target.is_symlink():
            raise CandidateError(f"シンボリックリンクへの書き込みを拒否しました: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def security_scan(root: Path, changed_files: set[str] | None = None) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if changed_files is not None and path.relative_to(root).as_posix() not in changed_files:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                name = function.id if isinstance(function, ast.Name) else (
                    function.attr if isinstance(function, ast.Attribute) else ""
                )
                if name in FORBIDDEN_CALLS or name.startswith(FORBIDDEN_PREFIXES):
                    findings.append(f"{path.relative_to(root)}:{node.lineno}: 制限対象の呼び出し {name}()")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                blocked = {
                    "subprocess", "socket", "requests", "httpx", "http", "ftplib",
                    "smtplib", "aiohttp", "websocket", "ctypes", "multiprocessing",
                }
                if any(
                    name.split(".")[0] in blocked or name.split(".")[0] == "urllib"
                    for name in names
                ):
                    findings.append(f"{path.relative_to(root)}:{node.lineno}: 制限対象のモジュール import")
    return findings


def _mac_sandbox_available(config: FactoryConfig) -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def _sandbox_command(candidate: Path, argv: list[str]) -> list[str]:
    sandbox = shutil.which("sandbox-exec")
    if sandbox is None:
        raise RuntimeError("macOS sandbox-exec が見つかりません。隔離テストを実行できません。")
    safe_candidate = str(candidate.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    profile = (
        '(version 1)\n'
        '(allow default)\n'
        '(deny network-inbound)\n'
        '(deny network-outbound)\n'
        f'(deny file-read* (require-all (subpath "{Path.home()}") '
        f'(require-not (subpath "{safe_candidate}")) '
        f'(require-not (subpath "{sys.prefix}")) '
        f'(require-not (subpath "{sys.base_prefix}"))))\n'
        f'(deny file-write* (require-all '
        f'(require-not (subpath "{safe_candidate}")) '
        '(require-not (literal "/dev/null"))))\n'
    )
    return [sandbox, "-p", profile, *argv]


def evaluate(
    root: Path, config: FactoryConfig, changed_files: set[str] | None = None,
    *, timeout_seconds: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "syntax": {"status": "passed", "errors": []},
        "tests": {"status": "not_run", "detail": ""},
        "security": {"status": "passed", "findings": []},
        "approved": False,
        "reasons": [],
    }
    python_files = list(root.rglob("*.py"))
    for path in python_files:
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            result["syntax"]["errors"].append(f"{path.relative_to(root)}: {exc}")
    if result["syntax"]["errors"]:
        result["syntax"]["status"] = "failed"

    findings = security_scan(root, changed_files)
    result["security"]["findings"] = findings
    if findings:
        result["security"]["status"] = "failed"

    test_paths = list(root.rglob("test_*.py")) + list(root.rglob("*_test.py"))
    test_paths = [
        path for path in test_paths
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    ]
    if not test_paths:
        result["tests"]["status"] = "incomplete"
        result["tests"]["detail"] = "テストファイルがありません"
    elif not config.allow_test_execution:
        result["tests"]["status"] = "incomplete"
        result["tests"]["detail"] = "allow_test_execution=false のため実行していません"
    elif timeout_seconds is not None and timeout_seconds <= 0:
        result["tests"]["status"] = "incomplete"
        result["tests"]["detail"] = "実験全体の時間制限に達したため実行していません"
    elif config.require_macos_sandbox and not _mac_sandbox_available(config):
        result["tests"]["status"] = "incomplete"
        result["tests"]["detail"] = "必須のmacOS sandbox-execが利用できません"
    else:
        temp_dir = root / ".factory-tmp"
        temp_dir.mkdir(exist_ok=True)
        python_executable = str(Path(sys.executable).resolve())
        command = [python_executable, "-m", "pytest", "-q"]
        if config.require_macos_sandbox:
            command = _sandbox_command(root, command)
        python_paths = {
            str(Path(path).resolve())
            for key in ("purelib", "platlib")
            if (path := sysconfig.get_paths().get(key))
        }
        python_paths.add(str(root))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(temp_dir),
            "TMPDIR": str(temp_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(sorted(python_paths)),
            "PIP_NO_INDEX": "1",
        }
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                test_timeout = (
                    config.experiment_timeout_seconds
                    if timeout_seconds is None
                    else min(config.experiment_timeout_seconds, timeout_seconds)
                )
                stdout, stderr = process.communicate(timeout=test_timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                stdout, stderr = process.communicate()
                result["tests"] = {
                    "status": "failed",
                    "detail": f"テストが時間切れです ({exc.timeout}秒)\n{stdout}\n{stderr}".strip()[-12000:],
                }
                stdout = stderr = ""
            if result["tests"]["status"] == "not_run":
                detail = (stdout + "\n" + stderr).strip()[-12000:]
                result["tests"] = {
                    "status": "passed" if process.returncode == 0 else "failed",
                    "detail": detail,
                    "return_code": process.returncode,
                }
        except OSError as exc:
            result["tests"] = {"status": "incomplete", "detail": f"テスト起動失敗: {exc}"}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if result["syntax"]["status"] != "passed":
        result["reasons"].append("Python構文検査が失敗しました")
    if result["tests"]["status"] != "passed":
        result["reasons"].append("必須テストが成功していないか、評価未完了です")
    if result["security"]["status"] != "passed":
        result["reasons"].append("セキュリティ検査で制限対象が見つかりました")
    result["approved"] = not result["reasons"]
    return result
