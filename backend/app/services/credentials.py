import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialVaultError(RuntimeError):
    pass


class CredentialVault:
    """Encrypts data-source credentials with a server-owned Fernet key."""

    def __init__(
        self,
        key: str | bytes | None = None,
        key_file: Path | None = None,
    ) -> None:
        settings = get_settings()
        configured_key = (
            settings.credential_encryption_key if key is None else key
        )
        self.key_file = key_file or settings.credential_key_file
        self._key = self._resolve_key(configured_key)

    def _resolve_key(self, configured_key: str | bytes | None) -> bytes:
        if configured_key:
            return (
                configured_key.encode("utf-8")
                if isinstance(configured_key, str)
                else configured_key
            )

        if self.key_file.exists():
            return self.key_file.read_bytes().strip()

        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        generated = Fernet.generate_key()
        self.key_file.write_bytes(generated + b"\n")
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            # Windows and some mounted filesystems may not honor POSIX modes.
            pass
        return generated

    @property
    def fernet(self) -> Fernet:
        try:
            return Fernet(self._key)
        except (ValueError, TypeError) as exc:
            raise CredentialVaultError(
                "OPENJM_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self.fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise CredentialVaultError("Unable to decrypt data-source credential") from exc


credential_vault = CredentialVault()
