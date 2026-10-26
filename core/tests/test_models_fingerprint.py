"""Fingerprints — text_fp (the cross-store join key) and the History
hashes node_fp / subtree_fp / child_set_fp (store-split-design §3).
These tests pin canonical form v2."""

import pytest

from core.src.models.fingerprint import (
    FINGERPRINT_VERSION,
    strip_format_chars,
    canonical_text,
    child_set_fp,
    node_fp,
    subtree_fp,
    text_fp,
)

BASE = "The device shall retry after 30 s - see \"Timer\" and the UE's list: * item"


@pytest.mark.parametrize("variant", [
    "The device SHALL  retry\nafter 30 s - see \"Timer\" and the UE's list: * item",
    "  the device shall retry after 30 s - see \"timer\" and the ue's list: * item\t",
    "The device shall retry after 30\u00a0s - see \"Timer\" and the UE's list: * item",
    "The device shall retry after 30 s - see \u201cTimer\u201d and the UE\u2019s list: * item",
    "The device shall retry after 30 s \u2013 see \"Timer\" and the UE's list: * item",
    "The device shall retry after 30 s \u2014 see \"Timer\" and the UE's list: * item",
    "The device shall retry after 30 s \u2212 see \"Timer\" and the UE's list: * item",
    "The device shall retry after 30 s \u2011 see \"Timer\" and the UE's list: * item",
    "The device shall retry after 30 s - see \"Timer\" and the UE's list: \u2022 item",
    "The device shall retry after 30 s - see \"Timer\" and the UE's list: \u25cf item",
    "The device shall retry after 30 s - see \"Timer\" and the UE's list: \u00b7 item",
    "The device shall retry after \uff13\uff10 s - see \"Timer\" and the UE's list: * item",
])
def test_typographic_variants_hash_equal(variant):
    assert text_fp("Retry Timer", variant) == text_fp("retry timer", BASE)


@pytest.mark.parametrize("changed", [
    BASE.replace("30", "31"),
    BASE.replace("shall", "should"),
    BASE.replace("shall retry", "shall not retry"),
    BASE.replace(" s ", " ms "),
])
def test_meaningful_changes_hash_different(changed):
    assert text_fp("t", changed) != text_fp("t", BASE)


def test_title_and_body_are_separated():
    assert text_fp("ab", "c") != text_fp("a", "bc")


def test_canonical_text_rules():
    assert canonical_text(None) == ""
    assert canonical_text("  A \t B ") == "a b"
    assert canonical_text("\u2018x\u2019 \u201cy\u201d \u2013\u2014\u2212 \u2022\u25aa\u2023\u25e6") == \
        "'x' \"y\" --- ****"
    # v3: format characters (BOM, zero-width space/joiner, soft hyphen,
    # directional marks) are removed before anything else
    assert canonical_text("\ufeffThe\u200b device\u00ad shall\u200d retry\u200e") == "the device shall retry"
    assert text_fp("t", "\ufeffx") == text_fp("t", "x")
    assert strip_format_chars("a\ufeffb\u200bc") == "abc" and strip_format_chars(None) == ""


def test_values_carry_the_version_prefix():
    assert FINGERPRINT_VERSION == "v3"
    for fp in (text_fp("t", "x"), node_fp("v3:x"), subtree_fp("v3:x", []),
               child_set_fp([])):
        prefix, _, digest = fp.partition(":")
        assert prefix == "v3"
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_node_fp_covers_priority_and_applicability():
    t = text_fp("t", "x")
    base = node_fp(t, "Mandatory", ["Phone", "Tablet"])
    assert node_fp(t, "mandatory", ["tablet", " phone "]) == base   # canonical, sorted
    assert node_fp(t, "Optional", ["Phone", "Tablet"]) != base
    assert node_fp(t, "Mandatory", ["Phone"]) != base
    assert node_fp(text_fp("t", "y"), "Mandatory", ["Phone", "Tablet"]) != base
    assert node_fp(t) == node_fp(t, "", None)


def test_subtree_fp_is_order_sensitive_child_set_fp_is_not():
    n, a, b = "v3:n", "v3:a", "v3:b"
    assert subtree_fp(n, [a, b]) != subtree_fp(n, [b, a])
    assert child_set_fp([a, b]) == child_set_fp([b, a])
    assert subtree_fp(n, [a]) != subtree_fp("v3:m", [a])
