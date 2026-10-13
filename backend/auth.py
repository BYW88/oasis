#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Credential-based access for local and LAN clients.

Loopback peers and Host headers are not identity: TCP forwarders can make a
remote request look local. By default every caller needs the generated code,
and desktop/update/auth-management HTTP endpoints are unavailable. An explicit
OASIS_TRUST_LOOPBACK=1 is only for deployments with no loopback forwarding.
The hardened Windows launcher never enables that compatibility option.
"""

import hmac
import ipaddress
import json
import os
import secrets
import time

# Live next to the database, i.e. next to the .exe in a frozen build (run_backend
# sets DB_PATH there) and in backend/ for a source checkout.
_DB_PATH = os.environ.get('DB_PATH') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oasis.db')
AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(_DB_PATH)), 'oasis.auth.json')

# Any of these means the request passed through a proxy or tunnel, so it did not
# originate on this machine — no matter what the peer socket says.
_PROXY_HEADERS = (
    'x-forwarded-for',
    'x-forwarded-host',
    'x-forwarded-proto',
    'x-real-ip',
    'x-original-forwarded-for',
    'forwarded',
    'via',
    'cf-connecting-ip',
)

# The addresses a browser on *this* machine can be pointed at. 0.0.0.0 is in here
# because people do type it and it is unroutable from anywhere else, so a Host of
# 0.0.0.0 still cannot have come from another device.
_LOCAL_HOST_NAMES = {'localhost', '0.0.0.0'}

# The code is read off a console window and typed into a phone, so the alphabet
# drops the characters people mistake for each other (I/1, O/0) and the code is
# grouped for legibility. 26 random characters provide over 128 bits of entropy.
_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_CODE_LEN = 26
_GROUP = 4

# Brute-force throttle. Counted only against a *wrong* code: a request with no
# code at all is just an un-paired device (or a poll from a phone whose code was
# revoked), and counting those would let such a device lock the owner out by
# doing nothing but retrying in the background.
_FAIL_LIMIT = 5          # wrong codes tolerated before the lockout starts
_LOCK_BASE_S = 5         # first lockout, doubling with each further failure
_LOCK_MAX_S = 300

# key -> (consecutive failures, locked-until timestamp). The API is a single
# process with a single event loop, so a plain dict needs no locking.
_failures: dict[str, tuple[int, float]] = {}


# --------------------------------------------------------------------------- #
# The code itself
# --------------------------------------------------------------------------- #

def _load() -> dict:
    try:
        with open(AUTH_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as e:
        print(f'⚠️ 無法讀取遠端存取設定，將視為未開啟: {e}')
    return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
        with open(AUTH_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        # Best effort: keep it out of other local accounts' reach. No-op on
        # Windows, which ignores POSIX modes.
        os.chmod(AUTH_PATH, 0o600)
    except OSError as e:
        print(f'⚠️ 無法寫入遠端存取設定: {e}')


def normalize(code: str | None) -> str:
    """Canonical form of a code as typed: grouping and case are cosmetic."""
    if not code:
        return ''
    return ''.join(c for c in code.upper() if c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')


def get_code() -> str | None:
    """The current access code, or None when remote access is off."""
    code = _load().get('code')
    return code if isinstance(code, str) and code else None


def has_code() -> bool:
    return get_code() is not None


def enable() -> str:
    """Turn remote access on with a freshly minted code (replacing any existing
    one, which is what cuts every already-paired device off)."""
    raw = ''.join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    code = '-'.join(raw[i:i + _GROUP] for i in range(0, _CODE_LEN, _GROUP))
    _save({'code': code, 'updated_at': int(time.time())})
    _failures.clear()
    return code


def disable() -> None:
    """Turn remote access off: the code is deleted and every remote caller is
    refused outright again. This machine is unaffected — it never needed one."""
    _save({})
    _failures.clear()


def verify(candidate: str | None) -> bool:
    """Constant-time check of a presented code against the stored one."""
    code = get_code()
    if not code:
        return False
    return hmac.compare_digest(normalize(candidate), normalize(code))


def print_code() -> None:
    """Print the code to the backend's own console — the only place it is ever
    shown. The web UI never displays it, so a settings page left open (or a
    screenshot of it) gives the code away to nobody."""
    code = get_code()
    if not code:
        print('🔓 遠端存取未開啟：僅限本機使用，所有遠端連線一律拒絕。')
        return
    # No box: CJK glyphs are double-width and would never line up with the frame.
    rule = '═' * 40
    print(f'\n{rule}')
    print('🔐 遠端存取已開啟')
    print(f'   存取碼： {code}')
    print('   本機與其他裝置連線時都需輸入此存取碼。')
    print('   忘記時請在本機使用管理工具查看。')
    print(f'{rule}\n')


# --------------------------------------------------------------------------- #
# Request classification and throttling
# --------------------------------------------------------------------------- #

def _host_is_local(request) -> bool:
    """True when the caller addressed this backend *as* localhost.

    Host is client-controlled. This is only a compatibility filter and cannot
    authenticate a remote caller behind a TCP forwarder.
    """
    host = request.headers.get('host', '').strip()
    if not host:
        return False
    if host.startswith('['):          # [::1]:8000
        name = host[1:].split(']', 1)[0]
    else:                             # localhost:8000 / 127.0.0.1:8000 / localhost
        name = host.rsplit(':', 1)[0] if ':' in host else host
    name = name.lower()
    if name in _LOCAL_HOST_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_local_request(request) -> bool:
    """Optional compatibility trust, disabled by default.

    Enable only when there are no loopback proxies or port forwarders. The
    hardened LAN deployment requires credentials even from a loopback peer.
    """
    if os.environ.get('OASIS_TRUST_LOOPBACK') != '1':
        return False
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    if not _host_is_local(request):
        return False
    client = request.client
    if client is None or not client.host:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def client_key(request) -> str:
    """Use the actual peer; untrusted forwarding headers cannot pick a bucket.

    A proxy shares a bucket until a trusted-proxy policy is implemented.
    """
    client = request.client
    return client.host if client and client.host else 'unknown'



def lockout_remaining(key: str) -> int:
    """Seconds this key must wait before it may try again (0 = may proceed)."""
    entry = _failures.get(key)
    if not entry:
        return 0
    _, until = entry
    remaining = until - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


def register_failure(key: str) -> None:
    """Count a wrong access code and extend the lockout once past the limit."""
    now = time.time()
    if len(_failures) >= 4096:
        for old_key, (_, until) in list(_failures.items()):
            if until + 3600 < now:
                _failures.pop(old_key, None)
        if key not in _failures and len(_failures) >= 4096:
            _failures.pop(next(iter(_failures)))
    count = min(_failures.get(key, (0, 0.0))[0] + 1, 32)
    if count > _FAIL_LIMIT:
        backoff = min(_LOCK_BASE_S * (2 ** (count - _FAIL_LIMIT - 1)), _LOCK_MAX_S)
        until = time.time() + backoff
        print(f'🔒 存取碼驗證失敗 {count} 次（{key}），暫時鎖定 {backoff} 秒')
    else:
        until = 0.0
    _failures[key] = (count, until)


def clear_failures(key: str) -> None:
    _failures.pop(key, None)


def extract_code(request) -> str | None:
    """Pull the access code from the Authorization header.

    <video src> can send neither a header nor a cookie, so /api/stream is also
    allowed to carry it as a `token` query parameter — see the caller.
    """
    header = request.headers.get('authorization', '')
    if header.lower().startswith('bearer '):
        return header[7:].strip() or None
    return None
