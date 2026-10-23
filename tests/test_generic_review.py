import json
from copy import deepcopy

import pytest

from chessmuse.demo import DATA, compile_demo
from chessmuse.engine import position_after
from chessmuse.library import DEFAULT_EXAMPLE, GameLibrary, make_game
from chessmuse.openings import recognize_opening
from chessmuse.review_evidence import sacrifice_signal


def test_primary_example_and_downloadable_source_are_the_verified_modern_game():
    source = json.loads((DATA / "modern-game.json").read_text(encoding="utf-8"))
    library = GameLibrary()
    assert next(iter(library.builtins)) == DEFAULT_EXAMPLE
    assert library.example(DEFAULT_EXAMPLE)["mainline"] == source["moves"]
    assert len(source["moves"]) == 81
    assert source["source"]["date"] == "2025-05-11"


def test_opening_name_requires_an_exact_verified_prefix():
    assert recognize_opening(["h2e2", "h9g7", "h0g2", "b9c7"])["name"] == "中炮对屏风马"
    assert recognize_opening(["h2e2", "h9g7", "h0g2", "c6c5"]) is None
    assert recognize_opening(["h2e2", "h9g7"]) is None


def test_grouped_opening_keeps_every_intermediate_board_and_context():
    moves = ["h2e2", "h9g7", "h0g2", "b9c7", "i0h0", "i9h9"]
    script = make_game({"moves": moves}, "group-test")
    script["paragraphs"] = [{"id": "opening", "title": "中炮对屏风马", "text": "红方当头炮，黑方屏风马。"}]
    script["events"][:4] = [{"id": "opening", "type": "move_group", "moves": moves[:4], "paragraph": "opening"}]
    plan = compile_demo(script, require_audio=False, require_narration=False)
    group = plan["events"][0]
    assert [f["state"]["fen"] for f in group["frames"]] == [
        position_after(script["initial_fen"], moves[:i]) for i in range(1,5)]
    assert len(plan["narration_inputs"]) == 2
    assert len(plan["narration_inputs"][0]["history"]) == 4
    assert plan["events"][-1]["state"]["ply"] == len(moves)
    invalid = deepcopy(script)
    invalid["events"][0]["moves"][1] = "b9c7"
    with pytest.raises(ValueError, match="主线"):
        compile_demo(invalid, require_audio=False, require_narration=False)


def test_ordinary_exchanges_do_not_displace_real_sacrifices_in_selection():
    source = json.loads((DATA / "modern-game.json").read_text(encoding="utf-8"))
    initial = make_game(source, "selection-test")["initial_fen"]
    moves = source["moves"]
    for ply, expected in [(39,False),(50,True),(57,False),(60,False)]:
        assert sacrifice_signal(position_after(initial,moves[:ply-1]),moves[ply-1:ply+6]) == expected


def test_legacy_import_cache_is_upgraded_to_request_full_review(tmp_path):
    library = GameLibrary(tmp_path)
    source = {"title": "旧导入", "moves": ["h2e2", "h9g7"]}
    saved = library.import_game(source)
    saved.pop("review_requested")
    folder = library.game_dir(saved["id"])
    folder.mkdir(parents=True)
    (folder / "script.json").write_text(json.dumps(saved), encoding="utf-8")
    migrated = library.import_game(source)
    assert migrated["id"] == saved["id"]
    assert migrated["review_requested"] is True


def test_short_recognized_opening_can_compile_without_inventing_a_turning_point():
    from chessmuse.autoreview import compile_story
    moves = ["h0g2", "h9g7", "b0c2", "b9c7"]
    source = make_game({"moves": moves}, "quiet-import")
    final = {"fen": position_after(source["initial_fen"], moves), "terminal": False,
             "lines": [{"score": {"kind": "cp", "value": 0, "bound": "exact"}}]}
    evidence = {"input": source, "opening": recognize_opening(moves), "nodes": [], "scan": {"final": final}}
    beat = lambda text, ref: {"text": text, "facts": [ref], "focus": None}
    generation = {"story": {"intro": [beat("双方从起马开始布局。", "game")], "nodes": [],
                            "finish": [beat("棋谱记录到这里，双方仍在开局阶段。", "ending")]}}
    reviewed = compile_story(source, evidence, generation)
    demo = compile_demo(reviewed, require_audio=False, require_narration=False)
    assert [e["type"] for e in demo["events"]] == ["speak", "move_group", "speak"]
    assert demo["events"][-1]["state"]["ply"] == 4
    assert not demo["narration_inputs"]


def test_review_failure_never_falls_back_to_only_move_announcements(tmp_path, monkeypatch):
    import asyncio
    from chessmuse.llm import LLMError
    library = GameLibrary(tmp_path)
    script = library.import_game({"moves": ["h2e2", "h9g7"]})

    async def failed_review(source, directory, **kwargs):
        assert source["mainline"] == script["mainline"]
        raise LLMError("讲解尚未通过审阅", kind="review")

    def unexpected(*args, **kwargs):
        pytest.fail("详细复盘失败时不能偷偷改为只生成短句")

    monkeypatch.setattr("chessmuse.autoreview.prepare_review", failed_review)
    monkeypatch.setattr("chessmuse.library.DeepSeekSettings.from_env", unexpected)
    with pytest.raises(LLMError, match="尚未通过"):
        asyncio.run(library.prepare(script))
    assert not (library.game_dir(script["id"]) / "script.json").exists()


