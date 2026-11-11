"""Checking an Ed25519 signature, in plain Python (RFC 8032, section 5.1.7).

Nova signs each release's SHA256SUMS with its release key. openssl can check that on most Linux systems, but not
on Windows (no openssl) or macOS (LibreSSL can't do Ed25519), so Orbit does it itself: this is the RFC's own
reference algorithm, which only needs Python's integers and hashlib. It verifies; it never signs, so the usual
worry about timing leaks (they matter for secret keys) doesn't apply. A check takes a few milliseconds.
"""
import base64
import hashlib

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
# The key inside a PEM "PUBLIC KEY" for Ed25519: this fixed DER header, then the 32 key bytes.
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def _add(a, b):
    """Point addition in extended coordinates (X, Y, Z, T)."""
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def verify(public_key, message, signature):
    """True when `signature` (64 bytes) is `public_key`'s (32 bytes) signature of `message` (bytes)."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _Q
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


def key_from_pem(pem):
    """The 32 key bytes of an Ed25519 public key in PEM form ("-----BEGIN PUBLIC KEY-----"). ValueError otherwise."""
    body = "".join(ln.strip() for ln in str(pem).splitlines() if ln.strip() and not ln.startswith("-----"))
    der = base64.b64decode(body, validate=True)
    if len(der) != 44 or not der.startswith(_SPKI_PREFIX):
        raise ValueError("not an Ed25519 public key")
    return der[len(_SPKI_PREFIX):]
