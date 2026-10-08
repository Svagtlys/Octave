"""Token generation + hashing primitives."""

from octave.auth.tokens import generate_token, hash_token


def test_hash_token_sha256_hex_deterministic() -> None:
    digest = hash_token("hello")
    assert digest == hash_token("hello")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    # sha256("hello") — fixed vector guards against swapping the algorithm.
    assert digest == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_hash_token_distinct_inputs() -> None:
    assert hash_token("a") != hash_token("b")


def test_generate_token_urlsafe_32_bytes_distinct() -> None:
    tokens = {generate_token() for _ in range(8)}
    assert len(tokens) == 8
    for token in tokens:
        # 32 urlsafe bytes -> at least 42 chars; no padding stripped to empty.
        assert len(token) >= 32
