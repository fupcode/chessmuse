"""Generate natural speech into a local cache; publish the manifest last."""

import asyncio
import json
import re
import shutil
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import edge_tts
from mutagen.mp3 import MP3

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+20%"


def audio_duration(path: Path) -> float:
    if path.suffix == ".wav":
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
    elif path.suffix == ".mp3":
        duration = MP3(path).info.length
    else:
        raise ValueError(f"Unsupported audio format: {path.suffix}")
    if duration <= 0:
        raise ValueError(f"Empty audio: {path.name}")
    return duration


async def prepare_neural_audio(data: Path, paragraphs: list[dict],
                               voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE):
    if not re.fullmatch(r"[A-Za-z0-9-]+", voice):
        raise ValueError("Invalid voice name")
    if not re.fullmatch(r"[+-]\d+%", rate):
        raise ValueError("Rate must be a signed percentage, e.g. -5%")
    records = []
    audio_dir = data / "audio"
    audio_dir.mkdir(exist_ok=True)
    staging_root = Path(".cache") / "tts"
    staging_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="tts-", dir=staging_root) as temporary:
        staging = Path(temporary)
        for paragraph in paragraphs:
            paragraph_id = paragraph["id"]
            if not re.fullmatch(r"[A-Za-z0-9-]+", paragraph_id):
                raise ValueError("Invalid paragraph ID")
            # Unique generation suffix prevents browser caches retaining old audio.
            filename = f"{paragraph_id}-{voice}-{staging.name}.mp3"
            target = staging / filename
            await asyncio.wait_for(
                edge_tts.Communicate(paragraph["text"], voice, rate=rate).save(str(target)),
                timeout=90,
            )
            audio_duration(target)
            records.append({"id": paragraph_id, "text": paragraph["text"], "file": filename})
            print(f"Generated: {paragraph_id}", flush=True)
        manifest = {"provider": "edge-tts", "voice": voice, "rate": rate, "paragraphs": records}
        # Copy bytes into the destination directory so files inherit its access
        # permissions instead of the private temporary directory's permissions.
        for record in records:
            shutil.copyfile(staging / record["file"], audio_dir / record["file"])
        staged_manifest = data / f"audio-{staging.name}.json"
        try:
            staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staged_manifest.replace(data / "audio.json")
        finally:
            staged_manifest.unlink(missing_ok=True)
