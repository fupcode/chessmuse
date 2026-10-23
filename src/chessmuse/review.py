"""Evidence-backed famous-game review; language models cannot author moves."""

import json
import re
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


def fact_catalog(evidence: dict) -> dict:
    nodes = {n["ply"]: n for n in evidence["nodes"]}
    a, b, c = [nodes[ply] for ply in KEY_PLIES]
    mat = next(m for m in b["models"] if m["request"]["model"] == "material" and m["request"]["depth"] == 5)
    trad = next(m for m in b["models"] if m["request"]["model"] == "traditional" and m["request"]["depth"] == 5)
    intuitive = mat["candidates"][0]
    expert_move = b["expert"]["bestmove"]
    notation = lambda fen, move: play(fen, move)[1]
    cannon_attack_evidence(play(b["fen"], "b2b7")[0])
    sac = c["sacrifice"]
    # These values are asserted against the actual evidence before authoring.
    if (any(m["candidates"][0]["move"] != "b7b0" for m in a["models"] if m["request"]["model"] == "material") or intuitive["move"] != "a1a0"
            or trad["candidates"][0]["move"] != "b2b7" or expert_move != "b2b7"
            or not sac["accepted_authored_line"]["terminal"] or not sac["approved_at_both_depths"]):
        raise ValueError("搜索结果已改变，需重新审查讲解事实目录，不能沿用旧结论")
    return {
        "12": {
            "intuition": "这匹马就摆在炮口上，黑方当然想吃。炮二进七，马是拿到了，可接下来还得顾着自己的防守。",
            "disagreement": f"这里可不能光顾着吃马。先走{notation(a['fen'], a['expert']['bestmove'])}补好防守，是更稳妥的选择。不过，吃马也不等于马上输棋，黑方后面还有补防的机会。",
            "variation": "把棋退回吃马之前，看看黑方先补防会怎么走。这里只看其中一种应对，随后再接着看古谱里的吃马。",
            "lesson": "有子可吃，先别急着伸手。把防守补好，还是先拿这匹马，要连着对方后面的出子一起看。",
        },
        "13": {
            "intuition": f"红方少了一匹马，很容易想着赶紧追回来。比如走{intuitive['features']['action']}，退车去追那门吃马的炮。但追上炮，可不等于已经把子拿回来了。",
            "disagreement": "红方选择炮八进五。注意黑方的中炮，恰好给红炮当了炮架，红炮隔着它就盯上了黑马。这会儿还没吃到马，黑方也能应对，但进炮和退车追炮，着眼点已经不同了。",
            "variation": "如果红方回车追炮呢？把刚才这步退回来看看。黑炮可以走开，红方并不能追一下就把丢掉的马补回来。看过这条变化，再回到进炮的局面。",
            "lesson": "少了子，也别只盯着怎么追回来。这步进炮借用了对方的炮架，把目标转向黑马；先看看手里的棋子还能做什么，再决定要不要追着吃。",
        },
        "19": {
            "intuition": "这一步，红方前车进一，吃士将军！可黑士回头就能吃掉这辆车，红方为什么还敢往里送？",
            "disagreement": "红方看中的是吃车以后的进攻，愿意为此付出一辆车。不过黑方也不一定非吃不可，还可以走将六进一。吃还是不吃，得分开来看。",
            "variation": "先看看黑方不吃车，走将六进一会怎样，这条路还要继续周旋。再回来看古谱：黑方士五退四吃车，红方另一辆车马上平四将军，接着进车吃炮，最后炮八平五收尾。",
            "lesson": "古谱里黑方吃下这辆车，最后确实被将死了。漂亮就漂亮在，红方送掉一车，另一辆车和炮却接上了进攻。不过这个结尾是顺着吃车走出来的，黑方不吃车时，还得另算。",
        },
    }


# The cards already contain spoken transitions; do not prepend workflow labels.
TRANSITIONS = ("",)
STAGES = ("intuition", "disagreement", "variation", "lesson")


