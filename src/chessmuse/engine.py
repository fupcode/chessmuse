"""Pikafish UCI boundary: bounded searches, legal PVs and inspectable evidence."""

import json
import math
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from cchess import ChessBoard

from chessmuse.game import move_facts, play


class EngineError(RuntimeError):
    """Engine startup, protocol, timeout or unverified evidence."""


def validate_fen(fen: str) -> str:
    """Validate syntax before feeding cchess's permissive FEN reader or UCI."""
    if not isinstance(fen, str):
        raise ValueError("FEN 必须为字符串")
    fields = fen.split()
    if len(fields) not in (2, 6) or fields[1] not in ("w", "b"):
        raise ValueError("FEN 需要棋盘、w/b 行棋方，以及可选的完整计数字段")
    rows = fields[0].split("/")
    if len(rows) != 10 or any(
        not re.fullmatch(r"[1-9rnbakcpRNBAKCP]+", row)
        or sum(int(c) if c.isdigit() else 1 for c in row) != 9 for row in rows
    ):
        raise ValueError("FEN 棋盘必须为10行9列，使用 rnbakcp 棋子编码")
    if fields[0].count("k") != 1 or fields[0].count("K") != 1:
        raise ValueError("分析局面必须各有一个将帅")
    if len(fields) == 6 and (
        fields[2:4] != ["-", "-"] or not fields[4].isdigit()
        or not fields[5].isdigit() or int(fields[5]) < 1
    ):
        raise ValueError("FEN 计数字段无效")
    return " ".join(fields) if len(fields) == 6 else " ".join(fields) + " - - 0 1"


def position_after(initial_fen: str, moves: list[str]) -> str:
    fen = " ".join(validate_fen(initial_fen).split()[:2])
    for move in moves:
        fen, _ = play(fen, move)
    return fen


def parse_info(line: str) -> dict | None:
    """Keep mate distinct from cp, and bounds distinct from exact estimates."""
    tokens = line.split()
    if tokens[:1] != ["info"] or "string" in tokens or "pv" not in tokens or "score" not in tokens:
        return None
    try:
        score_at = tokens.index("score")
        kind, value = tokens[score_at + 1:score_at + 3]
        if kind not in ("cp", "mate"):
            return None
        return {
            "depth": int(tokens[tokens.index("depth") + 1]),
            "multipv": int(tokens[tokens.index("multipv") + 1]) if "multipv" in tokens else 1,
            "score": {"kind": kind, "value": int(value), "bound":
                      "lower" if "lowerbound" in tokens else "upper" if "upperbound" in tokens else "exact"},
            "pv": tokens[tokens.index("pv") + 1:],
        }
    except (ValueError, IndexError):
        return None


def verified_line(fen: str, line: dict) -> dict:
    events = []
    for move in line["pv"]:
        try:
            after, notation = play(fen, move)
        except ValueError as error:
            raise EngineError(f"引擎返回非法PV走法 {move}：{error}") from error
        facts = move_facts(fen, move, notation)
        events.append({**facts, "after_fen": after})
        fen = after
    if not events:
        raise EngineError("引擎变化缺少合法走法")
    return {**line, "events": events}


