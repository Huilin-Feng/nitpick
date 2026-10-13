import hashlib
import hmac

from nitpick.security import verify_signature

SECRET = "s3cret"
BODY = b'{"action": "opened"}'


def _sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature():
    assert verify_signature(SECRET, BODY, _sign(BODY))


def test_wrong_secret():
    assert not verify_signature(SECRET, BODY, _sign(BODY, "other"))


def test_tampered_body():
    assert not verify_signature(SECRET, BODY + b" ", _sign(BODY))


def test_missing_header_or_secret():
    assert not verify_signature(SECRET, BODY, None)
    assert not verify_signature(None, BODY, _sign(BODY))
    assert not verify_signature(SECRET, BODY, "sha1=abc")
