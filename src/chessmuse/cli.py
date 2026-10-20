"""Local demo server and reproducible voice-cache commands."""

import argparse
import asyncio
import json
import subprocess
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from cchess import FULL_INIT_FEN

from chessmuse.demo import compile_demo
from chessmuse.library import GameLibrary
from chessmuse.server import DemoHandler, ImportJobs
from chessmuse.llm import ConfigurationError, DeepSeekClient, DeepSeekSettings, LLMError, Message
from chessmuse.narration import generate_announcements
from chessmuse.tts import DEFAULT_RATE, DEFAULT_VOICE, prepare_neural_audio, speech_text
from chessmuse.engine import EngineError, Pikafish, position_after
from chessmuse.analysis import analyze_cases, analyze_game
from chessmuse.game import play

PACKAGE = Path(__file__).parent


async def check_llm(env_file: str):
    settings = DeepSeekSettings.from_env(env_file)
    async with DeepSeekClient(settings) as client:
        completion = await client.complete([
            Message("user", '连通性测试：请只返回 JSON 对象 {"status":"ok"}。'),
        ], json_mode=True, max_tokens=64)
    if completion.data != {"status": "ok"}:
        raise LLMError("DeepSeek 连通但返回内容不符合检查要求", kind="response")
    print(f"DeepSeek 连接成功：{settings.model}，"
          f"输入 {completion.usage.prompt_tokens} tokens，"
          f"输出 {completion.usage.completion_tokens} tokens。")



