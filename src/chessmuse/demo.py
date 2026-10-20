"""Compile authored events into validated, seekable board snapshots."""

import json
from copy import deepcopy
from pathlib import Path

from chessmuse.game import announce_move, play
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
    fen, ply, branch, caption, last_move = snapshots[0], 0, None, None, None
    seen = set()
    for event in script["events"]:
        if event["id"] in seen:
            raise ValueError(f"Duplicate event ID: {event['id']}")
        seen.add(event["id"])
        match event["type"]:
            case "move":
                event["before_state"] = {
                    "fen": fen, "ply": ply, "branch": branch,
                    "caption": caption, "last_move": last_move,
                }
                previous_fen = fen
                fen, event["notation"] = play(fen, event["move"])
                text, event["gives_check"] = announce_move(previous_fen, event["move"], event["notation"])
                caption = f"move-{event['id']}"
                if caption in paragraphs:
                    raise ValueError(f"Reserved move paragraph ID: {caption}")
                paragraphs[caption] = {"id": caption, "title": event["notation"], "text": text, "kind": "move"}
                event["paragraph"] = caption
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
    ordered_ids = dict.fromkeys(e["paragraph"] for e in script["events"] if "paragraph" in e)
    ordered_ids.update(dict.fromkeys(paragraphs))
    script["paragraphs"] = [paragraphs[key] for key in ordered_ids]
    if require_audio:
        manifest = json.loads((DATA / "audio.json").read_text(encoding="utf-8-sig"))
        cached_text = {p["id"]: p["text"] for p in manifest["paragraphs"]}
        if cached_text != {p["id"]: p["text"] for p in paragraphs.values()}:
            raise ValueError("Audio text is stale; run chessmuse prepare-audio")
        script["voice"] = manifest["voice"]
        cached_files = {p["id"]: p.get("file", f"{p['id']}.wav") for p in manifest["paragraphs"]}
        for paragraph in paragraphs.values():
            filename = cached_files[paragraph["id"]]
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Audio file must be a simple filename")
            paragraph["duration"] = audio_duration(DATA / "audio" / filename)
            paragraph["audio"] = f"/audio/{filename}"
        for event in script["events"]:
            if event["type"] == "move":
                event["animation_ms"] = min(550, paragraphs[event["paragraph"]]["duration"] * 600)
    script["mainline_notations"] = notations
    script["schema_version"] = 2
    return script
