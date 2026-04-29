"""License client: hardware fingerprint + activation API + local cache.

The desktop app talks to https://digitalinos-web.vercel.app for license
verification. Once verified, the JWT + grace-period info is cached locally
in `~/.digitalinos/license.json` so the user can keep working offline for
up to 7 days.

Free trial: each device gets 10 free renders before a license is required.
The trial counter lives in the same on-disk JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover - requests is in requirements.txt
    requests = None  # type: ignore


DEFAULT_API_BASE = "https://digitalinos-web.vercel.app"
TRIAL_LIMIT = 10
GRACE_PERIOD_SECONDS = 7 * 24 * 3600


def _config_dir() -> Path:
    """Per-user config directory. Override with `DIGITALINOS_CONFIG_DIR` for tests."""
    env = os.environ.get("DIGITALINOS_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".digitalinos"


def _state_path() -> Path:
    return _config_dir() / "license.json"


def device_fingerprint() -> str:
    """Stable per-device identifier (SHA256 of host info).

    Uses a deliberately small set of inputs so:
      - the same physical machine always produces the same ID
      - moving the executable / changing the user account doesn't change it
      - it doesn't leak personally-identifying info (it's a one-way hash)
    """
    parts = [
        platform.node(),                           # hostname
        platform.machine(),                        # CPU arch
        platform.system(),                         # OS name
        platform.processor() or "",                # CPU brand
        f"{uuid.getnode():012x}",                  # primary MAC (stable per NIC)
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


@dataclass
class LicenseState:
    """On-disk license cache."""
    license_key: str = ""
    plan: str = ""                       # "STARTER" | "PRO" | "STUDIO" | ""
    expires_at: float = 0.0              # unix seconds, 0 = never set
    last_verified_at: float = 0.0        # unix seconds, 0 = never verified
    jwt: str = ""                        # latest signed JWT from /api/license/verify
    trial_used: int = 0                  # videos consumed against the free trial
    device_id: str = ""

    @property
    def has_license(self) -> bool:
        return bool(self.license_key)

    @property
    def trial_remaining(self) -> int:
        return max(0, TRIAL_LIMIT - int(self.trial_used))

    @property
    def trial_exhausted(self) -> bool:
        return self.trial_remaining <= 0

    def is_active(self, now: Optional[float] = None) -> bool:
        """A license counts as active if EITHER:
          - it has been verified online within the grace period, OR
          - the cached `expires_at` is still in the future.
        """
        if not self.license_key:
            return False
        n = time.time() if now is None else now
        if self.expires_at > 0 and self.expires_at <= n:
            return False
        if self.last_verified_at > 0 and (n - self.last_verified_at) <= GRACE_PERIOD_SECONDS:
            return True
        # Within plan window but not freshly verified. Still allow (offline-first).
        return self.expires_at > n


def load_state() -> LicenseState:
    path = _state_path()
    if not path.exists():
        return LicenseState(device_id=device_fingerprint())
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return LicenseState(device_id=device_fingerprint())
    state = LicenseState(
        license_key=str(data.get("license_key", "") or ""),
        plan=str(data.get("plan", "") or ""),
        expires_at=float(data.get("expires_at", 0) or 0),
        last_verified_at=float(data.get("last_verified_at", 0) or 0),
        jwt=str(data.get("jwt", "") or ""),
        trial_used=int(data.get("trial_used", 0) or 0),
        device_id=str(data.get("device_id", "") or "") or device_fingerprint(),
    )
    if not state.device_id:
        state.device_id = device_fingerprint()
    return state


def save_state(state: LicenseState) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh, indent=2)


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

class LicenseError(RuntimeError):
    pass


def _api_base() -> str:
    return os.environ.get("DIGITALINOS_API_BASE", DEFAULT_API_BASE).rstrip("/")


def activate(license_key: str, *, timeout: float = 15.0) -> LicenseState:
    """Redeem (or re-activate) a license key for THIS device.

    Calls `/api/license/redeem` then `/api/license/verify` to obtain a fresh
    JWT, persists the result locally and returns the new state.
    """
    if requests is None:
        raise LicenseError(
            "The `requests` library is missing. Activate `.venv` and run:\n"
            "    pip install -r requirements.txt"
        )

    key = (license_key or "").strip().upper()
    if not key:
        raise LicenseError("Please paste a license key first.")

    state = load_state()
    if not state.device_id:
        state.device_id = device_fingerprint()

    redeem_url = f"{_api_base()}/api/license/redeem"
    verify_url = f"{_api_base()}/api/license/verify"
    payload = {
        "license_key": key,
        "device_id": state.device_id,
        "device_label": _device_label(),
    }

    try:
        r = requests.post(redeem_url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise LicenseError(f"Could not reach the licence server:\n{exc}") from exc

    if r.status_code == 409:
        raise LicenseError(
            "This key is already used on the maximum number of devices for "
            "its plan. Release a device from your dashboard at "
            "https://digitalinos-web.vercel.app/dashboard and try again."
        )
    if r.status_code == 404:
        raise LicenseError("That license key was not found. Check for typos.")
    if r.status_code == 410:
        raise LicenseError(
            "This license has expired. Renew it from the dashboard at "
            "https://digitalinos-web.vercel.app/pricing."
        )
    if not r.ok:
        try:
            err = r.json().get("error") or r.text
        except ValueError:
            err = r.text
        raise LicenseError(f"Activation failed ({r.status_code}): {err}")

    body = r.json() if r.content else {}
    plan = str(body.get("plan", "") or "")
    expires_at = float(body.get("expires_at", 0) or 0)

    # Verify to get a JWT + canonical state.
    try:
        v = requests.post(verify_url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise LicenseError(f"Could not verify the licence:\n{exc}") from exc

    jwt = ""
    if v.ok:
        vbody = v.json() if v.content else {}
        jwt = str(vbody.get("token", "") or "")
        if not plan:
            plan = str(vbody.get("plan", "") or "")
        if not expires_at:
            expires_at = float(vbody.get("expires_at", 0) or 0)

    state.license_key = key
    state.plan = plan or state.plan
    state.expires_at = expires_at or state.expires_at
    state.last_verified_at = time.time()
    state.jwt = jwt or state.jwt
    save_state(state)
    return state


def reverify(*, timeout: float = 10.0) -> LicenseState:
    """Refresh the JWT for the cached licence (called silently on app start)."""
    state = load_state()
    if not state.license_key or requests is None:
        return state

    try:
        r = requests.post(
            f"{_api_base()}/api/license/verify",
            json={"license_key": state.license_key, "device_id": state.device_id},
            timeout=timeout,
        )
    except requests.RequestException:
        return state  # offline; rely on grace period.

    if not r.ok:
        return state
    try:
        body = r.json()
    except ValueError:
        return state
    state.jwt = str(body.get("token", "") or state.jwt)
    state.plan = str(body.get("plan", state.plan) or state.plan)
    state.expires_at = float(body.get("expires_at", state.expires_at) or state.expires_at)
    state.last_verified_at = time.time()
    save_state(state)
    return state


def release(*, timeout: float = 10.0) -> None:
    """Release the current device from the licence (frees a seat)."""
    state = load_state()
    if not state.license_key or requests is None:
        _clear()
        return
    try:
        requests.post(
            f"{_api_base()}/api/license/release",
            json={"license_key": state.license_key, "device_id": state.device_id},
            timeout=timeout,
        )
    except requests.RequestException:
        pass
    _clear()


def _clear() -> None:
    state = load_state()
    state.license_key = ""
    state.plan = ""
    state.expires_at = 0.0
    state.last_verified_at = 0.0
    state.jwt = ""
    save_state(state)


def increment_trial() -> LicenseState:
    """Bump the local trial counter by one. No-op once licensed."""
    state = load_state()
    if state.has_license:
        return state
    state.trial_used = int(state.trial_used) + 1
    save_state(state)
    return state


def can_render() -> tuple[bool, str]:
    """Return `(allowed, reason)` for the next render."""
    state = load_state()
    if state.has_license:
        if state.is_active():
            return True, "licensed"
        return False, "license_expired"
    if state.trial_remaining > 0:
        return True, "trial"
    return False, "trial_exhausted"


def _device_label() -> str:
    name = platform.node() or socket.gethostname() or "Desktop"
    return f"{name} ({platform.system()})"