def main():
    parser = argparse.ArgumentParser(description="棋妙：本地象棋复盘演示")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地演示")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("validate", help="验证主线、分支和语音缓存")
    llm_check = sub.add_parser("llm-check", help="发送一条短请求，检查 DeepSeek 配置与连通性")
    llm_check.add_argument("--env-file", default=".env", help="环境文件路径，默认读取当前目录的 .env")
    narrate = sub.add_parser("prepare-narration", help="用 LLM 将带上下文的走子事实压缩成自然短句")
    narrate.add_argument("--env-file", default=".env")
    prepare = sub.add_parser("prepare-audio", help="生成中文解说缓存（默认晓晓自然女声）")
    prepare.add_argument("--provider", choices=["edge", "windows"], default="edge")
    prepare.add_argument("--voice", default=DEFAULT_VOICE, help="在线声音名称")
    prepare.add_argument("--rate", default=DEFAULT_RATE, help="在线语速，默认 +8%%（108%%），如 --rate=+8%%")
    prepare.add_argument("--example", default="opening-v1", help="示例棋谱ID")
    prepare.add_argument("--force", action="store_true", help="强制重新合成所有音频")
    narrate.add_argument("--example", default="opening-v1")
    all_prepare = sub.add_parser("prepare-example", help="准备示例短句与语音，已有缓存时复用")
    all_prepare.add_argument("--example", default="opening-v1")
    all_prepare.add_argument("--force", action="store_true", help="重新生成短句和语音")
    all_prepare.add_argument("--env-file", default=".env")
    for command, help_text in (("analyze-position", "专家分析单个局面及指定候选"),
                               ("analyze-game", "全局浅扫棋谱并深度复查少量局面"),
                               ("analyze-suite", "复现内置专家分析小样本实验")):
        analyze = sub.add_parser(command, help=help_text)
        analyze.add_argument("--engine", required=True, help="Pikafish 可执行文件路径")
        analyze.add_argument("--eval-file", help="NNUE 权重；默认与引擎同目录的 pikafish.nnue")
        analyze.add_argument("--timeout", type=float, default=60, help="每次等待引擎的秒数上限")
        analyze.add_argument("--output", default=f".cache/analysis/{command}.json")
        analyze.add_argument("--force", action="store_true", help="忽略已有搜索缓存")
        if command == "analyze-position":
            analyze.add_argument("--fen", required=True)
            analyze.add_argument("--moves", nargs="*", default=[], help="从初始FEN到目标局面的合法历史")
            analyze.add_argument("--depth", type=int, default=10)
            analyze.add_argument("--multipv", type=int, default=3)
            analyze.add_argument("--candidate", action="append", default=[], help="单独搜索的候选，可重复")
        elif command == "analyze-game":
            analyze.add_argument("--input", help="本地 JSON 棋谱，支持 moves 或 mainline")
            analyze.add_argument("--example", default="opening-v1")
            analyze.add_argument("--scan-depth", type=int, default=6)
            analyze.add_argument("--review-depth", type=int, default=10)
            analyze.add_argument("--max-reviews", type=int, default=3)
            analyze.add_argument("--review-ply", action="append", type=int, default=[])
        else:
            analyze.add_argument("--input", default=str(PACKAGE / "data" / "analysis-cases.json"))
            analyze.add_argument("--depth", type=int, default=8)
    args = parser.parse_args()
    if args.command.startswith("analyze-"):
        try:
            with Pikafish(args.engine, eval_file=args.eval_file, timeout=args.timeout) as engine:
                if args.command == "analyze-position":
                    # Validate all requested candidates before the first search.
                    fen = position_after(args.fen, args.moves)
                    for move in args.candidate:
                        play(fen, move)
                    result = {"best": engine.search(args.fen, args.moves, depth=args.depth,
                              multipv=args.multipv, force=args.force), "candidates": {}}
                    for move in dict.fromkeys(args.candidate):
                        result["candidates"][move] = engine.search(args.fen, args.moves,
                            depth=args.depth, searchmoves=[move], force=args.force)
                    print(f"最佳走法：{result['best']['bestmove']}；指定候选：{len(result['candidates'])}")
                elif args.command == "analyze-game":
                    source = (json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input
                              else GameLibrary().example(args.example))
                    if not isinstance(source, dict):
                        raise ValueError("棋谱 JSON 必须为对象")
                    result = analyze_game(engine, source.get("initial_fen", FULL_INIT_FEN),
                        source.get("moves", source.get("mainline")), scan_depth=args.scan_depth,
                        review_depth=args.review_depth, max_reviews=args.max_reviews,
                        review_plies=args.review_ply, force=args.force, progress=lambda s: print(s, flush=True))
                    print(f"分析完成：{len(result['scan'])} 个半回合，{len(result['reviews'])} 个复查局面")
                else:
                    cases = json.loads(Path(args.input).read_text(encoding="utf-8"))
                    result = analyze_cases(engine, cases, depth=args.depth, force=args.force,
                                           progress=lambda s: print(s, flush=True))
                    print(f"实验完成：{len(result['cases'])} 个局面")
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"证据记录：{output}")
        except (EngineError, OSError, ValueError, TypeError) as error:
            parser.exit(1, f"专家分析失败：{error}\n")
        return
    library = GameLibrary()
    if args.command == "prepare-example":
        try:
            asyncio.run(library.prepare(library.example(args.example), force=args.force,
                force_narration=args.force, env_file=args.env_file,
                progress=lambda value, message: print(f"{value}% {message}", flush=True)))
        except (ConfigurationError, LLMError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.command == "prepare-narration":
        async def prepare_narration():
            script = compile_demo(library.example(args.example), require_audio=False, require_narration=False)
            async with DeepSeekClient(DeepSeekSettings.from_env(args.env_file)) as client:
                texts = await generate_announcements(client, script["narration_inputs"], library.game_dir(args.example) / "narration.json")
            for event_id, text in texts.items():
                print(f"{event_id}: {text}")
        try:
            asyncio.run(prepare_narration())
        except (ConfigurationError, LLMError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.command == "llm-check":
        try:
            asyncio.run(check_llm(args.env_file))
        except (ConfigurationError, LLMError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.command == "prepare-audio":
        script = compile_demo(library.example(args.example), require_audio=False,
                              narration_path=library.narration_path(args.example))
        audio_dir = library.audio / args.example
        if args.provider == "edge":
            asyncio.run(prepare_neural_audio(audio_dir, script["paragraphs"], args.voice, args.rate, force=args.force))
        else:
            for paragraph in script["paragraphs"]:
                paragraph["spoken_text"] = speech_text(paragraph["text"])
            staging_root = Path(".cache") / "tts"
            staging_root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="windows-", dir=staging_root) as temporary:
                script_path = Path(temporary).resolve() / "script.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
                subprocess.run([
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PACKAGE / "scripts" / "prepare-audio.ps1"),
                    "-DataDirectory", str(audio_dir.resolve()), "-ScriptPath", str(script_path),
                ], check=True)
    if args.command != "serve":
        example_id = getattr(args, "example", "opening-v1")
        demo = compile_demo(library.example(example_id), narration_path=library.narration_path(example_id),
                            audio_dir=library.audio / example_id)
        print(f"验证通过：{len(demo['mainline'])} 个主线半回合，"
              f"{len(demo['events'])} 个播放事件，{len(demo['paragraphs'])} 段语音。")
        return
    jobs = ImportJobs(library)
    handler = partial(DemoHandler, library=library, jobs=jobs)
    try:
        with ThreadingHTTPServer((args.host, args.port), handler) as server:
            print(f"棋妙已启动：http://{args.host}:{server.server_port}（Ctrl+C 停止）", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        jobs.close()
