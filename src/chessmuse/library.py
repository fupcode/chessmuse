"""Local game library, cache reuse and generation progress for web and CLI."""

import json
import re
import wave
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from cchess import FULL_INIT_FEN
from mutagen import MutagenError

from chessmuse.demo import DATA, compile_demo
from chessmuse.llm import DeepSeekClient, DeepSeekSettings
from chessmuse.narration import generate_announcements, read_announcements
from chessmuse.tts import DEFAULT_RATE, DEFAULT_VOICE, prepare_neural_audio

MAX_MOVES = 200


def make_game(source: dict, game_id: str) -> dict:
    """The import format is deliberately small: title and ICCS moves."""
    if not isinstance(source, dict):
        raise ValueError("棋谱必须是 JSON 对象")
    moves = source.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
        raise ValueError(f"moves 必须包含1到{MAX_MOVES}个半回合走法")
    if any(not isinstance(m, str) or not re.fullmatch(r"[a-i][0-9][a-i][0-9]", m) for m in moves):
        raise ValueError("走法必须为 ICCS 格式，例如 h2e2")
    if source.get("initial_fen", FULL_INIT_FEN) != FULL_INIT_FEN:
        raise ValueError("当前导入仅支持标准初始局面，请省略 initial_fen")
    title = source.get("title", "导入的棋谱")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ValueError("title 必须为1到100个字符")
    script = {
        "id": game_id, "title": title.strip(),
        "description": "导入棋谱 · LLM 走子短句 · 无引擎评价",
        "initial_fen": FULL_INIT_FEN, "mainline": moves,
        "paragraphs": [],
        "events": [{"id": f"m{i + 1}", "type": "move", "move": move,
                    **({"chapter": "开局"} if i == 0 else {})} for i, move in enumerate(moves)],
    }
    try:
        compile_demo(script, require_audio=False, require_narration=False)
    except (ValueError, AttributeError, IndexError, TypeError) as error:
        raise ValueError(f"棋谱包含非法走法：{error}") from None
    return script


def examples() -> dict[str, dict]:
    horses = make_game({"title": "双方起马 · 四步示例", "moves": ["h0g2", "h9g7", "b0c2", "b9c7"]}, "horse-opening")
    horses["description"] = "简短示例 · 每步自然讲述 · 无引擎评价"
    result = {horses["id"]: horses}
    reviewed = DATA / "famous-review.json"
    if reviewed.exists():
        famous = json.loads(reviewed.read_text(encoding="utf-8"))
        # Put the finished substantive review first in the web catalog.
        result = {famous["id"]: famous, **result}
    return result


class GameLibrary:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path.cwd()).resolve()
        self.cache = self.root / ".cache" / "games"
        self.audio = self.root / "audio"
        self.builtins = examples()

    def example(self, game_id: str) -> dict:
        if game_id not in self.builtins:
            raise ValueError("示例棋谱不存在")
        return deepcopy(self.builtins[game_id])

    def game_dir(self, game_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9-]+", game_id):
            raise ValueError("棋谱标识无效")
        return self.cache / game_id

    def import_game(self, source: dict) -> dict:
        # Reuse an identical imported game without content hashes.
        candidate = make_game(source, "import-" + uuid4().hex)
        if self.cache.exists():
            for record in self.cache.glob("import-*/script.json"):
                try:
                    saved = json.loads(record.read_text(encoding="utf-8"))
                    if all(saved.get(key) == candidate[key] for key in ("title", "initial_fen", "mainline")):
                        return saved
                except (OSError, ValueError, TypeError):
                    pass
        return candidate

    def narration_path(self, game_id: str) -> Path:
        local = self.game_dir(game_id) / "narration.json"
        if not local.exists() and game_id == "opening-v1":
            return DATA / "narration.json"
        if not local.exists() and game_id == "qima-13":
            return DATA / "famous-narration.json"
        return local

    async def prepare(self, script: dict, *, force: bool = False, force_narration: bool = False,
                      voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE,
                      env_file: str = ".env", progress=None) -> dict:
        report = progress or (lambda value, message: None)
        report(5, "校验棋谱与还原局面")
        plan = compile_demo(script, require_audio=False, require_narration=False)
        directory = self.game_dir(script["id"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
        narration = self.narration_path(script["id"])
        try:
            if force_narration:
                raise ValueError("Regeneration requested")
            read_announcements(narration, plan["narration_inputs"])
            report(35, "复用已有走子短句")
        except (OSError, ValueError, TypeError):
            narration = directory / "narration.json"
            report(10, "连接模型生成走子短句")
            async with DeepSeekClient(DeepSeekSettings.from_env(env_file)) as client:
                await generate_announcements(client, plan["narration_inputs"], narration,
                                             progress=lambda part, message: report(10 + int(part * 25), message))
        audio_dir = self.audio / script["id"]
        plan = compile_demo(script, require_audio=False, narration_path=narration, audio_dir=audio_dir)
        report(36, "检查本地语音缓存")
        if not force:
            try:
                manifest = json.loads((audio_dir / "audio.json").read_text(encoding="utf-8-sig"))
                if (manifest.get("provider") != "edge-tts" or manifest.get("voice") != voice
                        or manifest.get("rate") != rate):
                    raise ValueError("音频配置变化")
                result = compile_demo(script, narration_path=narration, audio_dir=audio_dir)
            except (OSError, ValueError, KeyError, TypeError, MutagenError, wave.Error, EOFError):
                pass
            else:
                report(97, "已核验并复用全部语音缓存")
                report(100, "导入完成，可以播放")
                return result
        await prepare_neural_audio(audio_dir, plan["paragraphs"], voice, rate, force=force,
                                   progress=lambda part, message: report(36 + int(part * 59), message))
        report(97, "校验音频与动画同步")
        result = compile_demo(script, narration_path=narration, audio_dir=audio_dir)
        report(100, "导入完成，可以播放")
        return result
