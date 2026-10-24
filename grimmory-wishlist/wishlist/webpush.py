"""Browser push notifications (Web Push), so alerts appear as Chrome notifications.

The browser hands us a subscription (an endpoint at Google's, Mozilla's, Apple's or
Microsoft's push service, plus two keys). To notify, the message is encrypted for that
browser (RFC 8291, aes128gcm) and posted to the endpoint, signed with this server's own
VAPID key (RFC 8292). The push service only ever sees ciphertext.

Needs the `cryptography` package; without it push is simply switched off.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:                                     # pragma: no cover - checked at startup
    ec = None

log = logging.getLogger("wishlist")

#: the push services browsers use; a subscription pointing anywhere else is refused
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "web.push.apple.com", "notify.windows.com")


def available() -> bool:
    return ec is not None


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def endpoint_ok(endpoint: str) -> bool:
    u = urllib.parse.urlparse(endpoint)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _point(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


class Vapid:
    """This server's signing key. Kept in a file next to the database: a new key would
    silently break every browser that already said yes."""

    def __init__(self, path: str = ""):
        self.key = None
        if path and Path(path).exists():
            self.key = serialization.load_pem_private_key(Path(path).read_bytes(), None)
        if self.key is None:
            self.key = ec.generate_private_key(ec.SECP256R1())
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(self.key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
        self.public = b64u(_point(self.key))

    def header(self, endpoint: str, contact: str) -> str:
        u = urllib.parse.urlparse(endpoint)
        claims = {"aud": "%s://%s" % (u.scheme, u.netloc), "exp": int(time.time()) + 12 * 3600, "sub": contact}
        signing = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + \
            b64u(json.dumps(claims, separators=(",", ":")).encode())
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return "vapid t=%s, k=%s" % (jwt, self.public)


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, local=None) -> bytes:
    """The aes128gcm body for one browser (RFC 8291), as a single record."""
    ua_public = unb64u(p256dh)
    secret = unb64u(auth)
    local = local or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(local)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = local.exchange(ec.ECDH(), peer)
    ikm = _hmac(_hmac(secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + body


def send(vapid: Vapid, sub: dict, message: dict, contact: str, ttl: int = 86400,
         urgency: str = "normal", timeout: float = 10) -> int:
    """Post one notification. Returns the push service's HTTP status (0 if unreachable).
    404 and 410 mean the browser has gone away for good."""
    body = encrypt(json.dumps(message).encode("utf-8"), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Authorization": vapid.header(sub["endpoint"], contact),
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": urgency})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError) as exc:
        log.warning("push to %s failed: %s", urllib.parse.urlparse(sub["endpoint"]).hostname, exc)
        return 0
