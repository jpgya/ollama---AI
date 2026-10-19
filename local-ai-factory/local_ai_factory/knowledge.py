from __future__ import annotations

import hashlib
import json
import base64
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class WebSearchError(RuntimeError):
    pass


class _NoSearchRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WebSearchError("検索サービスのHTTPリダイレクトを拒否しました。")


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: dict[str, str] | None = None
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._buffer: list[str] = []
        self._depth = 0
        self._inside_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and "b_algo" in classes and self._active is None:
            self._active = {"title": "", "url": "", "snippet": ""}
            self._depth = 1
            return
        if self._active is not None and tag == "li":
            self._depth += 1
        if tag == "h2" and self._active is not None:
            self._inside_title = True
        is_search_title = tag == "a" and self._inside_title
        if is_search_title:
            if self._active is None:
                self._active = {"title": "", "url": "", "snippet": ""}
            self._active["url"] = _resolve_search_url(attributes.get("href") or "")
            self._capture = "title"
            self._capture_tag = tag
            self._buffer = []
        elif self._active is not None and classes.intersection(
            {"result__snippet", "result__snippet-a", "result__snippet-b", "b_lineclamp2"}
        ):
            self._capture = "snippet"
            self._capture_tag = tag
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._active is not None and tag == self._capture_tag:
            text = " ".join("".join(self._buffer).split())
            if self._capture == "title":
                self._active["title"] = text
            elif self._capture == "snippet" and text:
                self._active["snippet"] = text
            self._capture = None
            self._capture_tag = None
        if tag == "h2":
            self._inside_title = False
        if tag == "li" and self._active is not None:
            self._depth -= 1
            if self._depth <= 0:
                if self._active["title"] and self._active["url"]:
                    self.results.append(self._active)
                self._active = None
                self._capture = None
                self._capture_tag = None


def _resolve_search_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.hostname == "www.bing.com" and parsed.path == "/ck/a":
        encoded = urllib.parse.parse_qs(parsed.query).get("u", [])
        if encoded and encoded[0].startswith("a1"):
            try:
                decoded = base64.urlsafe_b64decode(encoded[0][2:] + "===").decode("utf-8")
                parsed = urllib.parse.urlparse(decoded)
            except (ValueError, UnicodeDecodeError):
                return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, "")
    )


class WebSearch:
    def __init__(self, timeout_seconds: int = 12, max_results: int = 5):
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results

    def search(self, query: str) -> list[dict[str, str]]:
        query = " ".join(query.split())
        if not query or len(query) > 240:
            raise ValueError("検索語は1〜240文字で指定してください。")
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            url, headers={"User-Agent": "LocalAIFactory/0.1 (local web search)"},
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoSearchRedirect()
        )
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                final_url = urllib.parse.urlparse(response.geturl())
                if (
                    final_url.scheme != "https"
                    or final_url.hostname != "www.bing.com"
                ):
                    raise WebSearchError("検索サービスの予期しない転送先を拒否しました。")
                body = response.read(2_000_000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WebSearchError(
                f"Web検索に接続できませんでした。ネットワークを確認してください: {exc}"
            ) from exc
        parser = _SearchResultParser()
        parser.feed(body)
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for result in parser.results:
            url = result["url"]
            if url and url not in seen:
                result["snippet"] = result["snippet"][:1200]
                result["title"] = result["title"][:300]
                results.append(result)
                seen.add(url)
            if len(results) >= self.max_results:
                break
        return results


class KnowledgeStore:
    def __init__(self, data_root: Path):
        self.path = data_root / "datasets" / "web_knowledge.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._lock, self.path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
        return list(reversed(records[-max(1, min(limit, 100)):]))

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with self._lock, self.path.open(encoding="utf-8") as source:
            return sum(1 for line in source if line.strip())

    def add(self, result: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        title = result.get("title")
        snippet = result.get("snippet")
        url = _resolve_search_url(str(result.get("url", "")))
        if (
            not isinstance(title, str) or not title.strip()
            or not isinstance(snippet, str) or not snippet.strip()
            or not url
        ):
            raise ValueError("知識には有効なタイトル、概要、HTTP(S) URLが必要です。")
        title = title.strip()[:300]
        snippet = snippet.strip()[:1200]
        digest = hashlib.sha256(f"{url}\n{title}\n{snippet}".encode("utf-8")).hexdigest()
        record = {
            "id": digest[:16],
            "digest": digest,
            "title": title,
            "snippet": snippet,
            "url": url,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "user-approved web search result; unverified",
        }
        with self._lock:
            if self.path.exists() and self.path.stat().st_size >= 10_000_000:
                raise ValueError("保存済みWeb知識が10MBの上限に達しました。内容を整理してから再試行してください。")
            if self.path.exists():
                with self.path.open(encoding="utf-8") as source:
                    count = 0
                    for line in source:
                        count += 1
                        try:
                            if json.loads(line).get("digest") == digest:
                                return record, False
                        except (json.JSONDecodeError, AttributeError):
                            continue
                    if count >= 1000:
                        raise ValueError("保存済みWeb知識が1000件の上限に達しました。整理してから再試行してください。")
            with self.path.open("a", encoding="utf-8") as destination:
                destination.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record, True

    def context(self, limit: int = 12, max_chars: int = 12000) -> str:
        entries: list[str] = []
        total = 0
        for item in reversed(self.list(limit)):
            excerpt = (
                f"[{item['title']}]\n{item['snippet']}\n"
                f"Source: {item['url']} (unverified, user-approved search note)\n"
            )
            if total + len(excerpt) > max_chars:
                continue
            entries.append(excerpt)
            total += len(excerpt)
        return "\n".join(entries) if entries else "(保存済みWeb知識はありません)"
