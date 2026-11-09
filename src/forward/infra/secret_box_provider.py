"""Load configured key material for secret encryption."""

from __future__ import annotations

from pathlib import Path

from forward.auth.crypto import SecretBox, SecretBoxError


class ConfiguredSecretBoxProvider:
    """Create a secret box from the configured key or existing key file.

    Args:
        key_material: Optional high-entropy key from application settings.
        key_file: Configured path for the local operator key.
    """

    def __init__(self, *, key_material: str, key_file: Path) -> None:
        self._key_material = key_material.strip()
        self._key_file = key_file

    def create(self) -> SecretBox:
        """Load key material and return an authenticated secret box.

        Returns:
            The secret box configured for this application.

        Raises:
            SecretBoxError: If no key is configured or the key file is empty.
            OSError: If the configured key file cannot be read.
        """
        if self._key_material:
            return SecretBox.from_passphrase(self._key_material)

        try:
            key_material = self._key_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise SecretBoxError(
                "Encryption key is not configured. Set FORWARD_SECRET_KEY or create "
                f"the configured key file at {self._key_file}."
            ) from exc
        if not key_material:
            raise SecretBoxError(f"key file {self._key_file} is empty")
        return SecretBox.from_passphrase(key_material)
