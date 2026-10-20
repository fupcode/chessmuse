"""Two deterministic finite-depth models reusing ElephantEye's native rules."""

import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

from chessmuse.engine import EngineError, file_identity, validate_fen, verified_line
from chessmuse.game import move_facts, play

REVISION = "a3e6890386aa87ca4a184c4df8f9f13004b71565"
ROOT = Path(".cache/elephanteye")
EXECUTABLE = ROOT / ("intuition.exe" if sys.platform == "win32" else "intuition")
CPP = Path(__file__).parent / "scripts" / "intuition.cpp"
SOURCES = ["pregen.cpp", "position.cpp", "genmoves.cpp", "preeval.cpp", "evaluate.cpp"]
ALLOWLIST = ["LICENSE", "README.md", "base/base.h", "base/rc4prng.h", "base/x86asm.h"] + [
    "eleeye/" + name for name in SOURCES + ["pregen.h", "position.h", "preeval.h"]]
MODELS = {
    "material": "纯子力：车600、炮285、马270、仕相120、兵卒30；不含位置或静态交换延伸",
    "traditional": "ElephantEye传统评价：阶段位置表、将帅形状、牵制、车灵活性、马受阻",
}


def extract_sources(content: bytes, destination: Path):
    """Never extractall: unrelated legacy PHP/web/league modules stay in memory."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        prefix = f"eleeye-{REVISION}/"
        for name in ALLOWLIST:
            info = archive.getinfo(prefix + name)
            if info.file_size > 2_000_000:
                raise ValueError("上游源码文件超出预期大小")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))


def build_intuition(*, download=False) -> Path:
    source = ROOT / "source"
    if download:
        response = httpx.get(f"https://codeload.github.com/xqbase/eleeye/zip/{REVISION}",
                             trust_env=False, follow_redirects=True, timeout=60)
        response.raise_for_status()
        extract_sources(response.content, source)
    if not all((source / name).is_file() for name in ALLOWLIST):
        raise ValueError("缺少ElephantEye源码，请运行 prepare-intuition --download（仅提取白名单C++文件）")
    compiler = shutil.which("g++")
    if not compiler:
        raise ValueError("需要可用的g++编译器构建ElephantEye直觉模型")
    command = [compiler, "-DNDEBUG", "-O2", "-I", str(source / "eleeye"), "-o", str(EXECUTABLE), str(CPP)]
    command += [str(source / "eleeye" / name) for name in SOURCES]
    subprocess.run(command, check=True, timeout=120, capture_output=True)
    (ROOT / "build.json").write_text(json.dumps({"revision": REVISION,
        "compiler": compiler, "command": command, "harness": file_identity(CPP)}, indent=2), encoding="utf-8")
    return EXECUTABLE


def features(fen: str, move: str) -> dict:
    after, notation = play(fen, move)
    facts = move_facts(fen, move, notation)
    return {"capture": facts["capture"], "gives_check": facts["gives_check"],
            "action": facts["action"], "after_fen": after}


def rank_candidates(fen: str, *, model="material", depth=3, executable: Path = EXECUTABLE,
                    force=False, timeout=60) -> dict:
    fen = validate_fen(fen)
    if model not in MODELS or type(depth) is not int or depth not in (1, 3, 5):
        raise ValueError("直觉模型为material/traditional，实验深度为1、3、5")
    build = json.loads((executable.parent / "build.json").read_text(encoding="utf-8"))
    if build.get("harness") != file_identity(CPP) or build.get("revision") != REVISION:
        raise ValueError("直觉模型源码已变化，请运行 prepare-intuition 重新构建")
    identity = {"binary": file_identity(executable), "harness": file_identity(CPP), "upstream": REVISION}
    request = {"fen": fen, "model": model, "depth": depth, "identity": identity}
    cache = ROOT / "searches"
    cache.mkdir(parents=True, exist_ok=True)
    if not force:
        for path in cache.glob("*.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved["request"] == request:
                    return {**_validate(saved, fen), "cache_hit": True}
            except (OSError, ValueError, KeyError, TypeError, EngineError):
                continue
    result = subprocess.run([str(executable.resolve()), fen, str(depth), model],
                            capture_output=True, text=True, timeout=timeout, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    data = json.loads(result.stdout)
    record = _validate({"request": request, "raw": data}, fen)
    record.update({"description": MODELS[model], "score_unit": "model_units_not_pikafish_cp",
                   "score_pov": "side_to_move", "root_window": "full_for_each_candidate",
                   "search": "fixed_ply_alpha_beta_no_quiescence_no_extensions"})
    (cache / f"{uuid4().hex}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**record, "cache_hit": False}


def _validate(record: dict, fen: str) -> dict:
    candidates = []
    previous = float("inf")
    for item in record["raw"]["candidates"]:
        if item["score"] > previous or item["pv"][0] != item["move"]:
            raise ValueError("直觉候选排序或PV不一致")
        previous = item["score"]
        verified_line(fen, {"pv": item["pv"]})
        candidates.append({**item, "rank": len(candidates) + 1, "features": features(fen, item["move"])})
    if len({c["move"] for c in candidates}) != len(candidates):
        raise ValueError("直觉候选重复")
    return {**record, "candidates": candidates}
