"""Tests for the offline-capable, HMAC-protected licensing client.

These cover the pieces that are easy to test in isolation:
  - HMAC tag round-trip on save/load
  - Tampering with the trial counter on disk locks the trial
  - Legacy (pre-tamper-protection) plaintext files are still accepted once
  - Grace period constant matches what we promised the user (30 days)
  - `can_render()` reports the correct reason in trial / exhausted / licensed
    / expired states

Network-dependent paths (`activate`, `reverify`, `release`, server trial sync)
are exercised with a stubbed `requests` module so the suite stays offline.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.licensing import client as lc


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets a fresh ~/.digitalinos via DIGITALINOS_CONFIG_DIR."""
    monkeypatch.setenv("DIGITALINOS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("DIGITALINOS_API_BASE", "https://example.invalid")
    yield
    # nothing to clean up — tmp_path is per-test


def test_grace_period_is_thirty_days() -> None:
    """Hard guarantee for the user-facing offline guarantee."""
    assert lc.GRACE_PERIOD_SECONDS == 30 * 24 * 3600


def test_save_and_load_round_trip() -> None:
    state = lc.LicenseState(trial_used=3, device_id="dev-1")
    lc.save_state(state)
    loaded = lc.load_state()
    assert loaded.trial_used == 3
    # Saved file is HMAC-tagged.
    raw = json.loads((Path(os.environ["DIGITALINOS_CONFIG_DIR"]) / "license.json").read_text())
    assert "payload" in raw and "tag" in raw
    assert isinstance(raw["tag"], str) and len(raw["tag"]) == 64


def test_tampered_counter_locks_trial() -> None:
    """If the user edits trial_used to 0 in the JSON, load detects it and locks."""
    state = lc.LicenseState(trial_used=8, device_id="dev-1")
    lc.save_state(state)
    state_path = Path(os.environ["DIGITALINOS_CONFIG_DIR"]) / "license.json"
    raw = json.loads(state_path.read_text())
    raw["payload"]["trial_used"] = 0  # tamper!
    state_path.write_text(json.dumps(raw))

    reloaded = lc.load_state()
    assert reloaded.trial_used == lc.TRIAL_LIMIT, "Tampered counter must be locked, not trusted."
    assert reloaded.trial_remaining == 0


def test_legacy_plaintext_state_is_accepted() -> None:
    """Existing users on the old plaintext format upgrade smoothly."""
    state_path = Path(os.environ["DIGITALINOS_CONFIG_DIR"]) / "license.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "license_key": "DGIT-AAAA-BBBB-CCCC-DDDD",
        "plan": "PRO",
        "expires_at": time.time() + 86400,
        "last_verified_at": time.time(),
        "jwt": "x.y.z",
        "trial_used": 4,
        "device_id": "dev-1",
    }))

    loaded = lc.load_state()
    assert loaded.license_key == "DGIT-AAAA-BBBB-CCCC-DDDD"
    assert loaded.plan == "PRO"
    assert loaded.trial_used == 4
    # And the next save() upgrades the file format.
    lc.save_state(loaded)
    raw = json.loads(state_path.read_text())
    assert "payload" in raw and "tag" in raw


def test_increment_trial_offline_uses_local_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the server can't be reached, the counter still ticks locally."""
    monkeypatch.setattr(lc, "_report_render_to_server", lambda *_a, **_kw: None)

    s0 = lc.load_state()
    assert s0.trial_used == 0
    s1 = lc.increment_trial()
    s2 = lc.increment_trial()
    assert s1.trial_used == 1
    assert s2.trial_used == 2


def test_increment_trial_takes_max_of_local_and_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server count overrides local if higher (uninstall-and-reinstall case)."""
    # Pretend the server says we've already used 7 (e.g. fresh install on a
    # machine that previously used the trial elsewhere).
    monkeypatch.setattr(lc, "_report_render_to_server", lambda *_a, **_kw: 7)

    s = lc.increment_trial()
    assert s.trial_used == 7  # max(local 1, server 7)


def test_increment_trial_caps_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "_report_render_to_server", lambda *_a, **_kw: 9999)
    s = lc.increment_trial()
    assert s.trial_used == lc.TRIAL_LIMIT


def test_boot_sync_trial_pulls_higher_server_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot sync must adopt the server's count if higher than local."""
    state = lc.LicenseState(trial_used=2, device_id="dev-1")
    lc.save_state(state)
    monkeypatch.setattr(lc, "_fetch_server_trial", lambda *_a, **_kw: 5)
    after = lc.boot_sync_trial()
    assert after.trial_used == 5


