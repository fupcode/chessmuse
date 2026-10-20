import asyncio

import pytest

from chessmuse.tts import prepare_neural_audio


def test_generation_failure_preserves_active_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "audio.json"
    manifest.write_text("original manifest", encoding="utf-8")
    cached = tmp_path / "original.mp3"
    cached.write_bytes(b"original audio")

    class FailedSpeech:
        def __init__(self, *args, **kwargs):
            pass

        async def save(self, path):
            raise ConnectionError("TTS service unavailable")

    monkeypatch.setattr("chessmuse.tts.edge_tts.Communicate", FailedSpeech)
    with pytest.raises(ConnectionError):
        asyncio.run(prepare_neural_audio(tmp_path, [{"id": "intro", "text": "你好"}]))
    assert manifest.read_text(encoding="utf-8") == "original manifest"
    assert cached.read_bytes() == b"original audio"
    assert list((tmp_path / ".cache" / "tts").iterdir()) == []


def test_valid_cached_audio_never_calls_tts_and_force_regenerates(tmp_path, monkeypatch):
    from conftest import write_audio_cache

    monkeypatch.chdir(tmp_path)
    paragraphs = [{"id": "intro", "text": "你好"}]
    folder = tmp_path / "audio" / "example"
    write_audio_cache(folder, paragraphs)
    original = (folder / "intro.wav").read_bytes()
    calls = []

    class Speech:
        def __init__(self, *args, **kwargs):
            calls.append(args)

        async def save(self, path):
            from pathlib import Path
            Path(path).write_bytes(b"generated audio")

    monkeypatch.setattr("chessmuse.tts.edge_tts.Communicate", Speech)
    monkeypatch.setattr("chessmuse.tts.audio_duration", lambda path: 1 if path.exists() else (_ for _ in ()).throw(FileNotFoundError()))
    asyncio.run(prepare_neural_audio(folder, paragraphs))
    assert not calls
    assert (folder / "intro.wav").read_bytes() == original
    asyncio.run(prepare_neural_audio(folder, paragraphs, force=True))
    assert len(calls) == 1


def test_only_missing_clip_is_regenerated(tmp_path, monkeypatch):
    from conftest import write_audio_cache
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    paragraphs = [{"id": "one", "text": "一句"}, {"id": "two", "text": "二句"}]
    folder = tmp_path / "audio" / "example"
    write_audio_cache(folder, paragraphs)
    (folder / "two.wav").unlink()
    calls = []

    class Speech:
        def __init__(self, text, *args, **kwargs):
            calls.append(text)

        async def save(self, path):
            Path(path).write_bytes(b"generated")

    monkeypatch.setattr("chessmuse.tts.edge_tts.Communicate", Speech)
    def duration(path):
        if not path.exists():
            raise FileNotFoundError()
        return 1
    monkeypatch.setattr("chessmuse.tts.audio_duration", duration)
    asyncio.run(prepare_neural_audio(folder, paragraphs))
    assert calls == ["二句"]
