"""Evidence-backed famous-game review; language models cannot author moves."""

import json
from dataclasses import asdict
from pathlib import Path

from cchess import ChessBoard

from chessmuse.analysis import analyze_game, cp_loss
from chessmuse.demo import DATA, compile_demo
from chessmuse.engine import position_after, verified_line
from chessmuse.game import play
from chessmuse.intuition import rank_candidates
from chessmuse.llm import Message

KEY_PLIES = (12, 13, 19)
VALUES = {"r": 600, "n": 270, "c": 285, "a": 120, "b": 120, "p": 30, "k": 0}


def famous_source() -> dict:
    return json.loads((DATA / "famous-game.json").read_text(encoding="utf-8"))


def trace(fen: str, moves: list[str]) -> dict:
    line = verified_line(fen, {"pv": moves})
    red = fen.split()[1] == "w"
    change = 0
    ledger = []
    for event in line["events"]:
        board = ChessBoard(event["fen"])
        end = event["move"][2:]
        captured = board.get_fench((ord(end[0])-97, int(end[1])))
        if captured:
            change += VALUES[captured.lower()] * (-1 if captured.isupper() == red else 1)
        ledger.append({"move": event["move"], "capture": event["capture"],
                       "gives_check": event["gives_check"], "net_material_change": change})
    return {"events": line["events"], "ledger": ledger,
            "terminal": ChessBoard(line["events"][-1]["after_fen"]).no_moves(),
            "net_material_change": change}


def sacrifice_assessment(proof: dict, approved: bool) -> dict:
    """Conservative branch-level evidence, not a claim about all defenses."""
    own_loss = 0
    root_red = proof["events"][0]["fen"].split()[1] == "w"
    for event in proof["events"]:
        end = event["move"][2:]
        captured = ChessBoard(event["fen"]).get_fench((ord(end[0])-97, int(end[1])))
        if captured and captured.isupper() == root_red:
            own_loss += VALUES[captured.lower()]
    verified_mate = proof["terminal"] and proof["events"][-1]["gives_check"]
    candidate = own_loss > 0 and proof["net_material_change"] < 0 and approved and verified_mate
    return {"label": "弃子攻杀候选" if candidate else "证据不足，不归类",
            "gross_material_given": own_loss, "net_change_in_shown_line": proof["net_material_change"],
            "short_term_fully_recovered": proof["net_material_change"] >= 0,
            "verified_check_events": [i+1 for i,e in enumerate(proof["events"]) if e["gives_check"]],
            "verified_mate_in_shown_line": verified_mate, "approved_at_both_depths": approved,
            "all_defenses_forced_mate": False}


def cannon_attack_evidence(fen: str) -> dict:
    """Verify the geometric capture opportunity; not a forced next move."""
    board = ChessBoard(fen)
    occupied = [(x, board.get_fench((x, 7))) for x in range(2, 6) if board.get_fench((x, 7))]
    if board.get_fench((1, 7)) != "C" or board.get_fench((6, 7)) != "n" or occupied != [(4, "c")]:
        raise ValueError("炮架与黑马的几何关系已变化，需重审讲解")
    hypothetical = " ".join([fen.split()[0], "w"])
    after, notation = play(hypothetical, "b7g7")
    return {"kind": "geometric_attack_not_forced_capture", "fen": fen, "attacker": "b7", "screen": "e7",
            "target": "g7", "hypothetical_move_without_defender_reply": "b7g7",
            "hypothetical_notation": notation, "hypothetical_after_fen": after,
            "limitation": "仅核验炮的攻击关系；实际轮到黑方，黑方可以应对，不能当成强制吃马"}


