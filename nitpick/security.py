"""Webhook signature verification."""

import hashlib
import hmac


def verify_signature(secret: str | None, body: bytes, signature_header: str | None) -> bool:
    """Check GitHub's X-Hub-Signature-256 header.

    Without this check, anyone who finds the webhook URL could send fake
    events and make the bot spend money on LLM calls.
    """
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Constant-time comparison avoids leaking information through timing.
    return hmac.compare_digest(expected, signature_header)
