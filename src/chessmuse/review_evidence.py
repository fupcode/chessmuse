"""Game-independent key-moment selection and verified branch evidence."""

import json
from pathlib import Path

from cchess import ChessBoard

from chessmuse.analysis import analyze_game, cp_loss
from chessmuse.branches import branch_assessment
from chessmuse.demo import initial_pieces
from chessmuse.engine import Pikafish, position_after, verified_line
from chessmuse.game import move_facts, play
from chessmuse.intuition import EXECUTABLE, rank_candidates
from chessmuse.openings import recognize_opening

EVIDENCE_VERSION = 5
DEFAULT_ENGINE = Path(".cache/pikafish/Pikafish-Windows-x86-64-universal.exe")
VALUES = {"r": 600, "n": 270, "c": 285, "a": 120, "b": 120, "p": 30, "k": 0}
PIECES = {"r": "车", "n": "马", "b": "象", "a": "士", "k": "将", "c": "炮", "p": "卒",
          "R": "车", "N": "马", "B": "相", "A": "仕", "K": "帅", "C": "炮", "P": "兵"}


def game_input(script: dict) -> dict:
    return {k: script.get(k) for k in ("title", "initial_fen", "mainline", "source")}


def fact(fen: str, move: str) -> dict:
    after, action = play(fen, move)
    return move_facts(fen, move, action) | {"after_fen": after}


def material(fen: str) -> dict:
    counts = {"红方": {}, "黑方": {}}
    for p in initial_pieces(fen).values():
        side = "红方" if p["code"].isupper() else "黑方"
        name = PIECES[p["code"]]
        counts[side][name] = counts[side].get(name, 0) + 1
    return counts


def sacrifice_signal(fen: str, moves: list[str]) -> bool:
    """A moved piece actually lost in the following six plies; not a brilliance label."""
    if len(moves) < 2:
        return False
    board = ChessBoard(fen)
    first = moves[0]
    own = board.get_fench((ord(first[0])-97, int(first[1])))
    if VALUES[own.lower()] < 270:
        return False
    victim = board.get_fench((ord(first[2])-97, int(first[3])))
    if victim and VALUES[own.lower()] - VALUES[victim.lower()] < 100:
        return False  # Capturing an equal-valued piece is an exchange.
    target = first[2:]
    current = fen
    for i, move in enumerate(moves[:7]):
        if i and move[2:] == target and i % 2:
            capturing_piece = ChessBoard(current).get_fench((ord(move[0])-97, int(move[1])))
            reply = moves[i+1] if i+1 < len(moves) else None
            if reply and reply[2:] == target and VALUES[capturing_piece.lower()] >= VALUES[own.lower()] - 80:
                return False  # Immediate equal recapture, not a net sacrifice.
            return True
        current = play(current, move)[0]
        if i and move[:2] == target:
            target = move[2:]
    return False


def select_pool(scan: dict, moves: list[str]) -> list[int]:
    rows = []
    for row in scan["scan"]:
        ply = row["ply"]
        if ply <= 8 and len(moves) > 20:
            continue
        sacrifice = sacrifice_signal(row["fen"], moves[ply-1:ply+6])
        loss = max(0, row["loss_cp"] or 0)
        score = loss + (160 if sacrifice else 0)
        if row["loss_cp"] is None and row["best"]["bestmove"] != row["actual_move"]:
            score += 120
        rows.append((score, ply))
    ranked = sorted(rows, reverse=True)
    # Recheck candidates in short games too; selection may still find no notable moment.
    if not ranked:
        ranked = [(0, row["ply"]) for row in scan["scan"]]
    primary = [ply for _, ply in ranked[:8]]
    # Recheck the same player's preceding decision around a turning point.
    # This catches a retreat that surrendered the initiative before a later error.
    neighbors = [ply-2 for ply in primary if ply > 2]
    return list(dict.fromkeys(primary + neighbors))[:16]


def piece_annotations(fen: str, prefix: str) -> dict:
    result = {}
    for square, p in initial_pieces(fen).items():
        key = f"{prefix}-{square}"
        side = "红方" if p["code"].isupper() else "黑方"
        result[key] = {"id": key, "label": side + "一二三四五六七八九"[(8-p["file"]) if p["code"].isupper() else p["file"]] + "路" + PIECES[p["code"]] + "（" + square + "）",
                       "fen": fen, "rings": [square], "arrows": []}
    return result