def build_evidence(engine, *, force=False, progress=print) -> dict:
    source = famous_source()
    initial, moves = source["initial_fen"], source["mainline"]
    scan = analyze_game(engine, initial, moves, scan_depth=7, review_depth=12,
                        max_reviews=3, review_plies=KEY_PLIES, force=force, progress=progress)
    nodes = []
    for ply in KEY_PLIES:
        progress(f"直觉模型与专家对照：第{ply}半回合")
        history = moves[:ply-1]
        fen = position_after(initial, history)
        models = [rank_candidates(fen, model=model, depth=depth, force=force)
                  for model in ("material", "traditional") for depth in (1, 3, 5)]
        # Evaluate each distinct intuition first choice, even outside MultiPV.
        choices = dict.fromkeys([moves[ply-1]] + [r["candidates"][0]["move"] for r in models])
        best = engine.search(initial, history, depth=16, multipv=3, force=force)
        candidates = {move: engine.search(initial, history, depth=16, searchmoves=[move], force=force)
                      for move in choices}
        nodes.append({"ply": ply, "fen": fen, "actual_move": moves[ply-1], "models": models,
                      "expert": best, "candidates": candidates,
                      "selection_reason": {12: "吃马诱惑与防守反例", 13: "纯子力模型与专家、传统评价的分歧",
                                           19: "弃车攻杀候选及接受/拒绝分支"}[ply]})
        if ply == 13:
            nodes[-1]["cannon_attack"] = cannon_attack_evidence(play(fen, moves[ply-1])[0])
    root = nodes[-1]
    actual = moves[18]
    acceptance = engine.search(initial, moves[:19], depth=16, searchmoves=["e8d9"], force=force)
    refusal = engine.search(initial, moves[:19], depth=16, searchmoves=["f9f8"], force=force)
    accepted_trace = trace(root["fen"], moves[18:])
    actual12 = next(r for r in scan["reviews"] if r["ply"] == 19)["actual"]
    root["sacrifice"] = {
        "label": "弃车攻杀候选", "accepted": acceptance, "declined": refusal,
        "accepted_authored_line": accepted_trace,
        "expert_depths": [actual12["request"]["depth"], root["candidates"][actual]["request"]["depth"]],
        "expert_scores": [actual12["lines"][0]["score"], root["candidates"][actual]["lines"][0]["score"]],
        "approved_at_both_depths": all(
            loss is not None and loss <= 100 for loss in
            [cp_loss(next(r for r in scan["reviews"] if r["ply"] == 19)["best"], actual12),
             cp_loss(root["expert"], root["candidates"][actual])]),
        "limitation": "接受分支走到将死；拒绝分支仍可继续。并非对所有应法都已证明强制杀。",
    }
    root["sacrifice"]["assessment"] = sacrifice_assessment(accepted_trace, root["sacrifice"]["approved_at_both_depths"])
    # A separate concrete error, so the classic is not sold as a forced trap.
    defense = engine.search(initial, moves[:13], depth=16, multipv=3, force=force)
    retreat = engine.search(initial, moves[:13], depth=16, searchmoves=[moves[13]], force=force)
    return {"schema_version": 1, "source": source, "scan": scan, "nodes": nodes,
            "retreat_error": {"ply": 14, "best": defense, "actual": retreat, "loss_cp": cp_loss(defense, retreat)}}


def score_text(result: dict) -> str:
    score = result["lines"][0]["score"]
    if score["bound"] != "exact":
        raise ValueError("讲解不能把搜索界限作为精确评价引用")
    if score["kind"] == "mate":
        return "搜索发现杀棋" if score["value"] > 0 else "搜索发现对方存在杀棋"
    return f"行棋方视角的评价为{score['value']}，这是有限深度的引擎估计"


