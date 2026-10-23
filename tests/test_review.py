import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from cchess import ChessBoard

from chessmuse.demo import DATA, compile_demo
from chessmuse.engine import position_after
from chessmuse.intuition import ALLOWLIST, EXECUTABLE, REVISION, extract_sources, rank_candidates
from chessmuse.library import GameLibrary
from chessmuse.review import cannon_attack_evidence, famous_source, sacrifice_assessment, trace, validate_plan


def test_famous_mainline_is_complete_and_ends_in_checkmate():
    source = famous_source()
    assert len(source["mainline"]) == 25
    proof = trace(source["initial_fen"], source["mainline"])
    assert proof["terminal"] and proof["events"][-1]["gives_check"]
    assert proof["events"][11]["capture"] == "马"
    assert proof["events"][19]["capture"] == "车"
    assert source["source"]["kind"].startswith("古谱")


def test_famous_review_returns_every_branch_and_preserves_actual_mainline():
    library = GameLibrary()
    script = library.example("qima-13")
    demo = compile_demo(script, require_audio=False, narration_path=library.narration_path("qima-13"))
    snapshots = [position_after(script["initial_fen"], script["mainline"][:i]) for i in range(26)]
    restores = [event for event in demo["events"] if event["type"] == "set_position" and not event["state"]["branch"]]
    assert len(restores) == 4
    for event in restores:
        assert event["state"]["fen"] == snapshots[event["mainline_ply"]]
    actual = [e["move"] for e in demo["events"] if e["type"] == "move" and e["state"]["branch"] is None]
    assert actual == script["mainline"]
    assert demo["events"][-1]["state"]["fen"] == snapshots[-1]
    assert ChessBoard(snapshots[-1]).no_moves()
    assert all(p["text"] for p in demo["paragraphs"])


def test_composition_cannot_inject_free_tactical_text():
    composition = json.loads((DATA / "famous-composition.json").read_text(encoding="utf-8"))
    validate_plan(composition["catalog"], composition["plan"])
    broken = deepcopy(composition["plan"])
    broken["nodes"][0]["segments"][0]["transition"] = "黑方被强制将死"
    with pytest.raises(ValueError, match="未核验"):
        validate_plan(composition["catalog"], broken)
    broken = deepcopy(composition["plan"])
    broken["nodes"][0]["segments"].pop()
    with pytest.raises(ValueError, match="依次"):
        validate_plan(composition["catalog"], broken)


def test_sacrifice_requires_net_loss_expert_approval_and_verified_terminal_line():
    source = famous_source()
    fen = position_after(source["initial_fen"], source["mainline"][:18])
    proof = trace(fen, source["mainline"][18:])
    result = sacrifice_assessment(proof, True)
    assert result["label"] == "弃子攻杀候选"
    assert result["gross_material_given"] == 600
    assert result["net_change_in_shown_line"] == -195
    assert not result["all_defenses_forced_mate"]
    assert sacrifice_assessment(proof, False)["label"] == "证据不足，不归类"
    brief = trace(fen, source["mainline"][18:20])
    assert sacrifice_assessment(brief, True)["label"] == "证据不足，不归类"


def test_cannon_attack_is_verified_but_not_presented_as_a_forced_capture():
    source = famous_source()
    fen = position_after(source["initial_fen"], source["mainline"][:13])
    proof = cannon_attack_evidence(fen)
    assert fen.endswith(" b")
    assert proof["screen"] == "e7" and proof["target"] == "g7"
    assert proof["kind"] == "geometric_attack_not_forced_capture"
    changed = ChessBoard(fen)
    changed.pop_fench((4, 7))
    with pytest.raises(ValueError):
        cannon_attack_evidence(changed.to_fen())


def test_archive_extraction_writes_only_cpp_allowlist(tmp_path):
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as archive:
        for name in ALLOWLIST:
            archive.writestr(f"eleeye-{REVISION}/{name}", "test source")
        archive.writestr(f"eleeye-{REVISION}/LEAGUE/LIVE/upload.php", "irrelevant")
        archive.writestr("../outside.txt", "irrelevant")
    extract_sources(memory.getvalue(), tmp_path)
    assert sorted(str(p.relative_to(tmp_path)).replace("\\", "/") for p in tmp_path.rglob("*") if p.is_file()) == sorted(ALLOWLIST)
    assert not (tmp_path / "LEAGUE").exists()


def test_real_intuition_has_same_depth_exact_ranks_and_reproducible_preferences():
    if not EXECUTABLE.exists():
        pytest.skip("未构建ElephantEye直觉模型")
    source = famous_source()
    fen = position_after(source["initial_fen"], source["mainline"][:12])
    material = rank_candidates(fen, model="material", depth=5)
    traditional = rank_candidates(fen, model="traditional", depth=5)
    repeated = rank_candidates(fen, model="material", depth=5, force=True)
    assert material["candidates"][0]["move"] == "a1a0"
    assert traditional["candidates"][0]["move"] == "b2b7"
    assert [(r["move"],r["score"]) for r in material["candidates"]] == [(r["move"],r["score"]) for r in repeated["candidates"]]
    assert len(material["candidates"]) == len(traditional["candidates"])
    assert [c["rank"] for c in material["candidates"]] == list(range(1,len(material["candidates"])+1))


@pytest.mark.parametrize("text", [
    "先看直觉。",
    "纯子力模型在一层、三层和五层搜索中都把炮二进七吃马排在首位。",
    "这步损失778个引擎分值。",
    "评价为-71。",
    "十六层认为应该补士。",
    "这个走法排第4。",
    "红方胜率达到90%。",
])
def test_technical_fact_cards_are_rejected_before_composition(text):
    composition = json.loads((DATA / "famous-composition.json").read_text(encoding="utf-8"))
    catalog = deepcopy(composition["catalog"])
    catalog["12"]["intuition"] = text
    with pytest.raises(ValueError, match="后台技术话语"):
        validate_plan(catalog, composition["plan"])


def test_chess_numbers_and_concrete_explanations_remain_speakable():
    from chessmuse.review import validate_spoken_commentary
    validate_spoken_commentary("这里可不能急着吃马。先走士六进五补防，再看看红方怎么应。")
    validate_spoken_commentary("红方少了一匹马，炮八进五，隔着黑炮盯住黑马。")


@pytest.mark.parametrize("text", [
    "这步不是贪子，而是争先。",
    "关键并非多吃一子，而是接上进攻。",
    "关键不在于吃子，而在于补防。",
])
def test_commentary_rejects_invented_contrast(text):
    from chessmuse.review import validate_spoken_commentary
    with pytest.raises(ValueError, match="立靶"):
        validate_spoken_commentary(text)
