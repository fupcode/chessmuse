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
from mutagen import MutagenError

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+8%"


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


async def prepare_neural_audio(audio_dir: Path, paragraphs: list[dict],
                               voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE,
                               *, force: bool = False, progress=None):
    if not re.fullmatch(r"[A-Za-z0-9-]+", voice):
        raise ValueError("Invalid voice name")
    if not re.fullmatch(r"[+-]\d+%", rate):
        raise ValueError("Rate must be a signed percentage, e.g. -5%")
    records = []
    audio_dir.mkdir(parents=True, exist_ok=True)
    cached = {}
    if not force:
        try:
            old = json.loads((audio_dir / "audio.json").read_text(encoding="utf-8-sig"))
            if old.get("provider", "edge-tts") == "edge-tts" and old["voice"] == voice and old.get("rate") == rate:
                cached = {p["id"]: p for p in old["paragraphs"]}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    staging_root = Path(".cache") / "tts"
    staging_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="tts-", dir=staging_root) as temporary:
        staging = Path(temporary)
        for index, paragraph in enumerate(paragraphs):
            paragraph_id = paragraph["id"]
            if not re.fullmatch(r"[A-Za-z0-9-]+", paragraph_id):
                raise ValueError("Invalid paragraph ID")
            previous = cached.get(paragraph_id)
            reusable = False
            if previous and previous.get("text") == paragraph["text"]:
                filename = previous.get("file", "")
                if isinstance(filename, str) and filename and Path(filename).name == filename and "/" not in filename and "\\" not in filename:
                    try:
                        audio_duration(audio_dir / filename)
                        reusable = True
                    except (OSError, ValueError, wave.Error, MutagenError, EOFError):
                        # Invalid/missing audio is regenerated, never served silently.
                        reusable = False
            if reusable:
                records.append(previous)
                if progress:
                    progress((index + 1) / len(paragraphs), f"复用语音 {index + 1}/{len(paragraphs)}")
                continue
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
            if progress:
                progress((index + 1) / len(paragraphs), f"生成语音 {index + 1}/{len(paragraphs)}")
        manifest = {"provider": "edge-tts", "voice": voice, "rate": rate, "paragraphs": records}
        # Copy bytes into the destination directory so files inherit its access
        # permissions instead of the private temporary directory's permissions.
        for record in records:
            if (staging / record["file"]).exists():
                shutil.copyfile(staging / record["file"], audio_dir / record["file"])
        staged_manifest = audio_dir / f"audio-{staging.name}.json"
        try:
            staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staged_manifest.replace(audio_dir / "audio.json")
        finally:
            staged_manifest.unlink(missing_ok=True)
