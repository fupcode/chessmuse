import asyncio
import json
import time
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chessmuse.library import GameLibrary, make_game
from chessmuse.server import DemoHandler, ImportJobs


@pytest.mark.parametrize("source", [None, [], {}, {"moves": []}, {"moves": ["a0a9"]},
    {"moves": ["h9g7"]}, {"moves": ["../../../.env"]},
    {"moves": ["h2e2"], "initial_fen": "custom"}, {"moves": ["h2e2"], "title": ""}])
def test_invalid_imports_fail_before_generation(source):
    with pytest.raises(ValueError):
        make_game(source, "test-game")


def test_import_format_and_identical_import_reuse(tmp_path):
    library = GameLibrary(tmp_path)
    source = {"title": "测试棋谱", "moves": ["h2e2", "h9g7"]}
    script = library.import_game(source)
    folder = library.game_dir(script["id"])
    folder.mkdir(parents=True)
    (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
    assert library.import_game(source)["id"] == script["id"]
    assert len(script["events"]) == 2


def test_sample_preparation_reuses_audio_without_llm_credentials(demo_audio, monkeypatch):
    library = GameLibrary(demo_audio)
    def unexpected(*args, **kwargs):
        pytest.fail("valid cached example must not call cloud services")
    monkeypatch.setattr("chessmuse.library.DeepSeekSettings.from_env", unexpected)
    monkeypatch.setattr("chessmuse.tts.edge_tts.Communicate", unexpected)
    values = []
    result = asyncio.run(library.prepare(library.example("opening-v1"), progress=lambda value, text: values.append(value)))
    assert values == sorted(values)
    assert values[-1] == 100
    assert len(result["paragraphs"]) == 15
    assert all(p["audio"].startswith("/audio/opening-v1/") for p in result["paragraphs"])


def test_http_import_returns_real_job_progress_and_errors(tmp_path, monkeypatch):
    library = GameLibrary(tmp_path)

    async def prepare(script, *, progress):
        progress(20, "生成短句")
        await asyncio.sleep(.04)
        progress(80, "准备语音")
        return {"id": script["id"], "events": script["events"]}

    monkeypatch.setattr(library, "prepare", prepare)
    jobs = ImportJobs(library)
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(DemoHandler, library=library, jobs=jobs)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def post(payload, **headers):
            return urlopen(Request(base + "/api/import", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **headers}))
        try:
            with urlopen(base + "/api/examples") as response:
                assert len(json.load(response)["examples"]) == 2
            with urlopen(base + "/api/examples/horse-opening/download") as response:
                source = json.load(response)
                assert source["moves"] == ["h0g2", "h9g7", "b0c2", "b9c7"]
            with post({"game": source}) as response:
                assert response.status == 202
                job_id = json.load(response)["job_id"]
            deadline = time.monotonic() + 2
            while True:
                with urlopen(base + "/api/jobs/" + job_id) as response:
                    status = json.load(response)
                if status["status"] == "complete":
                    break
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert status["progress"] == 100
            assert len(status["result"]["events"]) == 4
            with pytest.raises(HTTPError) as error:
                post({"game": {"moves": ["a0a9"]}})
            assert error.value.code == 400
            with pytest.raises(HTTPError) as error:
                post({"example_id": "opening-v1"}, Origin="https://other.example")
            assert error.value.code == 403
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/audio/opening-v1/../../.env")
            assert error.value.code == 404
        finally:
            server.shutdown()
            thread.join()
            jobs.close()


def test_failed_generation_reports_failure_and_never_completes(tmp_path, monkeypatch):
    library = GameLibrary(tmp_path)
    async def fail(script, **kwargs):
        raise ValueError("缺少可用配置")
    monkeypatch.setattr(library, "prepare", fail)
    jobs = ImportJobs(library)
    try:
        job_id = jobs.start(library.example("horse-opening"))
        jobs.executor.shutdown(wait=True)
        job = jobs.get(job_id)
        assert job["status"] == "error"
        assert "result" not in job
        assert job["progress"] < 100
    finally:
        jobs.close()