def test_generator_reports_exact_correction_and_can_review_a_game_without_nodes(tmp_path):
    import asyncio
    from chessmuse.autoreview import generate_story
    from chessmuse.llm import Completion, TokenUsage
    beat = {"text": "双方从起马开始布局。", "facts": ["game"], "focus": None}
    valid = {"intro": [beat], "nodes": [], "finish": [beat]}
    invalid = {**valid, "intro": [beat, beat, beat]}

    class Client:
        def __init__(self, values):
            self.values = iter(values)
            self.calls = []

        async def complete(self, messages, **kwargs):
            self.calls.append(deepcopy(messages))
            value = next(self.values)
            return Completion(json.dumps(value), "offline-test", "request", TokenUsage(1, 1, 2), value)

    actor = Client([invalid, valid])
    critic = Client([{"ready": True, "issues": []}])
    packed = {"game": {}, "opening": None, "nodes": [], "ending": {}}
    generated = asyncio.run(generate_story(actor, packed, {}, tmp_path, lambda _: None, critic))
    assert generated["story"] == valid
    assert "intro" in actor.calls[1][-1].content
    assert "只允许1到1" in actor.calls[1][-1].content
    assert len(critic.calls) == 1
    assert (tmp_path / "draft-1.json").exists()
    # A failed later job can reuse the successfully reviewed section.
    cached = asyncio.run(generate_story(actor, packed, {}, tmp_path, lambda _: None, critic))
    assert cached == generated
    assert len(actor.calls) == 2


def test_remaining_piece_claims_use_the_board_instead_of_model_audit():
    from chessmuse.autoreview import validate_material_claims
    counts = {"红方": {"车": 1, "马": 1, "兵": 2}, "黑方": {"车": 1, "炮": 1, "卒": 2}}
    validate_material_claims("红方剩车马兵，黑方剩车炮卒。", counts)
    with pytest.raises(ValueError, match="当前棋盘已经没有"):
        validate_material_claims("双方都剩车马炮。", counts)


def test_attacking_a_pawn_exposes_the_attackers_legal_recaptures():
    from chessmuse.review_evidence import capture_reply_evidence
    source = json.loads((DATA / "modern-game.json").read_text(encoding="utf-8"))
    initial = make_game(source, "capture-test")["initial_fen"]
    fen = position_after(initial, source["moves"][:50] + ["g5h5"])
    capture = next(row for row in capture_reply_evidence(fen) if row["capture"]["move"] == "h6h5")
    assert capture["capture"]["capture"] == "兵"
    assert {f["move"] for f in capture["recaptures"]} >= {"d5h5", "f5h5"}
    assert all(f["capture"] == "车" for f in capture["recaptures"])


def test_quiet_game_does_not_require_invented_mistakes_or_branches():
    from chessmuse.review_evidence import select_nodes
    assert select_nodes([{"ply": i, "importance": 20} for i in range(1, 15)]) == []
    rows = [{"ply": 3, "importance": 500}, {"ply": 10, "importance": 180},
            {"ply": 11, "importance": 400}, {"ply": 14, "importance": 350}]
    assert [n["ply"] for n in select_nodes(rows, opening_plies=4)] == [11, 14]


@pytest.mark.parametrize("claim", ["红方提车过河。", "双方两车在河沿相对。"])
def test_short_game_cannot_invent_rooks_crossing_or_meeting_at_the_river(claim):
    from chessmuse.autoreview import validate_story
    packed = {"game": {}, "opening": None, "nodes": [], "ending": {"locations": [
        {"side": "红方", "piece": "车", "zone": "红方底线"},
        {"side": "黑方", "piece": "车", "zone": "黑方底线"}]}}
    story = {"intro": [{"text": "双方正常出子，展开阵型。", "facts": ["game"], "focus": None}],
             "nodes": [], "finish": [{"text": claim, "facts": ["ending"], "focus": None}]}
    with pytest.raises(ValueError, match="过河|河沿"):
        validate_story(story, packed, {})


def test_attention_matches_both_piece_kind_and_side():
    from cchess import FULL_INIT_FEN
    from chessmuse.autoreview import validate_focus_match
    from chessmuse.review_evidence import piece_annotations
    focus = piece_annotations(FULL_INIT_FEN, "focus")["focus-b0"]
    validate_focus_match("红马要先跳出来。", focus, {})
    with pytest.raises(ValueError, match="阵营不符"):
        validate_focus_match("黑马要先跳出来。", focus, {})
    with pytest.raises(ValueError, match="focus却圈住"):
        validate_focus_match("红车准备出动。", focus, {})


def test_shipped_primary_review_reuses_text_offline_and_returns_all_branches(tmp_path, monkeypatch):
    import asyncio
    from chessmuse.autoreview import prepare_review, REVIEW_VERSION
    library = GameLibrary(tmp_path)
    source = library.example(DEFAULT_EXAMPLE)
    assert source["review_version"] == REVIEW_VERSION

    def unexpected(*args, **kwargs):
        pytest.fail("已交付的主示例无需重新搜索或调用LLM")

    monkeypatch.setattr("chessmuse.autoreview.load_or_build_evidence", unexpected)
    reviewed = asyncio.run(prepare_review(source, tmp_path / "review"))
    demo = compile_demo(reviewed, require_audio=False, narration_path=library.narration_path(DEFAULT_EXAMPLE))
    restores = [e for e in demo["events"] if e["type"] == "set_position" and not e["state"]["branch"]]
    assert [e["mainline_ply"] for e in restores] == [50, 70, 72]
    for event in restores:
        assert event["animation_ms"] == 650
        assert event["state"]["fen"] == position_after(source["initial_fen"], source["mainline"][:event["mainline_ply"]])
    assert demo["events"][-1]["state"]["ply"] == 81
    assert sum(bool(p.get("annotation")) for p in demo["paragraphs"]) >= 12