COMMENTARY_SYSTEM_PROMPT = """你是一位正在和观众一起看棋的中国象棋讲解员。观众只关心这步棋为什么这样走、对手怎样应，以及接下来会发生什么。

口吻与内容：
- 直接从棋盘上的具体问题说起：哪颗子能吃、哪颗子受攻、谁要补防、怎样接上进攻。用简洁、顺口的中文，像坐在棋盘旁讲给人听。
- 先说眼前走法的吸引力或危险，再用已核验的应对解释后果。不要每段都套同一个开场，也不要把“这里可不能”变成新的固定口头禅。
- 直接说明事实和理由，禁止“不是……而是……”“并非……而是……”“不在于……而在于……”这类先否定再转折的表达。不要为说明一个事实，凭空引入观众没提出、此前没讨论的错误观点。不要把同一句式换成“并不是”“并非”继续使用。实际存在的两种走法可以直接说明各自后果。
- 每个判断尽量落到具体棋子、走法或变化上。不能只把分数换成“优势很大”“局面更好”，也不能只用“引擎推荐”代替原因。

绝不进入字幕或语音的内容：
- “先看直觉”“问题/分歧分析”“专家走法”“变化验证”“棋理总结”等流程播报。
- 纯子力模型、传统评价模型、直觉模型、两套模型、专家模型、Pikafish、ElephantEye、算法、位置表、搜索、深度、层数、节点数、候选排名、首选排名、PV、MultiPV。
- 引擎评分、分差、评价值、cp、胜率，以及“评价为-71”“损失778分”“一层、三层和五层都排首位”等数值报告。棋谱中的路数、步数和真实得失子数量可以正常使用。
这些是后台选材和核验资料，不能照读，也不要换个说法讲技术过程。

表达示例（只示范口吻，不给当前局面增加事实）：
反例：“先看直觉。纯子力模型在一层、三层和五层搜索中都把炮二进七吃马排在首位。”
正例：“这匹马就摆在炮口上，很容易想先吃下来。可吃完以后，自己的防守顾得上吗？”
反例：“专家十六层搜索认为这步损失778个引擎分值。”
正例：“这一步退马，给了红方继续进攻的机会。”仅当给定事实确实支持这一后果时才可这样说。
反例：“两套模型有分歧，接下来用变化验证。”
正例：“急着退车追炮，黑炮走开了怎么办？咱们把这几步摆出来看看。”

事实边界：
只能使用给定事实卡，不得编造走法、攻击关系、得失子或杀棋。不能把“攻击到”说成“已经吃到”，不能把一条变化里的将死说成所有防守都必败。没有具体原因可用时，少说一句，不能拿技术指标或空泛赞叹凑解释。

当前任务是编排已经审阅的口语事实卡，不是自由改写：严格按示例 JSON 返回全部三个节点，每节点四段。fact 必须依次为 intuition、disagreement、variation、lesson；它们仅是程序内部引用键，不是观众听到的标题。transition 必须为空字符串，卡片自身已有衔接。只返回 JSON。
"""

_TECHNICAL_COMMENTARY = re.compile(
    r"先看直觉|两套模型|模型|引擎|搜索|算法|位置表|分歧|变化验证|棋理总结|专家走法|"
    r"候选排名|排在首位|排第|评价[值为]|评分|分差|分值|胜率|层数|节点数|"
    r"(?:[一二三四五六七八九十百零、和及\d]+)层|"
    r"Pikafish|ElephantEye|MultiPV|\bPV\b|\bcp\b|depth",
    re.IGNORECASE,
)


def validate_spoken_commentary(text: str):
    if re.search(r"(?:不是|并非|不在于)[^。！？\n]*(?:而是|而在于)", text):
        raise ValueError("讲解请直接说明事实，禁止先否定再转折的立靶表达")
    if _TECHNICAL_COMMENTARY.search(text):
        raise ValueError("讲解含后台技术话语，请只讲具体棋子、攻防与走法后果")


