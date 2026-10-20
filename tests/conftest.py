"""Audio tests use tiny generated WAVs, never downloaded or versioned speech."""

import json
import wave

import pytest

from chessmuse.demo import compile_demo
from chessmuse.tts import speech_text


def write_audio_cache(folder, paragraphs, *, voice="zh-CN-XiaoxiaoNeural", rate="+8%"):
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for paragraph in paragraphs:
        name = paragraph["id"] + ".wav"
        with wave.open(str(folder / name), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 8000)
        records.append({"id": paragraph["id"], "text": paragraph["text"], "spoken_text": speech_text(paragraph["text"]), "file": name})
    (folder / "audio.json").write_text(json.dumps({
        "provider": "edge-tts", "voice": voice, "rate": rate, "paragraphs": records,
    }), encoding="utf-8")


@pytest.fixture
def demo_audio(tmp_path, monkeypatch):
    plan = compile_demo(require_audio=False)
    write_audio_cache(tmp_path / "audio" / "opening-v1", plan["paragraphs"])
    monkeypatch.setattr("chessmuse.demo.AUDIO_ROOT", tmp_path / "audio")
    return tmp_path