def fact_catalog(evidence: dict) -> dict:
    nodes = {n["ply"]: n for n in evidence["nodes"]}
    a, b, c = [nodes[ply] for ply in KEY_PLIES]
    mat = next(m for m in b["models"] if m["request"]["model"] == "material" and m["request"]["depth"] == 5)
    trad = next(m for m in b["models"] if m["request"]["model"] == "traditional" and m["request"]["depth"] == 5)
    intuitive = mat["candidates"][0]
    expert_move = b["expert"]["bestmove"]
    notation = lambda fen, move: play(fen, move)[1]
    rank = next(r["rank"] for r in mat["candidates"] if r["move"] == expert_move)
    cannon_attack_evidence(play(b["fen"], "b2b7")[0])
    sac = c["sacrifice"]
    # These values are asserted against the actual evidence before authoring.
    if (any(m["candidates"][0]["move"] != "b7b0" for m in a["models"] if m["request"]["model"] == "material") or intuitive["move"] != "a1a0"
            or trad["candidates"][0]["move"] != "b2b7" or expert_move != "b2b7"
            or not sac["accepted_authored_line"]["terminal"] or not sac["approved_at_both_depths"]):
        raise ValueError("搜索结果已改变，需重新审查讲解事实目录，不能沿用旧结论")
    return {
        "12": {
            "intuition": "先看黑方眼前的收益。纯子力模型在一层、三层和五层搜索中都把炮二进七吃马排在首位；它看见了少一匹马带来的物质收益。",
            "disagreement": f"专家在十六层搜索里更偏好先走{notation(a['fen'], a['expert']['bestmove'])}。吃马分支的{score_text(a['candidates']['b7b0'])}。这不是黑方一吃马就被证明必败。",
            "variation": "现在从吃马前的局面看一条专家建议的防守变化。推演只是代表性应对，不是唯一走法。看完后回到古谱，继续观察红方怎样利用出子先手。",
            "lesson": "这个节点要比较的是眼前得子与完成防守的先后。浅层模型的偏好不能代表所有普通棋手，古谱的漂亮结尾也不能替代对其他防守的检查。",
        },
        "13": {
            "intuition": f"红方已经少了一匹马。纯子力模型在五层搜索中优先选择{intuitive['features']['action']}，希望把车沿边线退回，攻击刚吃马的黑炮。它并不是当场就把炮吃掉。",
            "disagreement": f"专家十六层搜索把炮八进五列为首选，而它在同样五层的纯子力模型中排第{rank}。进炮后，黑方中炮成为炮架，红炮隔子攻击黑方七路马。这是攻击关系，并非已经吃到马，黑方仍可应对。加入位置表和将帅安全等特征的传统评价模型，在五层时也选择进炮。",
            "variation": "先回到红方走前的位置，对照看回车追炮的变化。对手可以移动黑炮，眼前追子并没有自动换回失去的马。之后恢复古谱中的进炮局面。",
            "lesson": "两套模型的差别说明，只数棋子容易忽略局面中的其他价值。但五层搜索仍很短，我们只把这个结果当作可解释的模型对照，不把它当成人类棋力等级。",
        },
        "19": {
            "intuition": "红方前车进一，先吃掉底士并将军；黑士可以回吃这辆车。这是一个有明确物质代价的弃车攻杀候选。",
            "disagreement": "十二层和十六层的专家复查都认可这步进车。主线接下来确实损失了一辆红车，但这不等于黑方必须接受弃车。",
            "variation": "先看拒绝弃车的将六进一分支，黑方没有吃车，局面还要继续算。再看古谱接受弃车的分支：士五退四吃车后，另一辆红车平四将军，随后进车吃炮，再由炮八平五收尾。",
            "lesson": "古谱接受弃车的这条变化，最后的将军和无合法应对都已用规则核验。将死结论只针对这条走到终局的分支；其他防守不能一概称为必杀。",
        },
    }


TRANSITIONS = ("先看直觉。", "问题在哪里？", "让我们用变化验证。", "回到这一步的棋理。")
STAGES = ("intuition", "disagreement", "variation", "lesson")


async def plan_commentary(client, catalog: dict) -> dict:
    """LLM composes from verified fact cards, never supplies tactical free text."""
    example = {"nodes": [{"ply": ply, "segments": [{"fact": stage, "transition": TRANSITIONS[i]}
                  for i,stage in enumerate(STAGES)]} for ply in KEY_PLIES]}
    messages = [
        Message("system", "你是象棋讲解编辑。按直觉、分歧、变化、总结组织给定事实卡。只可引用事实卡，不得改写事实或生成棋步。严格按示例JSON的结构，每节点四段，fact字段只能依次为intuition,disagreement,variation,lesson。transition只能从提供的短语中选，可为空字符串。"),
        Message("user", json.dumps({"cards": catalog, "transitions": TRANSITIONS, "example": example}, ensure_ascii=False)),
    ]
    for attempt in range(2):
        completion = await client.complete(messages, json_mode=True, max_tokens=2048)
        plan = completion.data
        try:
            validate_plan(catalog, plan)
            break
        except ValueError as error:
            if attempt:
                raise
            messages += [Message("assistant", completion.text),
                         Message("user", f"请修正并完整返回全部三个节点，每节点恰好四段：{error}")]
    return {"plan": plan, "model": completion.model, "usage": asdict(completion.usage),
            "request_id": completion.request_id, "mode": "llm_fact_card_composition"}


def validate_plan(catalog: dict, plan: dict):
    if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list):
        raise ValueError("解说计划格式无效")
    if any(not isinstance(n, dict) for n in plan["nodes"]) or [n.get("ply") for n in plan["nodes"]] != list(KEY_PLIES):
        raise ValueError("解说节点缺失或顺序错误")
    for node in plan["nodes"]:
        segments = node.get("segments", [])
        if (not isinstance(segments, list) or any(not isinstance(s, dict) for s in segments)
                or [s.get("fact") for s in segments] != list(STAGES)):
            raise ValueError("解说必须依次覆盖直觉、分歧、变化、总结")
        for segment in segments:
            if (set(segment) != {"fact", "transition"} or segment["transition"] not in (*TRANSITIONS, "")
                    or segment["fact"] not in catalog[str(node["ply"])]):
                raise ValueError("解说引用了未核验的自由文本或事实")


