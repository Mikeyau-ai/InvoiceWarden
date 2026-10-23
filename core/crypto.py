"""Local secret encryption.

Strategy:
  * A single Fernet master key is generated once and stored in the Windows
    Credential Manager via ``keyring`` (which is DPAPI-protected per-user).
  * Individual secrets (API keys, client secrets, passwords) are Fernet
    encrypted and kept in the SQLite ``settings`` table - never in plain text.

If keyring has no usable backend (e.g. running on a stripped-down box) we fall
back to a key file in DATA_DIR with 0600-style permissions. This is clearly
weaker and is logged.
"""
from __future__ import annotations

import base64
import logging
import os

import keyring
from cryptography.fernet import Fernet, InvalidToken

from config import (DATA_DIR, KEYRING_SERVICE, KEYRING_USERNAME,
                    LEGACY_KEYRING_SERVICE)

log = logging.getLogger(__name__)

_FALLBACK_KEY_FILE = DATA_DIR / ".masterkey"


def _load_or_create_key() -> bytes:
    """Return the Fernet master key, creating and persisting it on first use."""
    try:
        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if existing:
            return existing.encode("utf-8")
        # First launch since the InvoiceM8 -> InvoiceWarden rename: reuse the old
        # master key (minting a new one would orphan every saved secret). The
        # old entry is left in place so an older build still works.
        legacy = keyring.get_password(LEGACY_KEYRING_SERVICE, KEYRING_USERNAME)
        if legacy:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, legacy)
            except Exception:                 # still usable this session
                log.warning("Could not copy the master key to the new "
                            "credential entry; using the old one.")
            return legacy.encode("utf-8")
        key = Fernet.generate_key()
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.decode("utf-8"))
        # Read it straight back: a backend that accepts the write but does not
        # persist would silently mint a new key on every launch, orphaning
        # every stored secret.
        if keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) != key.decode("utf-8"):
            log.error("Credential Manager did not persist the master key; "
                      "falling back to a local key file so saved secrets "
                      "survive a restart.")
            raise RuntimeError("keyring did not persist the master key")
        log.info("Generated new master key in Windows Credential Manager.")
        return key
    except Exception as exc:  # keyring backend missing / locked
        log.warning("keyring unavailable (%s); using local key file fallback.", exc)
        if _FALLBACK_KEY_FILE.exists():
            return _FALLBACK_KEY_FILE.read_bytes()
        key = Fernet.generate_key()
        _FALLBACK_KEY_FILE.write_bytes(key)
        try:
            os.chmod(_FALLBACK_KEY_FILE, 0o600)
        except OSError:
            pass
        return key


class SecretBox:
    """Thin wrapper around Fernet for encrypting/decrypting setting values."""

    def __init__(self) -> None:
        """Load (or mint) the master key and prepare the Fernet cipher."""
        #: number of stored secrets that could not be decrypted this session
        self.decrypt_failures = 0
        self._fernet = Fernet(_load_or_create_key())

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string, returning a urlsafe token string."""
        if plaintext is None:
            plaintext = ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt a token produced by :meth:`encrypt`. Returns '' on failure.

        A failure means the master key changed since the value was written, so
        the stored secret is unrecoverable and must be re-entered. We count
        these so the UI can say so instead of silently showing a blank field
        that still looks populated.
        """
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            self.decrypt_failures += 1
            log.error("Failed to decrypt a stored secret (master key changed) - "
                      "it must be re-entered.")
            return ""

    @staticmethod
    def b64_basic(username: str, password: str) -> str:
        """Helper for MYOB company-file credentials: base64(user:pass)."""
        raw = f"{username}:{password}".encode("utf-8")
        return base64.b64encode(raw).decode("ascii")
