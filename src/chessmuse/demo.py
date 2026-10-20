"""Compile authored events into validated, seekable board snapshots."""

import json
import re
from copy import deepcopy
from pathlib import Path

from chessmuse.game import move_facts, play
from chessmuse.narration import read_announcements
from chessmuse.tts import audio_duration

DATA = Path(__file__).parent / "data"
AUDIO_ROOT = Path("audio")


def default_narration_path() -> Path:
    local = Path(".cache/games/opening-v1/narration.json")
    return local if local.exists() else DATA / "narration.json"


def load_script() -> dict:
    return json.loads((DATA / "demo.json").read_text(encoding="utf-8"))


def compile_demo(script: dict | None = None, *, require_audio: bool = True, require_narration: bool = True,
                 narration_path: Path | None = None, audio_dir: Path | None = None) -> dict:
    if require_audio and not require_narration:
        raise ValueError("Playable audio requires LLM narration")
    script = deepcopy(script if script is not None else load_script())
    if not isinstance(script.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9-]+", script["id"]):
        raise ValueError("Invalid game ID")
    audio_dir = audio_dir if audio_dir is not None else AUDIO_ROOT / script["id"]
    snapshots = [script["initial_fen"]]
    notations = []
    mainline_history = []
    for move in script["mainline"]:
        fen, notation = play(snapshots[-1], move)
        facts = move_facts(snapshots[-1], move, notation)
        mainline_history.append({"side": facts["side"], "action": facts["action"], "gives_check": facts["gives_check"]})
        snapshots.append(fen)
        notations.append(notation)

    paragraphs = {p["id"]: p for p in script["paragraphs"]}
    if len(paragraphs) != len(script["paragraphs"]):
        raise ValueError("Duplicate paragraph IDs")
    fen, ply, branch, caption, last_move = snapshots[0], 0, None, None, None
    seen = set()
    history = []
    narration_inputs = []
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
                facts = move_facts(previous_fen, event["move"], event["notation"])
                event["gives_check"] = facts["gives_check"]
                narration_inputs.append({"id": event["id"], "facts": facts, "history": history[-6:], "branch": branch})
                history.append({"side": facts["side"], "action": facts["action"], "gives_check": facts["gives_check"]})
                caption = f"move-{event['id']}"
                if caption in paragraphs:
                    raise ValueError(f"Reserved move paragraph ID: {caption}")
                paragraphs[caption] = {"id": caption, "title": event["notation"], "text": "", "kind": "move"}
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
                history = mainline_history[:ply]
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
    script["narration_inputs"] = narration_inputs
    if require_narration:
        announcements = read_announcements(narration_path if narration_path is not None else default_narration_path(), narration_inputs)
        for event in script["events"]:
            if event["type"] == "move":
                paragraphs[event["paragraph"]]["text"] = announcements[event["id"]]
    ordered_ids = dict.fromkeys(e["paragraph"] for e in script["events"] if "paragraph" in e)
    ordered_ids.update(dict.fromkeys(paragraphs))
    script["paragraphs"] = [paragraphs[key] for key in ordered_ids]
    if require_audio:
        manifest = json.loads((audio_dir / "audio.json").read_text(encoding="utf-8-sig"))
        cached_text = {p["id"]: p["text"] for p in manifest["paragraphs"]}
        if cached_text != {p["id"]: p["text"] for p in paragraphs.values()}:
            raise ValueError("Audio text is stale; run chessmuse prepare-audio")
        script["voice"] = manifest["voice"]
        cached_files = {p["id"]: p.get("file", f"{p['id']}.wav") for p in manifest["paragraphs"]}
        for paragraph in paragraphs.values():
            filename = cached_files[paragraph["id"]]
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Audio file must be a simple filename")
            paragraph["duration"] = audio_duration(audio_dir / filename)
            paragraph["audio"] = f"/audio/{script['id']}/{filename}"
        for event in script["events"]:
            if event["type"] == "move":
                event["animation_ms"] = min(550, paragraphs[event["paragraph"]]["duration"] * 600)
    script["mainline_notations"] = notations
    script["schema_version"] = 2
    return script
