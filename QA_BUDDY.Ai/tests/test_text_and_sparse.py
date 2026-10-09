from qabuddy.chunking import pack_tagged, split_lines, split_text
from qabuddy.sparse import encode_document, encode_query, tokenize
from qabuddy.text import Glossary, estimate_tokens, mask_secrets, normalize


def test_normalize_removes_invisible_characters_and_crlf():
    assert normalize("﻿Hello\r\nWorld​  \r\n\r\n\r\n\r\nEnd") == "Hello\nWorld\n\nEnd"


def test_mask_secrets_in_config_and_code():
    config = "username=qa@example.com\npassword=Test@4321\napi_key: abc123"
    masked = mask_secrets(config, config_file=True)
    assert "Test@4321" not in masked and "abc123" not in masked
    assert "username=qa@example.com" in masked
    code = 'const password = "hunter2";\nconst user = process.env.USER;\npassword: process.env.SECRET'
    masked = mask_secrets(code)
    assert "hunter2" not in masked
    assert "password: process.env.SECRET" in masked  # code that reads a secret stays readable
    assert mask_secrets("Authorization: Bearer abcdefghijklmnop") == "Authorization: Bearer ***"
    assert mask_secrets("https://bob:pa55w0rd@ci.example.com/job") == "https://***:***@ci.example.com/job"


def test_glossary_expands_abbreviations_and_synonyms(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text("abbreviations:\n  pom: page object model\nsynonyms:\n  - [login, sign in]\n", encoding="utf-8")
    glossary = Glossary.load(path)
    extra = glossary.expansions("Where is the POM for login?")
    assert "page object model" in extra and "sign in" in extra
    assert glossary.expansions("nothing to expand") == []


def test_tokenize_keeps_identifiers_whole_and_split():
    tokens = tokenize("WING-LOGIN-TC-001 failed in DriverManager.getDriver()")
    assert "wing-login-tc-001" in tokens
    assert {"wing", "login", "tc", "001"} <= set(tokens)
    assert "drivermanager.getdriver" in tokens
    assert "driver" in tokens and "manag" in tokens  # stemmed parts of DriverManager


def test_bm25_vectors_match_on_exact_ids():
    doc = encode_document("Test case INVALID-016: email containing only spaces")
    other = encode_document("Test case INVALID-017: password too long")
    query = encode_query("INVALID-016")

    def score(d):
        weights = dict(zip(d.indices, d.values))
        return sum(weights.get(i, 0) * v for i, v in zip(query.indices, query.values))

    assert score(doc) > score(other)


def test_split_text_respects_limit_and_overlaps():
    text = " ".join(f"Sentence number {i} talks about login tests." for i in range(200))
    chunks = split_text(text, max_tokens=100, overlap_tokens=20)
    assert len(chunks) > 5
    assert all(estimate_tokens(c) <= 100 for c in chunks)
    first_words = chunks[1].split()[:3]
    assert " ".join(first_words) in chunks[0]  # the next chunk starts with the previous one's tail


def test_pack_tagged_keeps_tags_per_chunk():
    units = [(f"paragraph {i} " * 20, i) for i in range(10)]
    packed = pack_tagged(units, max_tokens=120, overlap_tokens=0)
    assert all(tags for _, tags in packed)
    assert sorted(t for _, tags in packed for t in tags) == list(range(10))


def test_split_lines_keeps_line_numbers():
    lines = [f"line {i}" for i in range(1, 101)]
    windows = split_lines(lines, max_tokens=50, overlap_tokens=10)
    assert windows[0].first_line == 1
    assert windows[-1].last_line == 100
    assert windows[1].first_line <= windows[0].last_line  # overlap
