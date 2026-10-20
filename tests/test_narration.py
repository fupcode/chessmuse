import asyncio
import json
from copy import deepcopy

import pytest

from chessmuse.demo import compile_demo
from chessmuse.llm import Completion, LLMError, TokenUsage
from chessmuse.narration import generate_announcements, read_announcements, validate_announcements


def move_input():
    return {"id": "test-move", "facts": {
        "side": "红方", "action": "马二进三", "piece": "马",
        "capture": None, "gives_check": False, "verified_labels": [],
    }, "history": [{"side": "黑方", "action": "卒三进一"}], "branch": None}


@pytest.mark.parametrize("text", [
    "红方，跳马", "红方跳马。", "黑方跳马", "红方跳马将军", "红方退马",
    "红方马二进三", "红方跳马绝杀", "红方平炮杀马", "红方卧槽马", "红方跳马捉车",
])
def test_invalid_short_calls_are_rejected(text):
    with pytest.raises(ValueError):
        validate_announcements([move_input()], {"announcements": [{"id": "test-move", "text": text}]})


def test_verified_check_and_capture_allow_short_actions():
    item = move_input()
    item["facts"]["gives_check"] = True
    assert validate_announcements([item], {"announcements": [{"id": "test-move", "text": "红方跳马将军"}]})
    item["facts"].update(piece="炮", action="炮二平五", gives_check=False, capture="马")
    assert validate_announcements([item], {"announcements": [{"id": "test-move", "text": "红方平炮杀马"}]})


class FakeClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        data = next(self.outputs)
        return Completion(text=json.dumps(data, ensure_ascii=False), data=data, model="test-model",
                          request_id="test-request", usage=TokenUsage(10, 5, 15))


def test_generate_corrects_invalid_text_and_caches_exact_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inputs = [move_input()]
    client = FakeClient([
        {"announcements": [{"id": "test-move", "text": "红方，跳马"}]},
        {"announcements": [{"id": "test-move", "text": "红方跳马"}]},
    ])
    path = tmp_path / "narration.json"
    result = asyncio.run(generate_announcements(client, inputs, path))
    assert result == {"test-move": "红方跳马"}
    assert len(client.calls) == 2
    prompt = json.loads(client.calls[0][0][1].content)
    assert prompt["moves"][0]["history"] == inputs[0]["history"]
    assert read_announcements(path, inputs) == result
    changed = deepcopy(inputs)
    changed[0]["branch"] = "新分支"
    with pytest.raises(ValueError, match="上下文"):
        read_announcements(path, changed)


def test_invalid_llm_results_never_replace_previous_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "narration.json"
    path.write_text("previous cache", encoding="utf-8")
    client = FakeClient([{"announcements": []}, {"announcements": []}])
    with pytest.raises(LLMError, match="校验失败"):
        asyncio.run(generate_announcements(client, [move_input()], path))
    assert path.read_text(encoding="utf-8") == "previous cache"


def test_no_mechanical_fallback_without_llm_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("chessmuse.demo.read_announcements", lambda path, inputs: read_announcements(tmp_path / "missing.json", inputs))
    with pytest.raises(ValueError, match="prepare-narration"):
        compile_demo(require_audio=False)
