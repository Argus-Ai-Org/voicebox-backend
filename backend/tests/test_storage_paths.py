"""Storage path round-trips when the data dir itself lives under /data.

``to_storage_path`` used to strip at the first path component named ``data``.
On Linux that matches the FHS mount ``/data``, so a data dir of
``/data/storage/voicebox`` stored ``storage/voicebox/generations/{id}.wav``
and ``GET /audio/{id}`` looked in a nested path that does not exist.

Usage:
    python -m pytest backend/tests/test_storage_paths.py -v
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import config
from backend.database import Base, Generation, VoiceProfile, get_db
from backend.routes.audio import router as audio_router


@pytest.fixture
def fhs_data_dir(tmp_path, monkeypatch):
    """``--data-dir /data/storage/voicebox``: a data dir nested under a folder named data."""
    data_dir = (tmp_path / "data" / "storage" / "voicebox").resolve()
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "_data_dir", data_dir)
    return data_dir


@pytest.fixture
def named_data_dir(tmp_path, monkeypatch):
    """Docker / default layout: the data dir folder itself is named ``data``."""
    data_dir = (tmp_path / "app" / "data").resolve()
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "_data_dir", data_dir)
    return data_dir


def _touch_generation(data_dir: Path, name: str = "gen-1.wav") -> Path:
    audio = data_dir / "generations" / name
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"RIFF")
    return audio


def test_to_storage_path_does_not_strip_fhs_data_prefix(fhs_data_dir):
    audio = _touch_generation(fhs_data_dir)
    assert config.to_storage_path(audio) == f"generations/{audio.name}"


def test_resolve_roundtrip_under_fhs_data_prefix(fhs_data_dir):
    audio = _touch_generation(fhs_data_dir)
    stored = config.to_storage_path(audio)
    resolved = config.resolve_storage_path(stored)
    assert resolved == audio
    assert resolved.is_file()


def test_resolve_poisoned_relative_path_under_fhs_data_prefix(fhs_data_dir):
    """Rows written by the old strip-at-/data heuristic must still resolve."""
    audio = _touch_generation(fhs_data_dir)
    poisoned = f"storage/voicebox/generations/{audio.name}"
    resolved = config.resolve_storage_path(poisoned)
    assert resolved == audio
    assert resolved.is_file()


def test_to_storage_path_when_data_dir_is_named_data(named_data_dir):
    audio = _touch_generation(named_data_dir)
    assert config.to_storage_path(audio) == f"generations/{audio.name}"


def test_resolve_legacy_data_prefix(named_data_dir):
    """0.3.0 sometimes stored ``data/generations/...`` as the relative path."""
    audio = _touch_generation(named_data_dir)
    resolved = config.resolve_storage_path(f"data/generations/{audio.name}")
    assert resolved == audio


def test_resolve_empty_returns_none(fhs_data_dir):
    assert config.resolve_storage_path("") is None
    assert config.resolve_storage_path(None) is None


def test_audio_route_serves_file_for_poisoned_path(fhs_data_dir):
    audio = _touch_generation(fhs_data_dir, "gen-poisoned.wav")
    engine = create_engine(
        f"sqlite:///{fhs_data_dir / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    session.add(VoiceProfile(id="profile-1", name="Test"))
    session.add(
        Generation(
            id="gen-poisoned",
            profile_id="profile-1",
            text="hello",
            audio_path=f"storage/voicebox/generations/{audio.name}",
            status="completed",
        )
    )
    session.commit()
    session.close()

    app = FastAPI()
    app.include_router(audio_router)

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    response = client.get("/audio/gen-poisoned")
    assert response.status_code == 200
    assert response.content == b"RIFF"
