from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .factory import Factory
from .ollama import OllamaError
from .store import Store


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-ai-factory",
        description="Ollamaを使う安全優先のローカルAI開発工場",
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--dry-run", action="store_true", help="書き込み・モデル呼び出し前に設定と対象を確認")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="自律ループをフォアグラウンドで開始")
    sub.add_parser("stop", help="稼働中ループへ停止要求")
    sub.add_parser("status", help="状態とシステム負荷を表示")
    sub.add_parser("pause", help="一時停止")
    sub.add_parser("resume", help="再開")
    once = sub.add_parser("once", help="1サイクルだけ実行")
    once.add_argument("--task", help="キューより優先するタスク説明")
    once.add_argument("--dry-run", action="store_true", dest="dry_run", default=argparse.SUPPRESS)
    sub.add_parser("report", help="品質・処理時間・成功率を表示")
    sub.add_parser("models", help="Ollamaモデルと採用モデルを表示")
    sub.add_parser("tasks", help="タスク一覧を表示")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        factory = Factory(config, dry_run=args.dry_run)
        if args.dry_run:
            _print_json(factory.once())
            return 0
        if args.command == "once":
            _print_json(factory.once(args.task))
        elif args.command == "start":
            print("Local AI Factory を開始しました。Ctrl+C または `stop` で停止します。")
            factory.run_loop()
        elif args.command in ("stop", "pause", "resume"):
            _print_json(factory.control(args.command))
        elif args.command == "status":
            _print_json(factory.status())
        elif args.command == "report":
            store = Store(config.resolve("data_root"))
            report = store.report()
            report["success_rate"] = (
                report["approved"] / report["total"] if report["total"] else None
            )
            report["recent_runs"] = store.recent_runs()
            report_path = config.resolve("data_root") / "reports" / "latest.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report["saved_to"] = str(report_path)
            _print_json(report)
        elif args.command == "models":
            _print_json(factory.models())
        elif args.command == "tasks":
            _print_json(factory.store.list_tasks())
        return 0
    except KeyboardInterrupt:
        print("\n停止しました。", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OllamaError, OSError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
