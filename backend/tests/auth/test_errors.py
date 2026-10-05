"""AuthError hierarchy and HTTP status mapping."""

from octave.auth.errors import (
    AccountDisabled,
    AlreadyBootstrapped,
    AuthError,
    InvalidCredentials,
    NotLastOwnerGuard,
    SessionExpired,
    UsernameTaken,
    http_status_of,
)


def test_hierarchy() -> None:
    for exc_type in (
        InvalidCredentials,
        AccountDisabled,
        SessionExpired,
        UsernameTaken,
        NotLastOwnerGuard,
        AlreadyBootstrapped,
    ):
        assert issubclass(exc_type, AuthError)


def test_http_status_mapping() -> None:
    assert http_status_of(InvalidCredentials()) == 401
    assert http_status_of(AccountDisabled()) == 401
    assert http_status_of(SessionExpired()) == 401
    assert http_status_of(UsernameTaken()) == 409
    assert http_status_of(NotLastOwnerGuard()) == 409
    assert http_status_of(AlreadyBootstrapped()) == 409
