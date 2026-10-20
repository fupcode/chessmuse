"""Local demo server and reproducible voice-cache commands."""

import argparse
import asyncio
import json
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from chessmuse.demo import DATA, compile_demo
from chessmuse.tts import DEFAULT_RATE, DEFAULT_VOICE, prepare_neural_audio

PACKAGE = Path(__file__).parent


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, demo: bytes, **kwargs):
        self.demo = demo
        super().__init__(*args, directory=str(PACKAGE / "static"), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/demo":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(self.demo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(self.demo)
        else:
            super().do_GET()

    def translate_path(self, path):
        # Allow only authored audio names; never expose arbitrary source files.
        route = urlsplit(path).path
        if route.startswith("/audio/"):
            name = route.removeprefix("/audio/")
            if name in {p["audio"].removeprefix("/audio/") for p in json.loads(self.demo)["paragraphs"]}:
                return str(DATA / "audio" / name)
            return str(PACKAGE / "static" / "__missing__")
        return super().translate_path(path)

    def list_directory(self, path):
        self.send_error(404)
        return None


def main():
    parser = argparse.ArgumentParser(description="棋妙：本地象棋复盘演示")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地演示")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("validate", help="验证主线、分支和语音缓存")
    prepare = sub.add_parser("prepare-audio", help="生成中文解说缓存（默认晓晓自然女声）")
    prepare.add_argument("--provider", choices=["edge", "windows"], default="edge")
    prepare.add_argument("--voice", default=DEFAULT_VOICE, help="在线声音名称")
    prepare.add_argument("--rate", default=DEFAULT_RATE, help="在线语速，如 --rate=-5%%")
    args = parser.parse_args()
    if args.command == "prepare-audio":
        script = compile_demo(require_audio=False)
        if args.provider == "edge":
            asyncio.run(prepare_neural_audio(DATA, script["paragraphs"], args.voice, args.rate))
        else:
            subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PACKAGE / "scripts" / "prepare-audio.ps1"),
                "-DataDirectory", str(DATA.resolve()),
            ], check=True)
    demo = compile_demo()
    if args.command != "serve":
        print(f"验证通过：{len(demo['mainline'])} 个主线半回合，"
              f"{len(demo['events'])} 个播放事件，{len(demo['paragraphs'])} 段语音。")
        return
    handler = partial(DemoHandler, demo=json.dumps(demo, ensure_ascii=False).encode())
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"棋妙已启动：http://{args.host}:{server.server_port}（Ctrl+C 停止）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
