"""Compile authored events into validated, seekable board snapshots."""

import json
import re
from copy import deepcopy
from pathlib import Path

from chessmuse.annotations import validate_annotation
from chessmuse.game import move_facts, play
from chessmuse.narration import read_announcements
from chessmuse.tts import audio_duration, speech_text
from chessmuse.urls import BASE_URL

DATA = Path(__file__).parent / "data"
AUDIO_ROOT = Path("audio")


def default_narration_path() -> Path:
    local = Path(".cache/games/opening-v1/narration.json")
    return local if local.exists() else DATA / "narration.json"


def load_script() -> dict:
    return json.loads((DATA / "demo.json").read_text(encoding="utf-8"))


def initial_pieces(fen: str) -> dict:
    pieces = {}
    for rank, row in enumerate(fen.split()[0].split("/")):
        file = 0
        for code in row:
            if code.isdigit():
                file += int(code)
            else:
                square = chr(97 + file) + str(9 - rank)
                pieces[square] = {"id": square, "code": code, "file": file, "rank": rank}
                file += 1
    return pieces


def move_pieces(pieces: dict, move: str):
    moving = pieces.pop(move[:2])
    pieces[move[2:]] = {**moving, "file": ord(move[2]) - 97, "rank": 9 - int(move[3])}


def compile_demo(script: dict | None = None, *, require_audio: bool = True, require_narration: bool = True,
                 narration_path: Path | None = None, audio_dir: Path | None = None) -> dict:
    if require_audio and not require_narration:
        raise ValueError("Playable audio requires LLM narration")
    script = deepcopy(script if script is not None else load_script())
    if not isinstance(script.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9-]+", script["id"]):
        raise ValueError("Invalid game ID")
    audio_dir = audio_dir if audio_dir is not None else AUDIO_ROOT / script["id"]
    snapshots = [" ".join(script["initial_fen"].split()[:2])]
    positions = initial_pieces(script["initial_fen"])
    piece_snapshots = [deepcopy(positions)]
    notations = []
    mainline_history = []
    for move in script["mainline"]:
        fen, notation = play(snapshots[-1], move)
        facts = move_facts(snapshots[-1], move, notation)
        mainline_history.append({"side": facts["side"], "action": facts["action"], "gives_check": facts["gives_check"]})
        snapshots.append(fen)
        move_pieces(positions, move)
        piece_snapshots.append(deepcopy(positions))
        notations.append(notation)

    paragraphs = {p["id"]: p for p in script["paragraphs"]}
    if len(paragraphs) != len(script["paragraphs"]):
        raise ValueError("Duplicate paragraph IDs")
    fen, ply, branch, caption, last_move = snapshots[0], 0, None, None, None
    positions = deepcopy(piece_snapshots[0])
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
                    "caption": caption, "last_move": last_move, "pieces": list(positions.values()),
                }
                previous_fen = fen
                fen, event["notation"] = play(fen, event["move"])
                move_pieces(positions, event["move"])
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
            case "move_group":
                moves = event.get("moves")
                if branch or not isinstance(moves, list) or not 2 <= len(moves) <= 8:
                    raise ValueError("开局概括只能合并2到8个主线行动")
                caption = event.get("paragraph")
                if caption not in paragraphs:
                    raise ValueError("开局概括缺少讲解短句")
                event["before_state"] = {"fen": fen, "ply": ply, "branch": branch,
                    "caption": caption, "last_move": last_move, "pieces": list(positions.values())}
                event["frames"] = []
                for move in moves:
                    previous_fen = fen
                    fen, notation = play(fen, move)
                    facts = move_facts(previous_fen, move, notation)
                    history.append({"side": facts["side"], "action": facts["action"], "gives_check": facts["gives_check"]})
                    move_pieces(positions, move)
                    ply += 1
                    last_move = move
                    if ply >= len(snapshots) or fen != snapshots[ply]:
                        raise ValueError("合并报步与主线不一致")
                    event["frames"].append({"move": move, "notation": notation, "state": {
                        "fen": fen, "ply": ply, "branch": None, "caption": caption,
                        "last_move": move, "pieces": list(positions.values())}})
                event["notation"] = paragraphs[caption]["title"]
                event["animation_ms"] = 350
            case "set_position":
                event["before_state"] = {"fen": fen, "ply": ply, "branch": branch,
                    "caption": caption, "last_move": last_move, "pieces": list(positions.values())}
                event["animation_ms"] = 650
                caption = None
                ply = event["mainline_ply"]
                if not 0 <= ply < len(snapshots):
                    raise ValueError("Snapshot index outside mainline")
                fen, branch, last_move = snapshots[ply], event.get("branch"), None
                history = mainline_history[:ply]
                positions = deepcopy(piece_snapshots[ply])
            case "speak":
                caption = event["paragraph"]
                if caption not in paragraphs:
                    raise ValueError(f"Unknown paragraph: {caption}")
                validate_annotation(paragraphs[caption].get("annotation"), fen)
            case _:
                raise ValueError(f"Unknown event type: {event['type']}")
        event["state"] = {
            "fen": fen, "ply": ply, "branch": branch,
            "caption": caption, "last_move": last_move, "pieces": list(positions.values()),
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
        if any(p.get("spoken_text", p["text"]) != speech_text(p["text"]) for p in manifest["paragraphs"]):
            raise ValueError("Audio pronunciation is stale; run chessmuse prepare-audio")
        script["voice"] = manifest["voice"]
        cached_files = {p["id"]: p.get("file", f"{p['id']}.wav") for p in manifest["paragraphs"]}
        for paragraph in paragraphs.values():
            filename = cached_files[paragraph["id"]]
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Audio file must be a simple filename")
            paragraph["duration"] = audio_duration(audio_dir / filename)
            paragraph["audio"] = f"{BASE_URL}audio/{script['id']}/{filename}"
        for event in script["events"]:
            if event["type"] == "move_group":
                event["animation_ms"] = min(420, paragraphs[event["paragraph"]]["duration"] * 650 / len(event["frames"]))
            if event["type"] == "move":
                event["animation_ms"] = min(550, paragraphs[event["paragraph"]]["duration"] * 600)
    script["mainline_notations"] = notations
    script["schema_version"] = 2
    return script
