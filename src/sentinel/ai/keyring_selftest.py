"""Credential Manager self-test (V5): ``VYRXONE-Sentinel.exe --keyring-selftest`` (or ``python -m sentinel
--keyring-selftest``).

It exercises the real credential store with a *separate* service name (``VYRXONE Sentinel SelfTest``) and fake
values, so it can never touch a real API key or proxy sign-in. It also plants an unrelated "canary" entry first and
verifies that the canary is unchanged at the end. Everything it creates is removed again, even after a failure.
Nothing here uses the network. The result is printed; it is a manual Windows check, not proof from this repository.
"""

from __future__ import annotations

import secrets as _rand
from typing import Any, Callable

TEST_SERVICE = "VYRXONE Sentinel SelfTest"
CANARY_SERVICE = "VYRXONE Sentinel SelfTest Canary"


def run(backend: Any = "auto", out: Callable[[str], None] = print) -> bool:
    """Return True only if every step passed. ``backend`` is a keyring-like object (tests pass a fake)."""
    if backend == "auto":
        from .secrets import _default_backend
        backend = _default_backend()
        try:
            import keyring
            name = f"{type(keyring.get_keyring()).__module__}.{type(keyring.get_keyring()).__name__}"
        except Exception:
            name = "unavailable"
        out(f"keyring backend: {name}")
    if backend is None:
        out("FAIL  no secure credential store is available (keyring missing, or only a null/plain-text backend).")
        return False
    token = _rand.token_hex(8)
    canary_user, canary_value = "canary-" + token, "canary-value-" + _rand.token_hex(8)
    user = "selftest-" + token
    first, second = "first-" + _rand.token_hex(8), "second-" + _rand.token_hex(8)
    results: list[tuple[str, bool]] = []

    def step(label: str, fn: Callable[[], bool]) -> None:
        try:
            ok = bool(fn())
        except Exception as exc:                    # report only the type: never a value
            out(f"FAIL  {label} ({type(exc).__name__})")
            results.append((label, False))
            return
        out(("PASS  " if ok else "FAIL  ") + label)
        results.append((label, ok))

    try:
        backend.set_password(CANARY_SERVICE, canary_user, canary_value)
        step("store a value", lambda: backend.set_password(TEST_SERVICE, user, first) is None or True)
        step("read it back", lambda: backend.get_password(TEST_SERVICE, user) == first)
        step("replace it", lambda: (backend.set_password(TEST_SERVICE, user, second), backend.get_password(
            TEST_SERVICE, user) == second)[1])
        step("delete it", lambda: (backend.delete_password(TEST_SERVICE, user), backend.get_password(
            TEST_SERVICE, user) is None)[1])
        step("an unrelated entry was not modified", lambda: backend.get_password(CANARY_SERVICE, canary_user) == canary_value)
    finally:
        for svc, usr in ((TEST_SERVICE, user), (CANARY_SERVICE, canary_user)):
            try:
                backend.delete_password(svc, usr)
            except Exception:
                pass
    ok = all(r for _, r in results) and len(results) == 5
    out("RESULT: " + ("all checks passed" if ok else "FAILED") + " (self-test entries removed)")
    return ok
