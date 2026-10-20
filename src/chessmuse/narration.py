"""Contextual LLM move calls, separate from verified chess facts and audio."""

import json
import re
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from chessmuse.llm import DeepSeekClient, LLMError, Message

PROMPT_VERSION = 1
SYSTEM_PROMPT = """你是中国象棋主播。根据程序提供的每步事实与历史上下文，把正式棋谱压缩成自然的短行动句。
输入包含 id、facts（side 行棋方、action 正式四字走法、piece 行动棋子、capture 实际吃到的棋子、gives_check 是否将军、verified_labels 已核验的经典开局或战术名称）、history 和 branch。
要求：
1. 每句保留开头的红方或黑方，行动尽量2到6个字，整句4到12个汉字。不要逗号、句号、空格、数字或任何标点。
2. 不要机械朗读四字棋谱，不要保留几进几。可写红方当头炮、黑方跳马、红方起车、黑方进卒。左右方向不确定就省略。
3. 有将军事实时压缩成红方跳马将军等；没有将军事实不能说将军。verified_labels 包含屏风马时可写黑方屏风马，包含当头炮时可直接使用。
4. 平炮杀马只是在有对应吃马事实或已核验战术标签时的风格示例。不得从示例推断当前也有杀马、捉子、绝杀、妙手或优势。未提供的经典杀法不要猜。
5. 保留真实行动的棋子和方向，不把进炮说成平炮，不把出马说成出车。结合历史保持连贯，分支中的走法不要当成主线历史。
6. 输出 JSON 对象 {"announcements":[{"id":"事件id","text":"红方当头炮"}]}，每个输入id恰好一次，不要解释。
"""


def validate_announcements(inputs: list[dict], data: dict) -> dict[str, str]:
    records = data.get("announcements") if isinstance(data, dict) else None
    if not isinstance(records, list) or len(records) != len(inputs):
        raise ValueError("短句数量与走子不一致")
    expected = {item["id"]: item["facts"] for item in inputs}
    result = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record["id"] not in expected or record["id"] in result:
            raise ValueError("短句事件 ID 缺失、重复或不匹配")
        facts = expected[record["id"]]
        text = record.get("text")
        if not isinstance(text, str) or not re.fullmatch(r"[\u4e00-\u9fff]{4,12}", text):
            raise ValueError("短句应为4到12个汉字且不含标点或空格")
        if not text.startswith(facts["side"]) or ("黑方" if facts["side"] == "红方" else "红方") in text:
            raise ValueError("短句行棋方不匹配")
        if ("将军" in text) != facts["gives_check"]:
            raise ValueError("短句将军描述与事实不一致")
        action_text = text[2:]
        labels = facts["verified_labels"]
        if facts["piece"] not in action_text and action_text not in labels:
            raise ValueError("短句没有保留实际行动棋子")
        if action_text == facts["action"]:
            raise ValueError("请压缩行动而非照读正式棋谱")
        for direction in ("平", "退", "进"):
            if direction in action_text and direction not in facts["action"] and action_text not in labels:
                raise ValueError("短句行动方向不匹配")
        if any(word in text for word in ("绝杀", "将死", "妙手", "捉", "必胜", "得子", "弃子", "双将", "抽将")) and action_text not in labels:
            raise ValueError("短句包含未核验战术")
        if any(word in text for word in ("杀", "吃", "打", "兑", "换")) and action_text not in labels:
            capture = facts["capture"]
            if not capture or not any(word + capture in text for word in ("杀", "吃", "打", "兑", "换")):
                raise ValueError("短句吃子或杀子描述缺少证据")
        for label in ("当头炮", "屏风马", "卧槽马", "重炮", "铁门栓", "闷宫"):
            if label in text and label not in labels:
                raise ValueError("短句开局或杀法名称缺少核验")
        result[record["id"]] = text
    return result


def read_announcements(path: Path, inputs: list[dict]) -> dict[str, str]:
    if not path.exists():
        raise ValueError("缺少 LLM 短句缓存，请先运行 chessmuse prepare-narration")
    cache = json.loads(path.read_text(encoding="utf-8"))
    if cache.get("prompt_version") != PROMPT_VERSION or cache.get("inputs") != inputs:
        raise ValueError("走子或上下文已变化，请重新运行 chessmuse prepare-narration")
    return validate_announcements(inputs, cache)


async def generate_announcements(client: DeepSeekClient, inputs: list[dict], path: Path, *, progress=None) -> dict[str, str]:
    result, requests = {}, []
    for offset in range(0, len(inputs), 16):
        batch = inputs[offset:offset + 16]
        messages = [Message("system", SYSTEM_PROMPT), Message("user", json.dumps({"moves": batch}, ensure_ascii=False))]
        for attempt in range(2):
            completion = await client.complete(messages, json_mode=True, max_tokens=2048)
            requests.append({"id": completion.request_id, "model": completion.model, "usage": asdict(completion.usage)})
            try:
                result.update(validate_announcements(batch, completion.data))
                break
            except ValueError as error:
                if attempt:
                    raise LLMError(f"LLM 短句校验失败：{error}", kind="narration") from None
                messages.extend([Message("assistant", completion.text), Message("user", f"请修正全部短句并重新返回 JSON：{error}")])
        if progress:
            progress(min(offset + len(batch), len(inputs)) / len(inputs), "生成走子短句")
    cache = {
        "prompt_version": PROMPT_VERSION, "requests": requests,
        "inputs": inputs, "announcements": [{"id": key, "text": value} for key, value in result.items()],
    }
    staging_root = Path(".cache") / "narration"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{uuid4().hex}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    return result
