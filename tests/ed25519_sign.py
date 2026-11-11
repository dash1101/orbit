"""Ed25519 signing for the tests only (RFC 8032, section 5.1.6): signed test releases without needing an openssl that
can do Ed25519 (macOS's can't). Orbit itself only ever verifies (orbit/ed25519.py)."""
import base64
import hashlib

from orbit.ed25519 import _G, _P, _Q, _SPKI_PREFIX, _mul


def _compress(pt):
    zinv = pow(pt[2], _P - 2, _P)
    x, y = pt[0] * zinv % _P, pt[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret):
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret, message):
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _Q
    big_r = _compress(_mul(r, _G))
    k = int.from_bytes(hashlib.sha512(big_r + pub + message).digest(), "little") % _Q
    return big_r + ((r + k * a) % _Q).to_bytes(32, "little")


def public_pem(secret):
    body = base64.b64encode(_SPKI_PREFIX + public_key(secret)).decode()
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----\n"