async def plan_commentary(client, catalog: dict) -> dict:
    """LLM composes from verified fact cards, never supplies tactical free text."""
    for cards in catalog.values():
        for text in cards.values():
            validate_spoken_commentary(text)
    example = {"nodes": [{"ply": ply, "segments": [{"fact": stage, "transition": ""}
                  for stage in STAGES]} for ply in KEY_PLIES]}
    messages = [
        Message("system", COMMENTARY_SYSTEM_PROMPT),
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
    for cards in catalog.values():
        for text in cards.values():
            validate_spoken_commentary(text)
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
              "commentary_mode": composition["mode"],
              "description": "古谱完整主线 · 吃马、进炮与弃车攻杀 · 关键变化推演"}
    paragraphs, events = script["paragraphs"], script["events"]
    def speak(key, title, text, chapter=None):
        validate_spoken_commentary(title)
        validate_spoken_commentary(text)
        paragraphs.append({"id": key, "title": title, "text": text})
        events.append({"id": key, "type": "speak", "paragraph": key,
                       **({"chapter": chapter} if chapter else {})})
    def branch(key, ply, moves, label, return_ply):
        events.append({"id": f"{key}-position", "type": "set_position", "mainline_ply": ply-1, "branch": label})
        for i, move in enumerate(moves):
            events.append({"id": f"{key}-m{i+1}", "type": "move", "move": move})
        events.append({"id": f"{key}-restore", "type": "set_position", "mainline_ply": return_ply})
        speak(f"{key}-return", "回到古谱", "好，再接着看古谱。")
    speak("famous-intro", "认识这份古谱", "今天看橘中秘里的弃马十三着。红方先让黑炮吃马，后面又送出一辆车，这盘棋究竟是怎么攻起来的？咱们顺着古谱往下看，遇到关键地方，也看看黑方有没有别的走法。", "古谱开场")
    plans = {n["ply"]: n for n in composition["plan"]["nodes"]}
    nodes = {n["ply"]: n for n in evidence["nodes"]}
    for ply, move in enumerate(source["mainline"], 1):
        events.append({"id": f"famous-m{ply}", "type": "move", "move": move})
        if ply == 14:
            speak("retreat-error", "退马并非最强防守", "黑方选择退马，这一下，红方的机会就大了。前面吃马以后，黑方本来还有补防的余地，走到这里却没有抓住。别看最后红方攻得漂亮，黑方也有过把局面守好的机会。")
        if ply not in plans:
            continue
        node = nodes[ply]
        for index, segment in enumerate(plans[ply]["segments"]):
            stage = segment["fact"]
            speak(f"node-{ply}-{stage}", {12: ["这匹马能吃吗", "先补防还是先吃马", "换个走法看看", "吃子之前看防守"],
                         13: ["少了马，急着追吗", "借对方的炮架", "回车追炮会怎样", "别只盯着追回子"],
                         19: ["这辆车也敢送", "黑方一定要吃吗", "吃与不吃的两条路", "车炮接上进攻"]}[ply][index],
                  segment["transition"] + catalog[str(ply)][stage],
                  {12: "吃马之前", 13: "进炮还是追炮", 19: "弃车以后的进攻"}[ply] if index == 0 else None)
            if stage == "variation":
                if ply == 12:
                    branch("defense", ply, node["expert"]["lines"][0]["pv"][:6], "黑方先补防", ply)
                elif ply == 13:
                    branch("chase", ply, node["candidates"]["a1a0"]["lines"][0]["pv"][:6], "回车追炮", ply)
                else:
                    refusal = node["sacrifice"]["declined"]["lines"][0]["pv"][:5]
                    branch("decline", ply, [move] + refusal, "拒绝弃车", ply)
                    branch("accept", ply, source["mainline"][ply-1:], "古谱接受弃车至将死", ply)
    speak("famous-finish", "复盘结束", "炮八平五，黑方已经无处可解，这盘古谱就到这里了。回头看，进炮时没有急着追回那匹马，进车时又舍得把车送出去，都是为了接下来的进攻。轮到自己下棋，遇到眼前的得失，不妨再多问一句：下一步，对方会怎么应？", "终局总结")
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