def test_boot_sync_trial_does_not_decrement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server reporting a *lower* count never moves us backwards."""
    state = lc.LicenseState(trial_used=8, device_id="dev-1")
    lc.save_state(state)
    monkeypatch.setattr(lc, "_fetch_server_trial", lambda *_a, **_kw: 1)
    after = lc.boot_sync_trial()
    assert after.trial_used == 8


def test_boot_sync_trial_offline_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    state = lc.LicenseState(trial_used=4, device_id="dev-1")
    lc.save_state(state)
    monkeypatch.setattr(lc, "_fetch_server_trial", lambda *_a, **_kw: None)
    after = lc.boot_sync_trial()
    assert after.trial_used == 4


def test_can_render_states() -> None:
    # Brand-new state -> trial mode, allowed.
    allowed, reason = lc.can_render()
    assert allowed is True and reason == "trial"

    # Exhaust the trial.
    s = lc.LicenseState(trial_used=lc.TRIAL_LIMIT, device_id="dev-1")
    lc.save_state(s)
    allowed, reason = lc.can_render()
    assert allowed is False and reason == "trial_exhausted"

    # Licensed and active.
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="PRO",
        expires_at=time.time() + 86400,
        last_verified_at=time.time(),
        device_id="dev-1",
    )
    lc.save_state(s)
    allowed, reason = lc.can_render()
    assert allowed is True and reason == "licensed"

    # Licensed but expired *and* outside grace period.
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="PRO",
        expires_at=time.time() - 86400,
        last_verified_at=time.time() - lc.GRACE_PERIOD_SECONDS - 1,
        device_id="dev-1",
    )
    lc.save_state(s)
    allowed, reason = lc.can_render()
    assert allowed is False and reason == "license_expired"


def test_is_active_inside_grace_period_without_fresh_verify() -> None:
    """A licensed user who's been offline for 29 days stays active."""
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="PRO",
        expires_at=time.time() + 365 * 86400,
        last_verified_at=time.time() - 29 * 86400,
        device_id="dev-1",
    )
    assert s.is_active() is True


def test_is_active_outside_grace_period_locks() -> None:
    """31 days offline > 30-day grace -> not active any more."""
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="PRO",
        expires_at=time.time() - 1,  # plan window also closed
        last_verified_at=time.time() - 31 * 86400,
        device_id="dev-1",
    )
    assert s.is_active() is False


def test_device_fingerprint_is_stable_within_a_run() -> None:
    a = lc.device_fingerprint()
    b = lc.device_fingerprint()
    assert a == b
    assert len(a) == 32  # 32 hex chars


def test_release_calls_server_then_clears_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """release() must POST to /api/license/release-device with the cached
    key + this device's hardware id, then wipe the local cache."""
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="PRO",
        expires_at=time.time() + 86400,
        last_verified_at=time.time(),
        jwt="cached-jwt",
        device_id="dev-fp-1",
    )
    lc.save_state(s)

    captured: dict[str, Any] = {}

    class _Resp:
        ok = True
        status_code = 200
        content = b'{"ok":true,"released":true}'
        def json(self) -> dict[str, Any]:
            return {"ok": True, "released": True}

    def fake_post(url: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> _Resp:
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(lc.requests, "post", fake_post)

    lc.release()

    assert captured["url"].endswith("/api/license/release-device"), captured
    assert captured["json"] == {
        "key": "DGIT-AAAA-BBBB-CCCC-DDDD",
        "hardware_id": "dev-fp-1",
    }
    cleared = lc.load_state()
    assert cleared.license_key == ""
    assert cleared.plan == ""
    assert cleared.jwt == ""


def test_release_offline_still_clears_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the server is unreachable, release() still wipes the local cache so
    the user isn't stuck with a stale key on this PC."""
    s = lc.LicenseState(
        license_key="DGIT-AAAA-BBBB-CCCC-DDDD",
        plan="STUDIO",
        expires_at=time.time() + 86400,
        last_verified_at=time.time(),
        device_id="dev-fp-2",
    )
    lc.save_state(s)

    def fake_post(*_a: Any, **_k: Any) -> Any:
        raise lc.requests.RequestException("network down")

    monkeypatch.setattr(lc.requests, "post", fake_post)

    lc.release()  # must not raise

    cleared = lc.load_state()
    assert cleared.license_key == ""
