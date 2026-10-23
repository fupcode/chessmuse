from copy import deepcopy

import pytest

from chessmuse.branches import branch_assessment, branch_specs, verified_branch_results
from chessmuse.demo import compile_demo
from chessmuse.engine import position_after
from chessmuse.library import GameLibrary
from chessmuse.review import famous_source


@pytest.mark.parametrize("turn,value,expected", [
    ("w", 851, "红方已经大优"), ("b", -851, "红方已经大优"),
    ("w", -851, "黑方已经大优"), ("b", 851, "黑方已经大优"),
    ("w", -16, "大致均衡"), ("b", 10, "大致均衡"),
    ("w", 180, "红方稍占上风"), ("w", 400, "红方明显占优"),
])
def test_branch_assessment_uses_endpoint_side_to_move_without_speaking_scores(turn, value, expected):
    result = {"fen": famous_source()["initial_fen"].split()[0] + " " + turn,
              "terminal": False, "lines": [{"score": {"kind": "cp", "value": value, "bound": "exact"}}]}
    text = branch_assessment(result)
    assert expected in text
    assert str(abs(value)) not in text
    result["lines"][0]["score"]["bound"] = "lower"
    assert "不能断定" in branch_assessment(result)


def test_terminal_conclusion_uses_losing_side_and_mate_stays_separate_from_cp():
    result = {"fen": famous_source()["initial_fen"].split()[0] + " b", "terminal": True, "lines": []}
    assert "红方获胜" in branch_assessment(result)
    result.update(terminal=False, lines=[{"score": {"kind": "mate", "value": -3, "bound": "exact"}}])
    assert "红方已经有杀棋" in branch_assessment(result)


def test_branch_result_rejects_a_parent_position_evaluation():
    source = famous_source()
    evidence = {"source": source, "nodes": [
        {"ply": 12, "expert": {"lines": [{"pv": ["f9e8"]}]}},
        {"ply": 13, "candidates": {"a1a0": {"lines": [{"pv": ["a1a0"]}]}}},
        {"ply": 19, "sacrifice": {"declined": {"lines": [{"pv": ["f9f8"]}]}}},
    ]}
    specs = branch_specs(evidence)
    results = {}
    for key, spec in specs.items():
        history = source["mainline"][:spec["ply"] - 1] + spec["moves"]
        results[key] = {"fen": position_after(source["initial_fen"], history),
                        "request": {"moves": history}, "score_pov": "side_to_move"}
    evidence["branch_reviews"] = {"specs": specs, "results": results}
    verified_branch_results(evidence)
    changed = deepcopy(evidence)
    changed["branch_reviews"]["results"]["chase"]["fen"] = position_after(
        source["initial_fen"], source["mainline"][:12])
    with pytest.raises(ValueError, match="局面、走法或视角"):
        verified_branch_results(changed)


def test_every_branch_concludes_on_its_final_board_before_restore_and_names_actual_choice():
    library = GameLibrary()
    script = library.example("qima-13")
    demo = compile_demo(script, require_audio=False, narration_path=library.narration_path("qima-13"))
    paragraphs = {p["id"]: p for p in demo["paragraphs"]}
    for key, outcome in script["branch_outcomes"].items():
        restore = next(i for i, e in enumerate(demo["events"]) if e["id"] == f"{key}-restore")
        end = demo["events"][restore - 2:restore]
        assert [e["id"] for e in end] == [f"{key}-assessment", f"{key}-reason"]
        for event in end:
            assert event["type"] == "speak"
            assert event["state"]["branch"]
            assert event["state"]["fen"] == outcome["fen"]
        assert paragraphs[end[0]["paragraph"]]["text"] == outcome["assessment"]
        assert demo["events"][restore + 1]["id"] == f"{key}-return-1"
    assert "炮二进七" in paragraphs["defense-return-1"]["text"]
    assert "炮八进五" in paragraphs["chase-return-1"]["text"]
    assert "士五退四" in paragraphs["decline-return-1"]["text"]
