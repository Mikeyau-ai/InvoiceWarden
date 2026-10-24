# Copied unchanged from Y:/RamWarden/ed25519.py (same file as Zeolite app/ed25519.py).
"""Ed25519 signatures (RFC 8032) in plain Python, so licence checks need no extra package.

RamWarden is stdlib-only by design (see requirements.txt), so this is a small reference
implementation rather than a dependency. It is not constant-time, which doesn't matter
here: RamWarden only ever *verifies* public licence signatures. Signing happens on the
sixthdaystudios.com server with the private key; sign() exists for tests and key setup.
"""
import hashlib

_P = 2 ** 255 - 19                                                    # field prime
_L = 2 ** 252 + 27742317777372353535851937790883648493                # group order
_D = -121665 * pow(121666, _P - 2, _P) % _P                           # curve constant
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512_int(data):
    """SHA-512 of data as a little-endian integer."""
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _add(a, b):
    """Add two points in extended coordinates (X, Y, Z, T)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = 2 * t1 * t2 * _D % _P
    Dd = 2 * z1 * z2 % _P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, p):
    """Scalar multiplication s * p by double-and-add."""
    q = (0, 1, 1, 0)                                                  # the neutral point
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(a, b):
    """True when two extended-coordinate points are the same point."""
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y, sign):
    """The x coordinate for y (and the sign bit), or None if there isn't one."""
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)                                    # the base point


def _compress(p):
    """A point as its 32-byte encoding."""
    x, y, z, _ = p
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    """32 bytes back to a point, or None if they don't encode one."""
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _expand(secret):
    """The signing scalar and prefix derived from a 32-byte private seed."""
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret):
    """The 32-byte public key for a 32-byte private seed."""
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret, msg):
    """A 64-byte signature of msg (tests / key setup only; the server signs real licences)."""
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _sha512_int(prefix + msg) % _L
    R = _compress(_mul(r, _G))
    h = _sha512_int(R + A + msg) % _L
    s = (r + h * a) % _L
    return R + int.to_bytes(s, 32, "little")


def verify(public, msg, signature):
    """True only if signature is a valid Ed25519 signature of msg by public."""
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _sha512_int(signature[:32] + public + msg) % _L
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))
