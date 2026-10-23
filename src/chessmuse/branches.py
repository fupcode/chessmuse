"""Conclusions for the exact branch positions shown in the player."""

from chessmuse.engine import position_after, verified_line


def branch_specs(evidence: dict) -> dict:
    nodes = {n["ply"]: n for n in evidence["nodes"]}
    mainline = evidence["source"]["mainline"]
    return {
        "defense": {"ply": 12, "label": "黑方先补防", "moves": nodes[12]["expert"]["lines"][0]["pv"][:6]},
        "chase": {"ply": 13, "label": "回车追炮", "moves": nodes[13]["candidates"]["a1a0"]["lines"][0]["pv"][:6]},
        "decline": {"ply": 19, "label": "拒绝弃车", "moves": [mainline[18]] + nodes[19]["sacrifice"]["declined"]["lines"][0]["pv"][:5]},
        "accept": {"ply": 19, "label": "古谱接受弃车至将死", "moves": mainline[18:]},
    }


def build_branch_reviews(engine, evidence: dict, *, force=False, progress=print) -> dict:
    specs = branch_specs(evidence)
    source = evidence["source"]
    results = {}
    for key, spec in specs.items():
        progress(f"检查推演收尾：{spec['label']}")
        history = source["mainline"][:spec["ply"] - 1] + spec["moves"]
        results[key] = engine.search(source["initial_fen"], history, depth=16, force=force)
    return {"specs": specs, "results": results}


def branch_assessment(result: dict) -> str:
    """Convert an exact endpoint estimate to cautious, chess-facing language.

    The bands are editorial phrasing for this demo, not a general strength or
    win-probability calibration. Raw values never enter spoken commentary.
    """
    red_to_move = result["fen"].split()[1] == "w"
    if result["terminal"]:
        return "黑方已无棋可走，红方获胜。" if not red_to_move else "红方已无棋可走，黑方获胜。"
    score = result["lines"][0]["score"]
    if score["bound"] != "exact":
        return "这条变化还需要继续看，暂时不能断定哪一方占优。"
    red_value = score["value"] * (1 if red_to_move else -1)
    side = "红方" if red_value > 0 else "黑方"
    if score["kind"] == "mate":
        return f"{side}已经有杀棋，对方很难守住。"
    if abs(red_value) < 100:
        return "走到这里，双方局面大致均衡。"
    if abs(red_value) < 250:
        return f"走到这里，{side}稍占上风。"
    if abs(red_value) < 700:
        return f"走到这里，{side}明显占优。"
    return f"走到这里，{side}已经大优。"


def verified_branch_results(evidence: dict) -> dict:
    reviews = evidence.get("branch_reviews", {})
    specs = branch_specs(evidence)
    if reviews.get("specs") != specs or set(reviews.get("results", {})) != set(specs):
        raise ValueError("缺少当前推演末局面的评价，请重新准备古谱")
    source = evidence["source"]
    for key, spec in specs.items():
        history = source["mainline"][:spec["ply"] - 1] + spec["moves"]
        result = reviews["results"][key]
        expected = position_after(source["initial_fen"], history)
        if (" ".join(result["fen"].split()[:2]) != expected
                or result["request"]["moves"] != history
                or result["score_pov"] != "side_to_move"):
            raise ValueError("推演评价的局面、走法或视角不匹配")
    return reviews["results"]



def branch_conclusions(evidence: dict) -> dict:
    results = verified_branch_results(evidence)
    specs = branch_specs(evidence)
    source = evidence["source"]
    reasons = {
        "defense": "先补好这一手士，黑方还有稳住局面的余地。",
        "chase": "红车追炮，黑炮可以避开；红方随后还得进炮吃马，才把子力补回来。",
        "decline": "红车还留在前方，红炮又吃到了黑马，黑方依然承受着进攻的压力。",
        "accept": "红方送掉一辆车，另一辆车和炮接上进攻，最后炮八平五，将死黑方。",
    }
    bridges = {
        "defense": "回到古谱，黑方选择炮二进七，先吃下红马。接着看红方怎么应。",
        "chase": "回到古谱，红方直接选择炮八进五，把进炮这步先走了。",
        "decline": "回到古谱，接下来黑方选择士五退四，吃下红车。咱们再看吃车这条路。",
        "accept": "回到古谱，接着走完士五退四吃车以后的这段攻杀。",
    }
    conclusions = {}
    for key, spec in specs.items():
        root = position_after(source["initial_fen"], source["mainline"][:spec["ply"] - 1])
        events = verified_line(root, {"pv": spec["moves"]})["events"]
        # These concrete reasons are authored for this reviewed demonstration.
        # Stop for editorial review if a newly generated PV changes their basis.
        if (key == "defense" and spec["moves"][0] != "f9e8"
                or key == "chase" and (spec["moves"][:3] != ["a1a0", "b0b1", "b2b7"]
                                      or events[4]["capture"] != "马")
                or key == "decline" and (spec["moves"][1] != "f9f8" or events[4]["capture"] != "马")
                or key == "accept" and (not results[key]["terminal"] or not events[-1]["gives_check"])):
            raise ValueError("推演变化已改变，需重新审阅收尾理由")
        conclusions[key] = {"fen": " ".join(results[key]["fen"].split()[:2]),
            "assessment": branch_assessment(results[key]), "reason": reasons[key], "return": bridges[key]}
    return conclusions
