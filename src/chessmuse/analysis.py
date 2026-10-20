"""Low-cost game scan and explicitly limited expert rechecks (MVP step 2)."""

from chessmuse.engine import position_after, validate_fen
from chessmuse.game import play


def cp_loss(best: dict, actual: dict) -> int | None:
    """Same root player's POV only; never turn mate or score bounds into cp."""
    if (best.get("fen") != actual.get("fen") or best.get("score_pov") != actual.get("score_pov")
            or best.get("request", {}).get("depth") != actual.get("request", {}).get("depth")):
        return None
    if not best["lines"] or not actual["lines"]:
        return None
    a, b = best["lines"][0]["score"], actual["lines"][0]["score"]
    if any(s["kind"] != "cp" or s["bound"] != "exact" for s in (a, b)):
        return None
    # Retain negative differences: independent finite searches can disagree.
    return a["value"] - b["value"]


def analyze_game(engine, initial_fen: str, moves: list[str], *, scan_depth=6,
                 review_depth=10, max_reviews=3, review_plies=(), force=False, progress=None) -> dict:
    """Validate the whole score first, then scan every move and the final position.

    Review selection is provisional cp-loss triage, not the future tactical or
    intuition selector. User-selected plies take precedence within the budget.
    """
    if not isinstance(moves, list) or not moves or len(moves) > 200 or any(not isinstance(m, str) for m in moves):
        raise ValueError("分析棋谱须包含1到200个半回合")
    if not 1 <= scan_depth < review_depth <= 64 or not 0 <= max_reviews <= 20:
        raise ValueError("需要 1 <= scan_depth < review_depth <= 64，max_reviews 为0到20")
    review_plies = list(dict.fromkeys(review_plies))
    if any(type(p) is not int or not 1 <= p <= len(moves) for p in review_plies):
        raise ValueError("复查步号必须位于棋谱中（从1开始）")
    if len(review_plies) > max_reviews:
        raise ValueError("指定复查步数超过 max_reviews")
    initial_fen = validate_fen(initial_fen)
    # No expensive search before all actual moves have passed the rules boundary.
    snapshots = [position_after(initial_fen, [])]
    for move in moves:
        snapshots.append(play(snapshots[-1], move)[0])
    report = progress or (lambda message: None)
    scan = []
    for index, move in enumerate(moves):
        report(f"浅扫 {index + 1}/{len(moves)}：{move}")
        best = engine.search(initial_fen, moves[:index], depth=scan_depth, force=force)
        actual = engine.search(initial_fen, moves[:index], depth=scan_depth,
                               searchmoves=[move], force=force)
        loss = cp_loss(best, actual)
        scan.append({"ply": index + 1, "fen": snapshots[index], "actual_move": move,
                     "best": best, "actual": actual, "loss_cp": loss,
                     "search_disagreement": loss is not None and loss < 0})
    final = engine.search(initial_fen, moves, depth=scan_depth, force=force)
    ranked = sorted(scan, key=lambda row: max(0, row["loss_cp"] or 0), reverse=True)
    selected = list(review_plies)
    # Mate/bound evidence is reviewed without a fabricated numeric loss.
    for row in [r for r in scan if r["loss_cp"] is None] + ranked:
        if len(selected) >= max_reviews:
            break
        if row["ply"] not in selected:
            selected.append(row["ply"])
    reviews = []
    for ply in selected:
        report(f"深度复查第 {ply} 半回合")
        best = engine.search(initial_fen, moves[:ply - 1], depth=review_depth, multipv=3, force=force)
        actual = engine.search(initial_fen, moves[:ply - 1], depth=review_depth,
                               searchmoves=[moves[ply - 1]], force=force)
        reviews.append({"ply": ply, "reason": "指定复查" if ply in review_plies else "浅扫差值/非cp证据待复查",
                        "best": best, "actual": actual, "loss_cp": cp_loss(best, actual)})
    return {"schema_version": 1, "scope": "expert_scan_not_tactical_classification",
            "initial_fen": initial_fen, "moves": moves, "snapshots": snapshots,
            "score_pov": "side_to_move_at_each_root", "scan_depth": scan_depth,
            "review_depth": review_depth, "scan": scan, "final": final, "reviews": reviews}


def analyze_cases(engine, cases: list[dict], *, depth=8, force=False, progress=None) -> dict:
    """Small inspectable fixtures, with no unverified tactical labels."""
    from chessmuse.game import move_facts
    if not isinstance(cases, list) or not cases:
        raise ValueError("实验集必须为非空 JSON 数组")
    checked = []
    for case in cases:
        if not isinstance(case, dict) or not all(isinstance(case.get(k), str) for k in ("id", "initial_fen", "candidate")):
            raise ValueError("每个实验须提供 id、initial_fen、candidate 字符串")
        fen = position_after(case["initial_fen"], case.get("moves", []))
        _, notation = play(fen, case["candidate"])
        checked.append((case, move_facts(fen, case["candidate"], notation)))
    results = []
    for case, facts in checked:
        if progress:
            progress(case["id"])
        best = engine.search(case["initial_fen"], case.get("moves", []), depth=depth, multipv=3, force=force)
        candidate = engine.search(case["initial_fen"], case.get("moves", []), depth=depth,
                                  searchmoves=[case["candidate"]], force=force)
        results.append({"case": case, "facts": facts, "best": best, "candidate": candidate,
                        "loss_cp": cp_loss(best, candidate)})
    return {"schema_version": 1, "scope": "expert_fixture_experiments", "depth": depth, "cases": results}