def tactical_annotations(fen: str, prefix: str) -> dict:
    result = piece_annotations(fen, prefix)
    board = ChessBoard(fen)
    for start, end in board.create_moves():
        target = board.get_fench(end)
        if not target or board.is_checked_move(start, end):
            continue
        move = chr(97+start[0])+str(start[1])+chr(97+end[0])+str(end[1])
        key = f"{prefix}-attack-{move}"
        result[key] = {"id": key, "label": fact(fen, move)["action"] + "的攻击关系",
            "fen": fen, "rings": [move[:2], move[2:]],
            "arrows": [{"from": move[:2], "to": move[2:], "kind": "attack"}]}
    return result


def quiet_branch(fen: str, pv: list[str]) -> list[str]:
    events = verified_line(fen, {"pv": pv[:12]})["events"]
    for length in range(min(6, len(events)), len(events)+1):
        if all(not e["capture"] and not e["gives_check"] for e in events[max(0,length-2):length]):
            return pv[:length]
    quiet = [i+1 for i, e in enumerate(events) if i >= 5 and not e["gives_check"]]
    return pv[:quiet[-1]] if quiet else pv[:len(events)]


def protection_evidence(fen: str, first: str) -> list[dict]:
    after = play(fen, first)[0]
    target = first[2:]
    board = ChessBoard(after)
    proofs = []
    for start, end in board.create_moves():
        if chr(97+end[0])+str(end[1]) != target or board.is_checked_move(start,end):
            continue
        capture = chr(97+start[0])+str(start[1])+target
        next_fen = play(after, capture)[0]
        reply_board = ChessBoard(next_fen)
        for source, destination in reply_board.create_moves():
            if destination != end or reply_board.is_checked_move(source,destination):
                continue
            reply = chr(97+source[0])+str(source[1])+target
            proofs.append({"capture": fact(after,capture), "recapture": fact(next_fen,reply)})
            if len(proofs) >= 3:
                return proofs
    return proofs


def capture_reply_evidence(fen: str) -> list[dict]:
    """Distinguish attacking a piece from winning it: include legal recaptures."""
    board = ChessBoard(fen)
    result = []
    for start, end in board.create_moves():
        if not board.get_fench(end) or board.is_checked_move(start, end):
            continue
        move = chr(97+start[0])+str(start[1])+chr(97+end[0])+str(end[1])
        capture = fact(fen, move)
        after = ChessBoard(capture["after_fen"])
        replies = []
        for source, destination in after.create_moves():
            if destination == end and not after.is_checked_move(source, destination):
                reply = chr(97+source[0])+str(source[1])+move[2:]
                replies.append(fact(capture["after_fen"], reply))
        result.append({"capture": capture, "recaptures": replies})
    return result



def select_nodes(candidates: list[dict], opening_plies: int = 0) -> list[dict]:
    selected = []
    for row in sorted(candidates, key=lambda n: n["importance"], reverse=True):
        if row["importance"] < 100 or row["ply"] <= opening_plies or row.get("merged_into"):
            continue
        if any(abs(row["ply"]-n["ply"]) < 2 for n in selected):
            continue
        if len(selected) >= 3 and row["importance"] < 300:
            continue
        selected.append(row)
        if len(selected) == 4:
            break
    return selected

