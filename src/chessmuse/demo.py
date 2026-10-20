"""Compile authored events into validated, seekable board snapshots."""

import json
from copy import deepcopy
from pathlib import Path

from chessmuse.game import play
from chessmuse.tts import audio_duration

DATA = Path(__file__).parent / "data"


def load_script() -> dict:
    return json.loads((DATA / "demo.json").read_text(encoding="utf-8"))


def compile_demo(script: dict | None = None, *, require_audio: bool = True) -> dict:
    script = deepcopy(script if script is not None else load_script())
    snapshots = [script["initial_fen"]]
    notations = []
    for move in script["mainline"]:
        fen, notation = play(snapshots[-1], move)
        snapshots.append(fen)
        notations.append(notation)

    paragraphs = {p["id"]: p for p in script["paragraphs"]}
    if len(paragraphs) != len(script["paragraphs"]):
        raise ValueError("Duplicate paragraph IDs")
    if require_audio:
        manifest = json.loads((DATA / "audio.json").read_text(encoding="utf-8-sig"))
        cached_text = {p["id"]: p["text"] for p in manifest["paragraphs"]}
        if cached_text != {p["id"]: p["text"] for p in paragraphs.values()}:
            raise ValueError("Audio text is stale; run chessmuse prepare-audio")
        script["voice"] = manifest["voice"]
        cached_files = {p["id"]: p.get("file", f"{p['id']}.wav") for p in manifest["paragraphs"]}
    for paragraph in paragraphs.values():
        if require_audio:
            filename = cached_files[paragraph["id"]]
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Audio file must be a simple filename")
            paragraph["duration"] = audio_duration(DATA / "audio" / filename)
            paragraph["audio"] = f"/audio/{filename}"

    fen, ply, branch, caption, last_move = snapshots[0], 0, None, None, None
    seen = set()
    for event in script["events"]:
        if event["id"] in seen:
            raise ValueError(f"Duplicate event ID: {event['id']}")
        seen.add(event["id"])
        match event["type"]:
            case "move":
                fen, event["notation"] = play(fen, event["move"])
                ply += 1
                last_move = event["move"]
                if branch is None and (ply >= len(snapshots) or fen != snapshots[ply]):
                    raise ValueError("Mainline event disagrees with the authored game")
            case "set_position":
                ply = event["mainline_ply"]
                if not 0 <= ply < len(snapshots):
                    raise ValueError("Snapshot index outside mainline")
                fen, branch, last_move = snapshots[ply], event.get("branch"), None
            case "speak":
                caption = event["paragraph"]
                if caption not in paragraphs:
                    raise ValueError(f"Unknown paragraph: {caption}")
            case _:
                raise ValueError(f"Unknown event type: {event['type']}")
        event["state"] = {
            "fen": fen, "ply": ply, "branch": branch,
            "caption": caption, "last_move": last_move,
        }
    if branch is not None or fen != snapshots[-1]:
        raise ValueError("Timeline must finish at the end of the mainline")
    script["mainline_notations"] = notations
    script["schema_version"] = 1
    return script
