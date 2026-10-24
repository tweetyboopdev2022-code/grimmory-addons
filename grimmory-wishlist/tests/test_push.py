"""Chrome (Web Push) notifications: encryption, signing, subscriptions and the scripts' door."""
import base64
import hashlib
import hmac
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import call, wait_for

cryptography = pytest.importorskip("cryptography")
import webpush  # noqa: E402
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402


def test_encryption_matches_the_rfc_8291_example():
    local = ec.derive_private_key(int.from_bytes(webpush.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                  ec.SECP256R1())
    out = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                          "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                          "BTBZMqHH6r4Tts7J_aSIgg", salt=webpush.unb64u("DGv6ra1nlYgDCS1FRnbzlw"), local=local)
    assert webpush.b64u(out) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6"
        "PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_only_real_push_services_are_accepted():
    assert webpush.endpoint_ok("https://fcm.googleapis.com/fcm/send/abc")
    assert webpush.endpoint_ok("https://wns2-bl2p.notify.windows.com/w/?token=x")
    assert not webpush.endpoint_ok("http://fcm.googleapis.com/fcm/send/abc")
    assert not webpush.endpoint_ok("https://evil.example/fcm.googleapis.com")
    assert not webpush.endpoint_ok("https://127.0.0.1:6061/wishlist-api/push/send")


def test_the_signing_key_survives_a_restart(tmp_path):
    a = webpush.Vapid(str(tmp_path / "vapid.pem"))
    b = webpush.Vapid(str(tmp_path / "vapid.pem"))
    assert a.public == b.public and len(webpush.unb64u(a.public)) == 65
    assert (tmp_path / "vapid.pem").stat().st_mode & 0o077 == 0


# -- a pretend browser and push service ------------------------------------

class Browser:
    """What Chrome keeps: its own key pair and auth secret, which it hands out as a subscription."""

    def __init__(self, endpoint):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.endpoint = endpoint

    def subscription(self):
        pub = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        return {"endpoint": self.endpoint, "keys": {"p256dh": webpush.b64u(pub), "auth": webpush.b64u(self.auth)}}

    def read(self, body: bytes) -> dict:
        salt, idlen = body[:16], body[20]
        as_public, data = body[21:21 + idlen], body[21 + idlen:]
        ua_public = webpush.unb64u(self.subscription()["keys"]["p256dh"])
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        mac = lambda k, d: hmac.new(k, d, hashlib.sha256).digest()      # noqa: E731
        ikm = mac(mac(self.auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
        prk = mac(salt, ikm)
        plain = AESGCM(mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
            mac(prk, b"Content-Encoding: nonce\x00\x01")[:12], data, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


class FakePush(BaseHTTPRequestHandler):
    got: list = []
    status = 201

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        FakePush.got.append((self.path, dict(self.headers), self.rfile.read(n)))
        self.send_response(FakePush.status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def check_vapid(header: str, audience: str):
    t, k = (part.split("=", 1)[1] for part in header[len("vapid "):].split(", "))
    signing, sig = t.rsplit(".", 1)
    raw = webpush.unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
    try:
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   signing.encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        pytest.fail("bad VAPID signature")
    claims = json.loads(base64.urlsafe_b64decode(signing.split(".")[1] + "=="))
    assert claims["aud"] == audience and claims["sub"] == "https://lib.example"


@pytest.fixture()
def push(env, monkeypatch):
    FakePush.got, FakePush.status = [], 201
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakePush)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
    monkeypatch.setattr(webpush, "endpoint_ok", lambda e: e.startswith("http://127.0.0.1:"))
    env.app.cfg.ntfy_topic = ""          # Chrome only
    yield "http://127.0.0.1:%d" % srv.server_port
    srv.shutdown()


def turn_on(env, base, name="mac"):
    b = Browser(base + "/push/" + name)
    assert call(env, "POST", "/push/subscribe", "admin-token",
                {"subscription": b.subscription(), "label": "Chrome on Mac"}) == (200, {"ok": True})
    return b


def test_owner_alerts_go_to_the_owner_only(env, push):
    env.app.cfg.notify_owner = "alex"
    owner = turn_on(env, push)
    sam = Browser(push + "/push/sam")
    assert call(env, "POST", "/push/subscribe", "sam-token", {"subscription": sam.subscription(),
                                                              "label": "Chrome on Android"})[0] == 200
    status, body = call(env, "GET", "/push", "sam-token")
    assert status == 200 and body["owner"] is False and [d["label"] for d in body["devices"]] == ["Chrome on Android"]
    assert call(env, "GET", "/push", "admin-token")[1]["owner"] is True
    call(env, "POST", "/seen", "jordan-token", {"reason": "login"})        # a sign-in: owner only
    assert wait_for(lambda: FakePush.got)
    assert [p for p, _, _ in FakePush.got] == ["/push/mac"]
    assert "jordan" in owner.read(FakePush.got[0][2])["body"]
    # Sam can test and switch off only her own browser
    assert call(env, "POST", "/push/test", "sam-token", {"endpoint": owner.endpoint})[0] == 404
    assert call(env, "POST", "/push/unsubscribe", "sam-token", {"subscription": {"endpoint": owner.endpoint}}) \
        == (200, {"removed": False})


def test_the_requester_hears_when_their_book_arrives(env, push):
    turn_on(env, push)
    sam = Browser(push + "/push/sam")
    call(env, "POST", "/push/subscribe", "sam-token", {"subscription": sam.subscription()})
    status, body = call(env, "POST", "/items", "sam-token", {"title": "Onyx Storm", "author": "Rebecca Yarros"})
    assert wait_for(lambda: FakePush.got)                                  # the request: to alex
    FakePush.got = []
    assert call(env, "POST", "/items/%d/available" % body["id"], "admin-token")[0] == 200
    assert wait_for(lambda: FakePush.got)
    assert [p for p, _, _ in FakePush.got] == ["/push/sam"]
    assert sam.read(FakePush.got[0][2])["body"] == "Onyx Storm by Rebecca Yarros, which you asked for, is now in the library."
    env.app.push_arrivals()                                                # announced once
    import time
    time.sleep(0.3)
    assert len(FakePush.got) == 1


def test_a_subscription_somewhere_else_is_refused(env, push):
    b = Browser("https://evil.example/collect")
    assert call(env, "POST", "/push/subscribe", "admin-token", {"subscription": b.subscription()})[0] == 400
    bad = Browser(push + "/push/y").subscription()
    bad["keys"]["p256dh"] = "not-a-key"
    assert call(env, "POST", "/push/subscribe", "admin-token", {"subscription": bad})[0] == 400


def test_a_login_reaches_chrome_encrypted_and_signed(env, push):
    b = turn_on(env, push)
    assert call(env, "GET", "/push", "admin-token")[1]["devices"][0]["label"] == "Chrome on Mac"
    call(env, "POST", "/seen", "sam-token", {"reason": "login"})
    assert wait_for(lambda: FakePush.got)
    path, headers, body = FakePush.got[0]
    assert path == "/push/mac" and headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "normal"
    assert b"Sam" not in body                                  # the push service sees only ciphertext
    check_vapid(headers["Authorization"], push)
    msg = b.read(body)
    assert msg["body"] == "Sam (sam) signed in to Grimmory"
    assert msg["title"].endswith("Grimmory login") and msg["url"] == "https://lib.example/"
    assert not msg["urgent"]


def test_a_request_and_the_test_button(env, push):
    b = turn_on(env, push)
    call(env, "POST", "/items", "sam-token", {"title": "Onyx Storm", "author": "Rebecca Yarros"})
    assert wait_for(lambda: FakePush.got)
    msg = b.read(FakePush.got[0][2])
    assert msg["body"] == "Sam asked for Onyx Storm by Rebecca Yarros" and msg["url"] == "https://lib.example/#wishlist"
    assert call(env, "POST", "/push/test", "admin-token", {"endpoint": b.endpoint}) == (200, {"ok": True})
    assert "Test notification" in b.read(FakePush.got[-1][2])["title"]
    assert call(env, "POST", "/push/test", "admin-token", {"endpoint": push + "/push/other"})[0] == 404


def test_a_browser_that_went_away_is_forgotten(env, push):
    turn_on(env, push)
    FakePush.status = 410
    assert env.app.notify("hello") == 0
    assert env.app.store.push_subs() == []


def test_turning_off(env, push):
    b = turn_on(env, push)
    assert call(env, "POST", "/push/unsubscribe", "admin-token", {"subscription": {"endpoint": b.endpoint}}) \
        == (200, {"removed": True})
    assert env.app.notify("hello") == 0 and FakePush.got == []


def send(env, key, body):
    req = urllib.request.Request(env.url + "/push/send", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-Notify-Key": key} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_backup_script_needs_the_key(env, push):
    b = turn_on(env, push)
    msg = {"title": "Library backup FAILED", "message": "drive missing", "priority": "high"}
    assert send(env, "", msg)[0] == 403                         # no key set on the server: door shut
    env.app.cfg.notify_key = "k" * 32
    assert send(env, "wrong", msg)[0] == 403
    assert send(env, "k" * 32, msg) == (200, {"browsers": 1})
    got = b.read(FakePush.got[-1][2])
    assert got["body"] == "drive missing" and got["urgent"] and "FAILED" in got["title"]
    assert FakePush.got[-1][1]["Urgency"] == "high"
    assert send(env, "k" * 32, {"message": " "})[0] == 400
