import base64
from pathlib import Path

import pytest

from local_ai_factory.knowledge import (
    KnowledgeStore,
    WebSearch,
    _SearchResultParser,
    _resolve_search_url,
)


def test_search_parser_extracts_snippet_and_rejects_unsafe_urls():
    parser = _SearchResultParser()
    parser.feed(
        '<ol><li class="b_algo"><h2><a href="https://example.org/docs">'
        'Local docs</a></h2><p class="b_lineclamp2">A safe <b>local</b> project note.'
        '</p></li></ol>'
    )
    assert parser.results == [{
        "title": "Local docs",
        "url": "https://example.org/docs",
        "snippet": "A safe local project note.",
    }]
    assert _resolve_search_url("javascript:alert(1)") == ""


def test_resolves_bing_redirects_to_the_original_source():
    encoded_url = base64.urlsafe_b64encode(b"https://docs.example.org/page").decode().rstrip("=")
    assert _resolve_search_url(
        f"https://www.bing.com/ck/a?u=a1{encoded_url}&ntb=1"
    ) == "https://docs.example.org/page"


def test_knowledge_save_deduplicates_and_provides_cited_context(tmp_path: Path):
    store = KnowledgeStore(tmp_path / "data")
    result = {
        "title": "Python security",
        "snippet": "Prefer safe path handling.",
        "url": "https://example.org/security",
    }
    record, added = store.add(result)
    _, duplicate_added = store.add(result)
    assert added is True
    assert duplicate_added is False
    assert store.count() == 1
    context = store.context()
    assert record["url"] in context
    assert "unverified" in context


def test_knowledge_rejects_non_http_urls(tmp_path: Path):
    with pytest.raises(ValueError):
        KnowledgeStore(tmp_path / "data").add({
            "title": "bad", "snippet": "nope", "url": "file:///etc/passwd",
        })


def test_search_rejects_empty_or_oversized_queries():
    search = WebSearch()
    with pytest.raises(ValueError):
        search.search(" ")
    with pytest.raises(ValueError):
        search.search("x" * 241)
