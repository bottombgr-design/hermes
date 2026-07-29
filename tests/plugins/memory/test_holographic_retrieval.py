"""Tests for FactRetriever FTS5 query sanitization.

These tests cover the fix where raw natural-language queries passed to
FTS5 MATCH were AND-joined by default, dropping recall to zero on any
multi-word prose query. The sanitizer drops stopwords and OR-joins the
remaining content tokens as phrase literals.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")  # retrieval module imports numpy indirectly

from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore


# ---------------------------------------------------------------------------
# _sanitize_fts_query — unit tests (no DB required)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,expected_tokens",
    [
        # stopwords dropped
        ("what happened with the deployment rollback", {"happened", "deployment", "rollback"}),
        # single content word passes through
        ("compaction", {"compaction"}),
        # all stopwords → falls back to raw
        ("the and of", None),  # None = sentinel for fallback-to-raw
        # empty string → empty output
        ("", ""),
        # FTS5 operator characters stripped
        ("context: length-probe", {"context", "lengthprobe"}),
        # trailing punctuation stripped by tokenizer
        ("hello, world!", {"hello", "world"}),
    ],
)
def test_sanitize_fts_query_extracts_content_tokens(query, expected_tokens):
    result = FactRetriever._sanitize_fts_query(query)

    if expected_tokens == "":
        assert result == ""
        return

    if expected_tokens is None:
        # Pathological case: all stopwords — should fall back to raw query
        assert result == query
        return

    # OR-joined phrase literals: `"tok1" OR "tok2" OR ...`
    # Extract the tokens between quotes, order-independent.
    import re
    matches = re.findall(r'"([^"]+)"', result)
    assert set(matches) == expected_tokens, f"got {result!r}"


def test_sanitize_fts_query_never_crashes_on_fts5_specials():
    """Queries with FTS5 operator characters must not produce malformed SQL."""
    problematic = [
        'test " query',
        "test * query",
        "test (a OR b) query",
        "test^2 query",
        "test:colon query",
        "test-hyphen query",
        "a" * 1000,  # long query
    ]
    for q in problematic:
        result = FactRetriever._sanitize_fts_query(q)
        # We just need it to return a string without raising
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Integration test — actually run _fts_candidates against an in-memory DB
# ---------------------------------------------------------------------------

@pytest.fixture
def retriever_with_facts(tmp_path):
    """MemoryStore seeded with a few facts for retrieval tests."""
    db_path = tmp_path / "test_facts.db"
    store = MemoryStore(str(db_path))
    store.add_fact(
        content="The Thursday deployment rollback failed because of stale migration state.",
        category="project",
    )
    store.add_fact(
        content="Compaction settings tuned to 0.85 threshold.",
        category="tool",
    )
    store.add_fact(
        content="Venice.ai advertises availableContextTokens inside model_spec.",
        category="tool",
    )
    retriever = FactRetriever(store=store)
    yield retriever
    store.close()


def test_prefetch_recovers_prose_query(retriever_with_facts):
    """A natural-language query should now match the relevant fact.

    Before the sanitizer fix, 'what happened with the deployment rollback'
    returned zero hits because FTS5 required every token to co-occur.
    """
    results = retriever_with_facts.search(
        "what happened with the deployment rollback"
    )
    assert len(results) >= 1
    # The top hit should be the deployment rollback fact
    assert "deployment rollback" in results[0]["content"].lower()


def test_prefetch_single_keyword_still_works(retriever_with_facts):
    """Single-term queries (pre-fix working case) remain working."""
    results = retriever_with_facts.search("compaction")
    assert len(results) >= 1
    assert "Compaction" in results[0]["content"] or "compaction" in results[0]["content"].lower()


def test_prefetch_stopword_only_query_empty(retriever_with_facts):
    """Pure stopword queries return zero results but don't crash."""
    # Pass to _sanitize_fts_query directly first so we know what happens
    assert FactRetriever._sanitize_fts_query("the and of") == "the and of"
    # search() handles the likely-zero-hit case gracefully
    results = retriever_with_facts.search("the and of")
    # Either zero results or it errored-gracefully to [] — both are fine
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# retrieval_count bookkeeping — the retriever is the live read path, so it
# must record retrievals too. Previously only MemoryStore.search_facts (which
# has no callers) incremented, leaving the counter permanently zero.
# ---------------------------------------------------------------------------

def _counts(store):
    return dict(
        store._conn.execute("SELECT fact_id, retrieval_count FROM facts").fetchall()
    )


def test_search_increments_retrieval_count(retriever_with_facts):
    """search() bumps the counter for facts it actually returns."""
    store = retriever_with_facts.store
    assert set(_counts(store).values()) == {0}

    results = retriever_with_facts.search("compaction")
    assert len(results) >= 1

    after = _counts(store)
    for fact in results:
        assert after[fact["fact_id"]] == 1
    # Facts that were not returned stay untouched
    returned = {f["fact_id"] for f in results}
    assert all(c == 0 for fid, c in after.items() if fid not in returned)


def test_probe_increments_retrieval_count(retriever_with_facts):
    """probe() shares the same bookkeeping path as search()."""
    store = retriever_with_facts.store
    results = retriever_with_facts.probe("compaction")

    after = _counts(store)
    for fact in results:
        assert after[fact["fact_id"]] >= 1


def test_zero_hit_search_records_nothing(retriever_with_facts):
    """An empty result set must not touch any counter."""
    store = retriever_with_facts.store
    before = _counts(store)
    retriever_with_facts.search("zzzznomatchzzzz")
    assert _counts(store) == before


def test_record_retrievals_is_idempotent_per_call(retriever_with_facts):
    """Two searches produce two increments — the counter accumulates."""
    store = retriever_with_facts.store
    first = retriever_with_facts.search("compaction")
    fact_id = first[0]["fact_id"]
    assert _counts(store)[fact_id] == 1
    retriever_with_facts.search("compaction")
    assert _counts(store)[fact_id] == 2


def test_record_retrievals_empty_list_is_noop(retriever_with_facts):
    """Guard clause: empty id list must not issue a malformed UPDATE."""
    store = retriever_with_facts.store
    before = _counts(store)
    store.record_retrievals([])
    assert _counts(store) == before
