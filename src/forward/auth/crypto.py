"""Encrypt secrets using Fernet without loading configuration or files."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "v1:"


class SecretBoxError(RuntimeError):
    """Raised when the box cannot be constructed or a value cannot be opened."""


class SecretBox:
    """Symmetric encryption with a versioned, self-describing envelope."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise SecretBoxError("key material must be 32 bytes")
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    # ------------------------------------------------------------- construction
    @staticmethod
    def derive_key(material: str) -> bytes:
        """Derive a 32-byte key from high-entropy machine secret text.

        Args:
            material: High-entropy machine secret text.

        Returns:
            A 32-byte key suitable for Fernet.
        """
        return hashlib.sha256(material.encode("utf-8")).digest()

    @classmethod
    def from_passphrase(cls, material: str) -> SecretBox:
        """Create a box from configured high-entropy key material.

        Args:
            material: High-entropy machine secret text.

        Returns:
            An initialized secret box.

        Raises:
            SecretBoxError: If the key material is empty.
        """
        if not material:
            raise SecretBoxError("empty key material")
        return cls(cls.derive_key(material))

    # ------------------------------------------------------------------- use
    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise SecretBoxError("cannot encrypt None")
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{PREFIX}{token}"

    def decrypt(self, envelope: str) -> str:
        if not envelope:
            raise SecretBoxError("cannot decrypt an empty value")
        if not envelope.startswith(PREFIX):
            # Refusing an unprefixed value is the point: it means an older or
            # foreign writer left plaintext in the column, and quietly returning
            # it would hide that.
            raise SecretBoxError("unrecognised envelope — refusing to treat it as plaintext")
        try:
            return self._fernet.decrypt(envelope[len(PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretBoxError(
                "could not decrypt: wrong key, or the value was tampered with"
            ) from exc


__all__ = ["PREFIX", "SecretBox", "SecretBoxError"]
