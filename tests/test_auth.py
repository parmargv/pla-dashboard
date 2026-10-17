import pytest

from pla import auth
from pla.auth import (
    AuthUser,
    AuthValidationError,
    authenticate,
    change_own_password,
    create_first_admin,
    create_user,
    reset_password,
    update_user,
    verify_password,
    hash_password,
)
from pla.db import init_db, session_scope
from pla.models import Role


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1_000)
    orig = auth.hash_password
    monkeypatch.setattr(auth, "hash_password", lambda pw, iterations=1_000: orig(pw, iterations))


@pytest.fixture()
def url(tmp_path):
    u = f"sqlite:///{tmp_path}/auth.db"
    init_db(u)
    return u


def test_hash_roundtrip():
    h = hash_password("secret123", iterations=1000)
    assert verify_password("secret123", h) and not verify_password("wrong123", h)
    assert not verify_password("x", "garbage")


def test_first_admin_only_once(url):
    with session_scope(url) as s:
        create_first_admin(s, username="Admin", password="admin1234", full_name="Admin")
        with pytest.raises(AuthValidationError):
            create_first_admin(s, username="other", password="admin1234", full_name="X")


def test_env_admin_seed(tmp_path):
    u = f"sqlite:///{tmp_path}/seed.db"
    init_db(u, "boss", "boss12345")
    with session_scope(u) as s:
        assert isinstance(authenticate(s, "BOSS", "boss12345"), AuthUser)


def test_login_and_lockout(url):
    with session_scope(url) as s:
        create_user(s, username="clerk", password="clerk123", full_name="Clerk")
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with session_scope(url) as s:
            assert authenticate(s, "clerk", "nope") == auth.INVALID_CREDENTIALS
    with session_scope(url) as s:
        assert "locked" in authenticate(s, "clerk", "nope")
    with session_scope(url) as s:  # correct password still refused while locked
        assert isinstance(authenticate(s, "clerk", "clerk123"), str)
    with session_scope(url) as s:
        uid = s.scalar(auth.select(auth.User.id))
        auth.unlock_user(s, uid)
    with session_scope(url) as s:
        user = authenticate(s, "clerk", "clerk123")
        assert isinstance(user, AuthUser) and user.can_edit and not user.is_admin


def test_unknown_and_disabled_users(url):
    with session_scope(url) as s:
        assert authenticate(s, "ghost", "whatever1") == auth.INVALID_CREDENTIALS
        create_first_admin(s, username="admin", password="admin1234", full_name="A")
        v = create_user(s, username="viewer", password="viewer123", full_name="V", role=Role.VIEWER)
        update_user(s, v.id, full_name="V", role=Role.VIEWER, is_active=False)
        assert authenticate(s, "viewer", "viewer123") == auth.INVALID_CREDENTIALS


def test_last_admin_protected(url):
    with session_scope(url) as s:
        a = create_first_admin(s, username="admin", password="admin1234", full_name="A")
        with pytest.raises(AuthValidationError, match="administrator"):
            update_user(s, a.id, full_name="A", role=Role.EDITOR, is_active=True)


def test_password_rules_and_change(url):
    with session_scope(url) as s:
        with pytest.raises(AuthValidationError):
            create_user(s, username="user1", password="short", full_name="U")
        with pytest.raises(AuthValidationError):
            create_user(s, username="bad name", password="good1234", full_name="U")
        u = create_user(s, username="user1", password="good1234", full_name="U")
        with pytest.raises(AuthValidationError):
            change_own_password(s, u.id, "wrong", "newpass123")
        change_own_password(s, u.id, "good1234", "newpass123")
        assert isinstance(authenticate(s, "user1", "newpass123"), AuthUser)
        reset_password(s, u.id, "reset1234")
        assert isinstance(authenticate(s, "user1", "reset1234"), AuthUser)
