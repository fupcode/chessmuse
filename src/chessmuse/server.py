"""Small local HTTP API with real background import progress."""

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from chessmuse.library import GameLibrary
from chessmuse.llm import ConfigurationError, LLMError
from chessmuse.urls import BASE_URL

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 256 * 1024


class ImportJobs:
    def __init__(self, library: GameLibrary):
        self.library = library
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chessmuse-import")
        self.lock = Lock()
        self.jobs = {}

    def start(self, script: dict) -> str:
        with self.lock:
            for job_id, job in self.jobs.items():
                if job["game_id"] == script["id"] and job["status"] in {"queued", "processing"}:
                    return job_id
            if len(self.jobs) >= 50:
                for old_id in list(self.jobs):
                    if self.jobs[old_id]["status"] in {"complete", "error"}:
                        del self.jobs[old_id]
                        break
                else:
                    raise ValueError("任务较多，请稍后再试")
            job_id = uuid4().hex
            self.jobs[job_id] = {"game_id": script["id"], "status": "queued", "progress": 0, "message": "等待处理"}
        self.executor.submit(self._run, job_id, script)
        return job_id

    def _update(self, job_id, **fields):
        with self.lock:
            self.jobs[job_id].update(fields)

    def _run(self, job_id, script):
        self._update(job_id, status="processing")
        try:
            result = asyncio.run(self.library.prepare(script, progress=lambda value, message: self._update(
                job_id, progress=value, message=message)))
            self._update(job_id, status="complete", progress=100, message="导入完成，可以播放", result=result)
        except (ConfigurationError, LLMError, ValueError) as error:
            self._update(job_id, status="error", message=str(error))
        except Exception:
            self._update(job_id, status="error", message="棋谱处理失败，请检查网络连接后重试；本地已有缓存会保留")

    def get(self, job_id):
        with self.lock:
            return deepcopy(self.jobs.get(job_id))

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, library: GameLibrary | None = None,
                 jobs: ImportJobs | None = None, demo: bytes | None = None, **kwargs):
        self.library = library or GameLibrary()
        self.jobs = jobs
        self.demo = demo
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def json_response(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def app_path(self, path):
        route = urlsplit(path).path
        return "/" + route[len(BASE_URL):] if route.startswith(BASE_URL) else None

    def redirect_entry(self):
        request = urlsplit(self.path)
        if request.path not in {"/", BASE_URL.rstrip("/")}:
            return False
        self.send_response(308)
        self.send_header("Location", BASE_URL + ("?" + request.query if request.query else ""))
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_HEAD(self):
        if self.redirect_entry():
            return
        if self.app_path(self.path) is None:
            self.send_error(404)
            return
        super().do_HEAD()

    def do_GET(self):
        if self.redirect_entry():
            return
        path = self.app_path(self.path)
        if path is None:
            self.send_error(404)
            return
        if path == "/api/examples":
            self.json_response(200, {"examples": [{"id": game["id"], "title": game["title"],
                "description": game["description"]} for game in self.library.builtins.values()]})
        elif path.startswith("/api/examples/") and path.endswith("/download"):
            game_id = path.removeprefix("/api/examples/").removesuffix("/download")
            try:
                game = self.library.example(game_id)
            except ValueError:
                self.json_response(404, {"error": "示例不存在"})
                return
            payload = json.dumps({"title": game["title"], "moves": game["mainline"],
                                  **({"source": game["source"]} if game.get("source") else {})}, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{game_id}.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path.startswith("/api/jobs/"):
            job = self.jobs.get(path.removeprefix("/api/jobs/")) if self.jobs else None
            self.json_response(200 if job else 404, job or {"error": "任务不存在"})
        elif path == "/api/demo":
            if self.demo:
                self.json_response(200, json.loads(self.demo))
            else:
                self.json_response(409, {"error": "请先选择示例或导入棋谱"})
        else:
            super().do_GET()

    def do_POST(self):
        if self.app_path(self.path) != "/api/import" or not self.jobs:
            self.json_response(404, {"error": "接口不存在"})
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.json_response(403, {"error": "请从当前演示页面导入棋谱"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请使用 JSON 棋谱")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_UPLOAD_BYTES:
                self.json_response(413, {"error": "棋谱文件过大，最大256KB"})
                return
            request = json.loads(self.rfile.read(size).decode("utf-8-sig"))
            if not isinstance(request, dict):
                raise ValueError("导入参数必须是 JSON 对象")
            if "example_id" in request:
                if not isinstance(request["example_id"], str):
                    raise ValueError("示例标识无效")
                script = self.library.example(request["example_id"])
            else:
                script = self.library.import_game(request.get("game"))
            job_id = self.jobs.start(script)
        except (ValueError, UnicodeError) as error:
            self.json_response(400, {"error": str(error)})
            return
        self.json_response(202, {"job_id": job_id})

    def translate_path(self, path):
        route = self.app_path(path)
        if route is None:
            return str(STATIC / "__missing__")
        if route.startswith("/audio/"):
            parts = route.removeprefix("/audio/").split("/")
            if len(parts) == 2 and re.fullmatch(r"[a-zA-Z0-9-]+", parts[0]) and re.fullmatch(r"[a-zA-Z0-9_.-]+\.(mp3|wav)", parts[1]):
                folder = self.library.audio / parts[0]
                try:
                    manifest = json.loads((folder / "audio.json").read_text(encoding="utf-8-sig"))
                    if parts[1] in {p["file"] for p in manifest["paragraphs"]}:
                        return str(folder / parts[1])
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            return str(STATIC / "__missing__")
        return super().translate_path(route)

    def list_directory(self, path):
        self.send_error(404)
        return None
