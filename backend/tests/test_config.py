from pathlib import Path

from app.core.config import REPO_ROOT, Settings


def test_relative_paths_are_anchored_to_repo_root():
    settings = Settings(
        database_url="sqlite+aiosqlite:///./data/test-openjm.db",
        upload_dir=Path("./data/test-uploads"),
        vector_path=Path("./data/test-vector"),
    )

    expected_db = (REPO_ROOT / "data" / "test-openjm.db").resolve().as_posix()

    assert settings.database_url == f"sqlite+aiosqlite:///{expected_db}"
    assert settings.upload_dir == (REPO_ROOT / "data" / "test-uploads").resolve()
    assert settings.vector_path == (REPO_ROOT / "data" / "test-vector").resolve()
