"""Local demo server and reproducible voice-cache commands."""

import argparse
import asyncio
import json
import subprocess
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from chessmuse.demo import compile_demo
from chessmuse.library import GameLibrary
from chessmuse.server import DemoHandler, ImportJobs
from chessmuse.llm import ConfigurationError, DeepSeekClient, DeepSeekSettings, LLMError, Message
from chessmuse.narration import generate_announcements
from chessmuse.tts import DEFAULT_RATE, DEFAULT_VOICE, prepare_neural_audio, speech_text

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
    args = parser.parse_args()
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
