import pytest

from chessmuse.demo import compile_demo, load_script
from chessmuse.game import announce_move, play


def test_branch_returns_to_exact_mainline_position():
    demo = compile_demo()
    events = {event["id"]: event for event in demo["events"]}
    anchor = events["m4"]["state"]
    returned = events["return-position"]["state"]
    assert returned["fen"] == anchor["fen"]
    assert returned["ply"] == 4
    assert returned["branch"] is None
    assert events["b2"]["state"]["fen"] != returned["fen"]
    assert demo["events"][-1]["state"]["ply"] == 8
    assert all(p["duration"] > 0 for p in demo["paragraphs"])


def test_every_move_has_a_rule_based_announcement_and_shorter_animation():
    demo = compile_demo()
    paragraphs = {p["id"]: p for p in demo["paragraphs"]}
    assert demo["schema_version"] == 2
    moves = [e for e in demo["events"] if e["type"] == "move"]
    assert len(moves) == 10
    for event in moves:
        paragraph = paragraphs[event["paragraph"]]
        assert paragraph["kind"] == "move"
        assert paragraph["text"].startswith(("红方", "黑方"))
        assert 0 < event["animation_ms"] < paragraph["duration"] * 1000
        after, _ = play(event["before_state"]["fen"], event["move"])
        assert after == event["state"]["fen"]
    assert paragraphs["move-m1"]["text"] == "红方，当头炮。"
    assert paragraphs["move-m2"]["text"] == "黑方，马八进七。"


def test_check_announcement_is_based_on_the_board():
    fen = "4k4/9/9/3N5/4P4/9/9/9/9/4K4 w"
    _, notation = play(fen, "d6f7")
    text, check = announce_move(fen, "d6f7", notation)
    assert check and "将军" in text
    _, notation = play(fen, "e5e6")
    text, check = announce_move(fen, "e5e6", notation)
    assert not check and "将军" not in text


@pytest.mark.parametrize("move", ["a0a9", "h9g7", "h0h2", "z9a0", "a0a0"])
def test_rejects_illegal_opening_moves(move):
    with pytest.raises(ValueError):
        play(load_script()["initial_fen"], move)


def test_rejects_exposing_facing_generals():
    with pytest.raises(ValueError):
        play("4k4/9/9/9/4R4/9/9/9/9/4K4 w", "e5d5")


def test_rejects_leaving_king_in_rook_check():
    with pytest.raises(ValueError):
        play("3k5/9/9/9/9/9/9/4r4/9/R3K4 w", "a0a1")


def test_compiler_rejects_unvalidated_timeline_move():
    script = load_script()
    script["events"][1]["move"] = "h9g7"
    with pytest.raises(ValueError):
        compile_demo(script, require_audio=False)


def test_compiler_rejects_legal_but_wrong_mainline():
    script = load_script()
    script["events"][1]["move"] = "b2e2"
    with pytest.raises(ValueError, match="disagrees"):
        compile_demo(script, require_audio=False)


def test_changed_commentary_requires_audio_regeneration():
    script = load_script()
    script["paragraphs"][0]["text"] += "新增的讲解。"
    with pytest.raises(ValueError, match="stale"):
        compile_demo(script)
