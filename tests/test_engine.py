"""Exercise real subprocess pipes offline; integration runs when Pikafish is present."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cchess import FULL_INIT_FEN

from chessmuse.analysis import analyze_game, cp_loss
from chessmuse.engine import EngineError, Pikafish, parse_info, validate_fen


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text('''
import sys
from cchess import ChessBoard
from chessmuse.game import play
fen = None
candidate = None
for raw in sys.stdin:
    s = raw.strip()
    if s == "uci":
        print("id name Pikafish fake", flush=True)
        print("uciok", flush=True)
    elif s == "isready": print("readyok", flush=True)
    elif s.startswith("position fen "):
        source = s.removeprefix("position fen ").split(" moves ")
        fen = " ".join(source[0].split()[:2])
        if len(source) > 1:
            for m in source[1].split(): fen = play(fen, m)[0]
    elif s.startswith("go "):
        if "searchmoves" in s: candidate = s.split("searchmoves ")[1].split()[0]
        else:
            candidate = None
            for a, b in ChessBoard(fen).create_moves():
                m = chr(97+a[0])+str(a[1])+chr(97+b[0])+str(b[1])
                try: play(fen, m)
                except ValueError: continue
                candidate = m
                break
        depth = s.split()[2]
        print(f"info depth {depth} score cp 20 pv {candidate}", flush=True)
        print(f"bestmove {candidate}", flush=True)
    elif s == "quit": break
''', encoding="utf-8")
    net = tmp_path / "pikafish.nnue"
    net.write_text("fake weights", encoding="utf-8")
    original = subprocess.Popen
    def launch(argv, **kwargs):
        return original([sys.executable, str(script)], **kwargs)
    monkeypatch.setattr("chessmuse.engine.subprocess.Popen", launch)
    return script, net, tmp_path / "cache"


def test_pipe_search_candidate_history_and_cache(fake_engine):
    exe, net, cache = fake_engine
    with Pikafish(exe, eval_file=net, cache_dir=cache) as engine:
        first = engine.search(FULL_INIT_FEN, ["h2e2"], searchmoves=["h9g7"], depth=3)
        assert first["bestmove"] == "h9g7"
        assert first["lines"][0]["events"][0]["side"] == "黑方"
        assert first["score_pov"] == "side_to_move"
        commands = [e["line"] for e in first["transcript"] if e["direction"] == "send"]
        assert commands[-1] == "go depth 3 searchmoves h9g7"
        assert commands[-2].endswith(" moves h2e2")
        assert not first["cache_hit"]
        old_transcript = list(engine.transcript)
        second = engine.search(FULL_INIT_FEN, ["h2e2"], searchmoves=["h9g7"], depth=3)
        assert second["cache_hit"]
        assert engine.transcript == old_transcript  # no second search sent
        assert not engine.search(FULL_INIT_FEN, ["h2e2"], searchmoves=["h9g7"], depth=4)["cache_hit"]
        assert not engine.search(FULL_INIT_FEN, ["h2e2"], searchmoves=["h9g7"], depth=3, force=True)["cache_hit"]
    assert engine.process.poll() is not None


def test_corrupt_cache_is_recomputed(fake_engine):
    exe, net, cache = fake_engine
    with Pikafish(exe, eval_file=net, cache_dir=cache) as engine:
        first = engine.search(FULL_INIT_FEN, depth=3)
        path = Path(first["cache_file"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["transcript"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        assert not engine.search(FULL_INIT_FEN, depth=3)["cache_hit"]


def test_invalid_candidate_and_game_fail_before_search(fake_engine):
    exe, net, cache = fake_engine
    with Pikafish(exe, eval_file=net, cache_dir=cache) as engine:
        before = list(engine.transcript)
        with pytest.raises(ValueError):
            engine.search(FULL_INIT_FEN, searchmoves=["a0a9"])
        with pytest.raises(ValueError):
            analyze_game(engine, FULL_INIT_FEN, ["h2e2", "a9a0"])
        assert engine.transcript == before


def test_timeout_reaps_child(fake_engine):
    exe, net, cache = fake_engine
    exe.write_text('import sys\nfor s in sys.stdin:\n    if s.strip() == "quit": break\n', encoding="utf-8")
    engine = Pikafish(exe, eval_file=net, cache_dir=cache, timeout=0.1)
    with pytest.raises(EngineError, match="超时"):
        with engine:
            pass
    assert engine.process.poll() is not None


@pytest.mark.parametrize("fen", ["9/9 w", FULL_INIT_FEN.replace(" w", " x"),
    FULL_INIT_FEN + "\ngo depth 99", FULL_INIT_FEN.replace("K", "R")])
def test_bad_fen_rejected(fen):
    with pytest.raises(ValueError):
        validate_fen(fen)


def test_mate_and_bound_scores_are_never_cp_losses():
    def result(score):
        return {"lines": [{"score": score}]}
    exact = {"kind": "cp", "value": 50, "bound": "exact"}
    for suffix, kind, bound in [("mate -2", "mate", "exact"), ("cp 50 lowerbound", "cp", "lower"),
                                 ("cp 50 upperbound", "cp", "upper")]:
        info = parse_info(f"info depth 8 multipv 2 score {suffix} nodes 100 pv h2e2")
        assert info["score"]["kind"] == kind
        assert info["score"]["bound"] == bound
        assert cp_loss(result(exact), result(info["score"])) is None
    assert cp_loss(result(exact), result({**exact, "value": 80})) == -30
    assert parse_info("info string depth 8 score cp 12 pv h2e2") is None


def test_scan_preserves_roots_and_explicit_review(fake_engine):
    exe, net, cache = fake_engine
    with Pikafish(exe, eval_file=net, cache_dir=cache) as engine:
        result = analyze_game(engine, FULL_INIT_FEN, ["h2e2", "h9g7"], scan_depth=2,
                              review_depth=3, max_reviews=1, review_plies=[2])
    assert len(result["snapshots"]) == 3
    assert result["reviews"][0]["ply"] == 2
    for row in result["scan"]:
        assert row["actual"]["fen"] == row["best"]["fen"] == row["fen"]
        assert row["actual"]["lines"][0]["pv"][0] == row["actual_move"]
    assert result["final"]["fen"] == result["snapshots"][-1]
    assert cp_loss(result["scan"][0]["best"], result["scan"][1]["actual"]) is None


def test_unverified_pv_and_unfinished_depth_are_rejected(fake_engine):
    exe, net, cache = fake_engine
    with Pikafish(exe, eval_file=net, cache_dir=cache) as engine:
        request = {"depth": 3, "searchmoves": []}
        for info, message in [
            ("info depth 3 score cp 0 pv h2e2 a9a0", "非法PV"),
            ("info depth 2 score cp 0 pv h2e2", "深度"),
        ]:
            transcript = [{"direction": "receive", "line": line} for line in [info, "bestmove h2e2"]]
            with pytest.raises(EngineError, match=message):
                engine._result(FULL_INIT_FEN, request, transcript)
        request["searchmoves"] = ["b2e2"]
        with pytest.raises(EngineError, match="指定候选"):
            engine._result(FULL_INIT_FEN, request, [
                {"direction": "receive", "line": "bestmove h2e2"}])


def test_real_pikafish_rook_blunder_and_repeatability(tmp_path):
    executable = Path(".cache/pikafish/Pikafish-Windows-x86-64-universal.exe")
    if not executable.exists():
        pytest.skip("真实引擎未安装；离线协议测试仍运行")
    fen = "r3k4/9/9/9/4p4/9/9/9/9/R3K4 w"
    with Pikafish(executable, cache_dir=tmp_path / "real") as engine:
        best = engine.search(fen, depth=6)
        actual = engine.search(fen, depth=6, searchmoves=["a0a8"])
        repeated = engine.search(fen, depth=6, force=True)
        assert best["bestmove"] == repeated["bestmove"] == "a0a9"
        assert best["lines"][0]["score"] == repeated["lines"][0]["score"]
        assert cp_loss(best, actual) > 500
        assert actual["lines"][0]["pv"][:2] == ["a0a8", "a9a8"]
        terminal = engine.search("4k4/9/9/9/4p4/9/9/9/3r1r3/4K4 w", depth=6)
        assert terminal["terminal"] and terminal["bestmove"] is None and terminal["lines"] == []