def compile_review(evidence: dict, composition: dict) -> dict:
    catalog = fact_catalog(evidence)
    validate_plan(catalog, composition["plan"])
    source = evidence["source"]
    script = {**source, "paragraphs": [], "events": [], "evidence_schema": 1,
              "commentary_mode": composition["mode"]}
    paragraphs, events = script["paragraphs"], script["events"]
    def speak(key, title, text, chapter=None):
        paragraphs.append({"id": key, "title": title, "text": text})
        events.append({"id": key, "type": "speak", "paragraph": key,
                       **({"chapter": chapter} if chapter else {})})
    def branch(key, ply, moves, label, return_ply):
        events.append({"id": f"{key}-position", "type": "set_position", "mainline_ply": ply-1, "branch": label})
        for i, move in enumerate(moves):
            events.append({"id": f"{key}-m{i+1}", "type": "move", "move": move})
        events.append({"id": f"{key}-restore", "type": "set_position", "mainline_ply": return_ply})
        speak(f"{key}-return", "回到古谱", "推演到这里，回到古谱当前局面。")
    speak("famous-intro", "认识这份古谱", "今天看橘中秘的弃马十三着。这是古谱的十三回合通行版本，不是现代比赛实战。我们看完完整主线，重点讲吃马的诱惑、进炮与追子的取舍，以及最后的弃车攻杀；同时用现代搜索检查黑方是否还有其他防守。", "古谱开场")
    plans = {n["ply"]: n for n in composition["plan"]["nodes"]}
    nodes = {n["ply"]: n for n in evidence["nodes"]}
    for ply, move in enumerate(source["mainline"], 1):
        events.append({"id": f"famous-m{ply}", "type": "move", "move": move})
        if ply == 14:
            loss = evidence["retreat_error"]["loss_cp"]
            speak("retreat-error", "退马并非最强防守", f"古谱中的黑方选择退马。十六层搜索中，这步相对同一局面的最佳走法损失约{loss}个引擎分值。这个差值只说明本局面的搜索结果；吃马后仍能补防，退马这一步才让红方的机会明显扩大。")
        if ply not in plans:
            continue
        node = nodes[ply]
        for index, segment in enumerate(plans[ply]["segments"]):
            stage = segment["fact"]
            speak(f"node-{ply}-{stage}", ["直觉", "分歧", "变化验证", "棋理总结"][index],
                  segment["transition"] + catalog[str(ply)][stage],
                  node["selection_reason"] if index == 0 else None)
            if stage == "variation":
                if ply == 12:
                    branch("defense", ply, node["expert"]["lines"][0]["pv"][:6], "黑方先补防", ply)
                elif ply == 13:
                    branch("chase", ply, node["candidates"]["a1a0"]["lines"][0]["pv"][:6], "回车追炮", ply)
                else:
                    refusal = node["sacrifice"]["declined"]["lines"][0]["pv"][:5]
                    branch("decline", ply, [move] + refusal, "拒绝弃车", ply)
                    branch("accept", ply, source["mainline"][ply-1:], "古谱接受弃车至将死", ply)
    speak("famous-finish", "复盘结束", "炮八平五，黑方已无合法应对，古谱主线到此结束。今天的关键不是见子就吃或见弃子就叫妙手，而是把眼前收益、出子节奏和后续防守放在同一条变化里检验。我们展示了专家认可的进炮和进车，也保留了黑方拒绝弃车的防守分支。", "终局总结")
    compile_demo(script, require_audio=False, require_narration=False)
    return script


