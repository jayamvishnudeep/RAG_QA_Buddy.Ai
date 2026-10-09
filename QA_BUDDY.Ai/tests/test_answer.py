from qabuddy import answer
from qabuddy.llm import _ThinkFilter
from qabuddy.prompts import MODES
from qabuddy.retrieval import Retrieved, deduplicate, fuse
from qabuddy.store import Hit


def _hit(id_, text="body", doc="a.java", location="L1-L10", source="selenium"):
    return Retrieved(id_, 0.5, {"text": text, "doc_id": doc, "location": location, "source": source, "title": doc})


def test_rrf_fusion_rewards_agreement():
    semantic = [Hit("a", 0.9, {}), Hit("b", 0.8, {}), Hit("c", 0.7, {})]
    keyword = [Hit("c", 12.0, {}), Hit("a", 9.0, {})]
    fused = fuse({"semantic": semantic, "keyword": keyword})
    assert [h.id for h in fused] == ["a", "c", "b"]
    assert fused[0].ranks == {"semantic": 1, "keyword": 2}


def test_a_test_case_named_in_the_question_comes_first():
    titles = {"x": "WING-LOGIN-TC-001", "y": "WING-LOGIN-TC-002", "z": "WING-LOGIN-TC-042"}
    semantic = [Hit(i, 0.9, {"title": titles[i]}) for i in ("x", "y", "z")]
    keyword = [Hit(i, 9.0, {"title": titles[i]}) for i in ("y", "x", "z")]
    fused = fuse({"semantic": semantic, "keyword": keyword}, "What are the steps for WING-LOGIN-TC-042?")
    assert fused[0].id == "z"


def test_each_search_keeps_its_best_hit():
    both = [Hit(f"b{i}", 0.5, {"title": f"b{i}"}) for i in range(10)]
    fused = fuse({"semantic": [Hit("only-semantic", 0.9, {"title": "RetryAnalyzer.java"})] + both, "keyword": both})
    assert "only-semantic" in [h.id for h in fused[:2]]


def test_deduplicate_drops_overlapping_windows_and_identical_text():
    hits = [
        _hit("1", "header\nsame body", "log.txt", "L1-L40"),
        _hit("2", "other\nsomething", "log.txt", "L10-L45"),  # overlaps 1 by more than half
        _hit("3", "header2\nsame body", "copy.csv", "row 1"),  # same body as 1
        _hit("4", "x\nunique", "b.java", "L1-L5"),
    ]
    assert [h.id for h in deduplicate(hits)] == ["1", "4"]


def test_parse_citations_ignores_out_of_range_numbers():
    text = "Use the explicit wait [1]. Retries hide flakes [2][3]. See [2, 4] and [9]."
    assert answer.parse_citations(text, 4) == [1, 2, 3, 4]


def test_tool_style_citations_become_plain_while_streaming():
    style = answer.CitationStyle()
    pieces = ["Added the IP ", "【1", "†L1-L9】 and quarantined", " the test 【6†L10-L13】【3】", ". See [2"]
    out = "".join(style.feed(p) for p in pieces) + style.flush()
    assert out == "Added the IP [1] and quarantined the test [6][3]. See [2"
    assert answer.parse_citations(out, 6) == [1, 3, 6]


def test_follow_up_questions_are_searched_with_the_previous_one():
    history = [{"role": "user", "content": "negative login test cases"}, {"role": "assistant", "content": "..."}]
    assert answer.retrieval_query("now write them in Playwright", history) == "negative login test cases\nnow write them in Playwright"
    long = "What does the PRD say about two factor authentication requirements for enterprise accounts today?"
    assert answer.retrieval_query(long, history) == long


def test_pack_context_respects_the_budget():
    hits = [_hit(str(i), "x" * 4000) for i in range(5)]  # ~1000 tokens each
    assert len(answer.pack_context(hits, 2500)) == 2


def test_messages_hold_numbered_sources_and_strip_old_citations():
    used = [_hit("1", "Selenium framework · LoginPage.java\ncode")]
    history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "answer [1][2]"}]
    messages = answer.build_messages("q2", history, MODES["rca"], used, {"selenium": "Selenium framework"})
    assert messages[0]["role"] == "system" and "Failure analysis" in messages[0]["content"]
    assert messages[2]["content"] == "answer "
    assert messages[-1]["content"].startswith("Sources:\n\n[1] Selenium framework")
    assert messages[-1]["content"].endswith("Question: q2")


class FakeRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, sources=None, top_k=None, mode="hybrid"):
        self.calls.append((query, sources))
        return self.hits


class FakeLLM:
    configured = True
    model = "fake-model"
    provider = "Fake"

    def stream(self, messages):
        yield "delta", "Use explicit waits "
        yield "delta", "[1]."
        yield "usage", {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}


def test_answer_stream_events(settings):
    retriever = FakeRetriever([_hit("p1", "Selenium framework · WaitHelpers.java\nwait code")])
    events = list(answer.answer_stream(settings, "how do we wait?", mode="framework", retriever=retriever, llm=FakeLLM()))
    kinds = [e for e, _ in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and "delta" in kinds
    done = events[-1][1]
    assert done["cited"] == [1] and done["usage"]["prompt_tokens"] == 100
    assert retriever.calls[0][1] == list(MODES["framework"].sources)  # mode default filter applied


def test_answer_stream_without_llm_returns_sources(settings):
    class NoLLM(FakeLLM):
        configured = False

    retriever = FakeRetriever([_hit("p1", "header\nline one")])
    events = list(answer.answer_stream(settings, "anything", retriever=retriever, llm=NoLLM()))
    text = "".join(d for e, d in events if e == "delta")
    assert text.startswith("Best matching sources:") and "[1]" in text
    assert events[-1][1]["notice"].startswith("No LLM is configured")
    assert events[-1][1]["cited"] == [1]


def test_think_filter_hides_reasoning_split_across_chunks():
    f = _ThinkFilter()
    out = f.feed("Hello <thi") + f.feed("nk>secret plan</th") + f.feed("ink> world") + f.flush()
    assert out == "Hello world"


def test_rate_limited_request_is_retried_once(settings, monkeypatch):
    import dataclasses

    import httpx

    from qabuddy import llm as llm_module

    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "slow down"})
        body = 'data: {"choices":[{"delta":{"content":"Hi [1]"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)
    client = llm_module.LLM(dataclasses.replace(settings, llm_api_key="test", llm_base_url="https://llm.test/v1"))
    client._client = httpx.Client(base_url="https://llm.test/v1", transport=httpx.MockTransport(handler))
    events = list(client.stream([{"role": "user", "content": "hi"}]))
    assert ("delta", "Hi [1]") in events and len(calls) == 2