def file_identity(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class Pikafish:
    """One synchronous UCI session; do not share across request threads.

    Each search clears engine state and uses one thread for repeatable depth
    experiments. A reader thread allows Windows pipes to have a real deadline.
    """

    def __init__(self, executable: str | Path, *, eval_file: str | Path | None = None,
                 timeout: float = 60, cache_dir: Path | None = Path(".cache/analysis/searches")):
        self.executable = Path(executable).resolve()
        self.eval_file = Path(eval_file).resolve() if eval_file else self.executable.parent / "pikafish.nnue"
        self.timeout = timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout 必须为有限正数")
        self.cache_dir = cache_dir
        self.process = None
        self.transcript = []
        self.inbox = queue.Queue()
        self.identity = {}

    def __enter__(self):
        try:
            self.identity = {"executable": file_identity(self.executable), "network": file_identity(self.eval_file),
                             "options": {"Threads": 1, "Hash": 32, "EvalFile": str(self.eval_file)}}
            self.process = subprocess.Popen(
                [str(self.executable)], cwd=self.executable.parent, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            self._send("uci")
            greeting = self._until("uciok")
            names = [s.removeprefix("id name ") for s in greeting if s.startswith("id name ")]
            if not names or "Pikafish" not in names[0]:
                raise EngineError("当前仅支持 Pikafish UCI 引擎")
            self.identity["name"] = names[0]
            for name, value in self.identity["options"].items():
                self._send(f"setoption name {name} value {value}")
            self._send("isready")
            self._until("readyok")
            self.handshake = list(self.transcript)
            return self
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.inbox.put(line.rstrip())
        finally:
            self.inbox.put(None)

    def _send(self, command: str):
        if "\n" in command or "\r" in command:
            raise ValueError("UCI 命令不能包含换行")
        self.transcript.append({"direction": "send", "line": command})
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise EngineError("引擎输入管道已关闭") from error

    def _until(self, prefix: str) -> list[str]:
        deadline = time.monotonic() + self.timeout
        lines = []
        while True:
            if time.monotonic() >= deadline:
                self.close()
                raise EngineError(f"等待 {prefix} 超时（{self.timeout}秒），已终止引擎")
            try:
                line = self.inbox.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise EngineError(f"等待 {prefix} 超时（{self.timeout}秒），已终止引擎") from None
            if line is None:
                raise EngineError("引擎提前退出：" + " | ".join(lines[-4:]))
            self.transcript.append({"direction": "receive", "line": line})
            lines.append(line)
            if "CRITICAL ERROR" in line or "ERROR:" in line:
                raise EngineError(line)
            if line == prefix or line.startswith(prefix + " "):
                return lines

    def search(self, initial_fen: str, moves=(), *, depth: int = 8, multipv: int = 1,
               searchmoves=(), force: bool = False) -> dict:
        if not 1 <= depth <= 64 or not 1 <= multipv <= 8:
            raise ValueError("depth 范围1到64，multipv 范围1到8")
        initial_fen = validate_fen(initial_fen)
        moves, searchmoves = list(moves), list(searchmoves)
        fen = position_after(initial_fen, moves)
        for move in searchmoves:
            play(fen, move)
        request = {"initial_fen": initial_fen, "moves": moves, "depth": depth,
                   "multipv": multipv, "searchmoves": searchmoves}
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            if not force:
                for path in self.cache_dir.glob("*.json"):
                    try:
                        saved = json.loads(path.read_text(encoding="utf-8"))
                        if saved.get("schema_version") == 1 and saved.get("engine") == self.identity and saved.get("request") == request:
                            checked = self._result(fen, request, saved["transcript"])
                            return {**checked, "cache_hit": True, "cache_file": str(path)}
                    except (OSError, ValueError, KeyError, TypeError, EngineError):
                        continue
        self.transcript = []
        self._send("ucinewgame")
        self._send(f"setoption name MultiPV value {multipv}")
        self._send("isready")
        self._until("readyok")
        self._send("position fen " + initial_fen + (" moves " + " ".join(moves) if moves else ""))
        self._send(f"go depth {depth}" + (" searchmoves " + " ".join(searchmoves) if searchmoves else ""))
        self._until("bestmove")
        result = self._result(fen, request, self.transcript)
        if self.cache_dir is not None:
            path = self.cache_dir / (uuid4().hex + ".json")
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["cache_file"] = str(path)
        return {**result, "cache_hit": False}

    def _result(self, fen: str, request: dict, transcript: list) -> dict:
        latest = {}
        bestmove = None
        ended = False
        for entry in transcript:
            if entry["direction"] != "receive":
                continue
            line = entry["line"]
            info = parse_info(line)
            if info:
                latest[info["multipv"]] = info
            if line.startswith("bestmove "):
                token = line.split()[1]
                bestmove = None if token in ("(none)", "0000") else token
                ended = True
        if not ended:
            raise EngineError("分析记录缺少 bestmove 结束标记")
        if bestmove is None:
            if not ChessBoard(fen).no_moves():
                raise EngineError("非终局却没有最佳走法")
            lines = []
        else:
            play(fen, bestmove)
            if request["searchmoves"] and bestmove not in request["searchmoves"]:
                raise EngineError("引擎未遵循指定候选限制")
            lines = [verified_line(fen, latest[key]) for key in sorted(latest)]
            if not lines or lines[0]["pv"][0] != bestmove:
                raise EngineError("最佳走法与主要变化不一致")
            if any(line["depth"] < request["depth"] for line in lines):
                raise EngineError("引擎未完成请求深度")
            if request["searchmoves"] and any(line["pv"][0] not in request["searchmoves"] for line in lines):
                raise EngineError("变化超出指定候选限制")
        return {"schema_version": 1, "engine": self.identity, "request": request, "fen": fen,
                "score_pov": "side_to_move", "bestmove": bestmove, "terminal": bestmove is None,
                "lines": lines, "handshake": self.handshake, "transcript": list(transcript)}

    def close(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.stdin.write("quit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=3)
        if hasattr(self, "reader"):
            self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()

    def __exit__(self, *args):
        self.close()