def build_review_evidence(engine, script: dict, *, progress=lambda x: None) -> dict:
    initial, moves = script["initial_fen"], script["mainline"]
    scan = analyze_game(engine, initial, moves, scan_depth=8, review_depth=18, max_reviews=0,
                        progress=lambda x: progress("全局扫描 · " + x))
    candidates = []
    for ply in select_pool(scan, moves):
        progress(f"复查第{(ply+1)//2}回合的攻防选择")
        history = moves[:ply-1]
        best = engine.search(initial, history, depth=20, multipv=2)
        actual = engine.search(initial, history, depth=20, searchmoves=[moves[ply-1]])
        loss = cp_loss(best, actual)
        sacrifice = sacrifice_signal(scan["snapshots"][ply-1], moves[ply-1:ply+6])
        importance = max(0, loss or 0) + (180 if sacrifice and (loss is None or loss < 180) else 0)
        best_score, actual_score = best["lines"][0]["score"], actual["lines"][0]["score"]
        if all(s["kind"] == "cp" and s["bound"] == "exact" for s in (best_score, actual_score)):
            if best_score["value"] > 150 and actual_score["value"] < 100:
                importance += 120  # Letting a real advantage disappear matters.
            if abs(best_score["value"]) < 100 and actual_score["value"] < -150:
                importance += 100
            if best_score["value"] < -200:
                importance *= .35  # Do not repeatedly lecture the already losing side.
        if loss is None and best["bestmove"] != moves[ply-1]:
            importance += 180
        candidates.append({"ply": ply, "best": best, "actual": actual,
                           "loss_cp": loss, "sacrifice_candidate": sacrifice, "importance": importance})
    # A sacrifice and the opponent's immediate defensive choice form one story.
    by_ply = {n["ply"]: n for n in candidates}
    for row in candidates:
        reply = by_ply.get(row["ply"] + 1)
        if row["sacrifice_candidate"] and reply and (reply["loss_cp"] or 0) > 150:
            row["reply_review"] = reply
            reply["merged_into"] = row["ply"]
            row["importance"] = max(row["importance"], reply["importance"]) + 40
    opening = recognize_opening(moves)
    opening_plies = len(opening["moves"]) if opening else 0
    selected = select_nodes(candidates, opening_plies)
    nodes = []
    for row in sorted(selected, key=lambda n: n["ply"]):
        ply = row["ply"]
        fen = scan["snapshots"][ply-1]
        actual_fact = fact(fen, moves[ply-1])
        # Keep a human-like material candidate as an independent, inspectable comparison.
        models = []
        if EXECUTABLE.exists():
            models = [rank_candidates(fen, model=name, depth=3) for name in ("material", "traditional")]
        branch_ply = ply - 1
        root_fen = fen
        principal = row["best"]
        if row.get("reply_review"):
            branch_ply = ply
            root_fen = actual_fact["after_fen"]
            principal = row["reply_review"]["best"]
        alternatives = [principal["bestmove"]] + [m["candidates"][0]["move"] for m in models]
        actual_at_root = moves[branch_ply] if branch_ply < len(moves) else None
        choice = next((m for m in alternatives if m and m != actual_at_root), None)
        if choice:
            branch = engine.search(initial, moves[:branch_ply], depth=20, searchmoves=[choice])
            line = quiet_branch(root_fen, branch["lines"][0]["pv"])
        else:
            # A genuinely forced position needs no fabricated alternative.
            branch, line = row["actual"], row["actual"]["lines"][0]["pv"][:6]
        proof = verified_line(root_fen, {"pv": line})
        endpoint = engine.search(initial, moves[:branch_ply] + line, depth=20)
        context = [fact(scan["snapshots"][i], moves[i]) for i in range(max(0,ply-5), min(len(moves),ply+3))]
        nodes.append({**row, "fen": fen, "actual_fact": actual_fact, "models": models,
            "branch": {"root_ply": branch_ply, "fen": root_fen, "moves": line, "proof": proof, "search": branch, "endpoint": endpoint,
                       "assessment": branch_assessment(endpoint), "protection": protection_evidence(root_fen,line[0]),
                       "is_alternative": choice is not None},
            "actual_line": verified_line(fen, {"pv": row["actual"]["lines"][0]["pv"][:8]})["events"],
            "context": context, "post_material": material(actual_fact["after_fen"]),
            "branch_material": material(proof["events"][-1]["after_fen"])})
    return {"version": EVIDENCE_VERSION, "input": game_input(script), "engine": engine.identity,
            "scan": scan, "nodes": nodes, "opening": recognize_opening(moves)}


def load_or_build_evidence(script: dict, directory: Path, engine_path: Path, progress) -> dict:
    path = directory / "evidence.json"
    if not engine_path.exists():
        raise ValueError("缺少复盘引擎，请将 Pikafish 与 pikafish.nnue 放到 .cache/pikafish/，或设置 CHESSMUSE_ENGINE")
    with Pikafish(engine_path) as engine:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (saved.get("version") == EVIDENCE_VERSION and saved.get("input") == game_input(script)
                    and saved.get("engine") == engine.identity):
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
        evidence = build_review_evidence(engine, script, progress=progress)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence
