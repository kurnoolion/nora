"""req_fingerprint — the cross-store join key (store-split-design §3)."""

from core.src.models.fingerprint import normalize_content, req_fingerprint


def test_whitespace_and_case_do_not_change_the_fingerprint():
    a = req_fingerprint("Retry Timer", "The device SHALL  retry\nafter T1.")
    b = req_fingerprint("retry timer", "the device shall retry after t1.")
    assert a == b


def test_content_change_changes_the_fingerprint():
    assert req_fingerprint("t", "retry after T1") != req_fingerprint("t", "retry after T2")


def test_title_and_body_are_separated():
    assert req_fingerprint("ab", "c") != req_fingerprint("a", "bc")


def test_normalize_content_handles_none_and_empty():
    assert normalize_content(None) == ""
    assert normalize_content("  A \t B ") == "a b"


def test_fingerprint_is_sha256_hex():
    fp = req_fingerprint("t", "x")
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)
