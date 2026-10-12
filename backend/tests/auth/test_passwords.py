"""PasswordHasher protocol: Argon2Hasher round-trip + DummyHasher seam.

Only this module calls real argon2 (deliberately ~100ms per hash); every
other suite injects DummyHasher so tests stay fast.
"""

from octave.auth.passwords import Argon2Hasher, DummyHasher


def test_argon2_round_trip() -> None:
    hasher = Argon2Hasher()
    encoded = hasher.hash("correct horse battery staple")
    assert encoded != "correct horse battery staple"
    assert hasher.verify(encoded, "correct horse battery staple")


def test_argon2_wrong_password_false() -> None:
    hasher = Argon2Hasher()
    encoded = hasher.hash("right")
    assert not hasher.verify(encoded, "wrong")


def test_argon2_verify_garbage_false() -> None:
    """Corrupt/non-argon2 hashes must return False, not raise."""
    hasher = Argon2Hasher()
    assert not hasher.verify("not-an-argon2-hash", "anything")


def test_argon2_needs_rehash_false_for_fresh_hash() -> None:
    hasher = Argon2Hasher()
    encoded = hasher.hash("password")
    assert not hasher.needs_rehash(encoded)


def test_dummy_hasher_round_trip() -> None:
    hasher = DummyHasher()
    encoded = hasher.hash("secret")
    assert hasher.verify(encoded, "secret")
    assert not hasher.verify(encoded, "other")
    assert not hasher.needs_rehash(encoded)
