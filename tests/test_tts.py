import asyncio

import pytest

from chessmuse.tts import prepare_neural_audio


def test_generation_failure_preserves_active_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "audio.json"
    manifest.write_text("original manifest", encoding="utf-8")
    audio = tmp_path / "audio"
    audio.mkdir()
    cached = audio / "original.mp3"
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