def experiment_summary(evidence: dict) -> dict:
    def search_summary(result):
        depth = result["request"]["depth"]
        return {"engine": result["engine"]["name"], "request": result["request"],
                "score_pov": result["score_pov"], "fen": result["fen"], "bestmove": result["bestmove"],
                "lines": [{k: line[k] for k in ("depth", "multipv", "score", "pv")} for line in result["lines"]],
                "commands": [e["line"] for e in result["transcript"] if e["direction"] == "send"],
                "final_info": [e["line"] for e in result["transcript"] if e["direction"] == "receive"
                               and (e["line"].startswith(f"info depth {depth} ") or e["line"].startswith("bestmove "))]}
    nodes = []
    for node in evidence["nodes"]:
        models = []
        for result in node["models"]:
            important = {node["actual_move"], node["expert"]["bestmove"]}
            rows = [c for c in result["candidates"] if c["rank"] <= 5 or c["move"] in important]
            models.append({"model": result["request"]["model"], "depth": result["request"]["depth"],
                           "description": result["description"], "nodes": result["raw"]["nodes"],
                           "total_candidates": len(result["candidates"]), "candidates": rows})
        summary = {"ply": node["ply"], "selection_reason": node["selection_reason"], "models": models,
                   "expert": search_summary(node["expert"]),
                   "candidates": {m: search_summary(r) for m,r in node["candidates"].items()}}
        if node["ply"] == 13:
            summary["cannon_attack"] = cannon_attack_evidence(play(node["fen"], node["actual_move"])[0])
        if "sacrifice" in node:
            sac = node["sacrifice"]
            summary["sacrifice"] = {
                "assessment": sacrifice_assessment(sac["accepted_authored_line"], sac["approved_at_both_depths"]),
                "ledger": sac["accepted_authored_line"]["ledger"], "expert_depths": sac["expert_depths"],
                "expert_scores": sac["expert_scores"], "accepted": search_summary(sac["accepted"]),
                "declined": search_summary(sac["declined"]), "limitation": sac["limitation"]}
        nodes.append(summary)
    return {"schema_version": 1, "source": evidence["source"], "scope": "manually_selected_famous_game_nodes",
            "nodes": nodes, "retreat_error": {"ply": 14, "loss_cp": evidence["retreat_error"]["loss_cp"],
                "best": search_summary(evidence["retreat_error"]["best"]),
                "actual": search_summary(evidence["retreat_error"]["actual"])},
            "scan": [{"ply": r["ply"], "actual_move": r["actual_move"], "loss_cp": r["loss_cp"]}
                     for r in evidence["scan"]["scan"]]}


async def prepare_famous(engine_path: str, *, force=False, env_file=".env", progress=print):
    """Publish a reviewed script only after evidence and narration validate."""
    from chessmuse.engine import Pikafish
    from chessmuse.llm import DeepSeekClient, DeepSeekSettings
    from chessmuse.narration import generate_announcements, read_announcements
    from chessmuse.tts import prepare_neural_audio
    folder = Path(".cache/review/qima-13")
    folder.mkdir(parents=True, exist_ok=True)
    evidence_file = folder / "evidence.json"
    if force or not evidence_file.exists():
        with Pikafish(engine_path) as engine:
            evidence = build_evidence(engine, force=force, progress=progress)
        evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        if evidence["source"] != famous_source():
            raise ValueError("古谱来源或走法已变更，请用 --force 重建证据")
    catalog = fact_catalog(evidence)
    composition_file = folder / "composition.json"
    composition = None
    if not force and composition_file.exists():
        saved = json.loads(composition_file.read_text(encoding="utf-8"))
        if saved.get("catalog") == catalog:
            validate_plan(catalog, saved["plan"])
            composition = saved
    if composition is None:
        progress("根据已验证事实卡组织三处关键讲解")
        async with DeepSeekClient(DeepSeekSettings.from_env(env_file)) as client:
            composition = {**await plan_commentary(client, catalog), "catalog": catalog}
        composition_file.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")
    script = compile_review(evidence, composition)
    narration = DATA / "famous-narration.json"
    plan = compile_demo(script, require_audio=False, require_narration=False)
    try:
        if force:
            raise ValueError("重新生成")
        read_announcements(narration, plan["narration_inputs"])
    except (OSError, ValueError, KeyError):
        progress("为主线与推演生成走子短句")
        async with DeepSeekClient(DeepSeekSettings.from_env(env_file)) as client:
            await generate_announcements(client, plan["narration_inputs"], narration,
                                         progress=lambda part, msg: progress(f"{int(part*100)}% {msg}"))
    plan = compile_demo(script, require_audio=False, narration_path=narration)
    await prepare_neural_audio(Path("audio/qima-13"), plan["paragraphs"], force=force,
                               progress=lambda part, msg: progress(f"{int(part*100)}% {msg}"))
    playable = compile_demo(script, narration_path=narration, audio_dir=Path("audio/qima-13"))
    # These portable records support offline playback and editorial rechecking.
    (DATA / "famous-review.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "famous-composition.json").write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")
    report = Path("docs/experiments/qima-13.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(experiment_summary(evidence), ensure_ascii=False, indent=2), encoding="utf-8")
    return playable
