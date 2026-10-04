import pytest

from app.conversation.sessions import SessionCapacityError, SessionStore


def test_capacity_is_bounded_and_released_on_delete() -> None:
    store = SessionStore(capacity=1)
    first = store.create("call-one")
    assert store.create("call-one") is first
    with pytest.raises(SessionCapacityError):
        store.create("call-two")
    store.delete(first.session_id)
    store.delete(first.session_id)
    assert store.create("call-two").call_id == "call-two"


def test_idle_expiry_releases_capacity() -> None:
    now = [0.0]
    store = SessionStore(capacity=1, idle_ttl_seconds=10, clock=lambda: now[0])
    first = store.create("call-one")
    now[0] = 10
    assert store.get(first.session_id) is None
    assert store.create("call-two").call_id == "call-two"


def test_maximum_age_expires_even_an_active_session() -> None:
    now = [0.0]
    store = SessionStore(idle_ttl_seconds=10, maximum_age_seconds=15, clock=lambda: now[0])
    session = store.create("call-one")
    now[0] = 9
    assert store.get(session.session_id) is session
    now[0] = 15
    assert store.get(session.session_id) is None


def test_clear_releases_all_sessions() -> None:
    store = SessionStore()
    session = store.create("call-one")
    store.clear()
    assert store.get(session.session_id) is None
