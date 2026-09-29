from cryptography.fernet import Fernet

from app.services.credentials import CredentialVault


def test_credential_vault_encrypts_and_decrypts_without_plaintext(tmp_path):
    key = Fernet.generate_key()
    vault = CredentialVault(key=key, key_file=tmp_path / "unused.key")
    secret = "postgresql+asyncpg://openjm:super-secret@localhost/client"

    encrypted = vault.encrypt(secret)

    assert secret not in encrypted
    assert vault.decrypt(encrypted) == secret


def test_credential_vault_creates_local_key_file(tmp_path):
    key_file = tmp_path / "credentials.key"
    vault = CredentialVault(key="", key_file=key_file)

    encrypted = vault.encrypt("sqlite+aiosqlite:///tmp/example.db")

    assert key_file.exists()
    assert vault.decrypt(encrypted) == "sqlite+aiosqlite:///tmp/example.db"
