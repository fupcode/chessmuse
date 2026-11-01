import json
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chessmuse.cli import DemoHandler
from chessmuse.demo import compile_demo
from chessmuse.library import GameLibrary
from chessmuse.server import ImportJobs, MAX_UPLOAD_BYTES


def test_demo_and_cached_audio_are_served_without_exposing_source(demo_audio):
    handler = partial(DemoHandler, demo=json.dumps(compile_demo()).encode(), library=GameLibrary(demo_audio))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/") as response:
                assert response.geturl() == base + "/chessmuse/"
                html = response.read()
                assert b'lang="zh-CN"' in html
                assert b'<base href="/chessmuse/">' in html
            for method in ("GET", "HEAD"):
                for path in ("/", "/chessmuse", "/?from=home"):
                    connection = HTTPConnection("127.0.0.1", server.server_port)
                    try:
                        connection.request(method, path)
                        response = connection.getresponse()
                        assert response.status == 308
                        assert response.getheader("Location") == "/chessmuse/" + ("?from=home" if "?" in path else "")
                        assert response.read() == b""
                    finally:
                        connection.close()
            for asset in ("style.css", "app.js", "player.js", "import.js", "motion.js"):
                with urlopen(base + "/chessmuse/" + asset + "?v=test") as response:
                    assert response.status == 200
                    assert response.read()
            with urlopen(base + "/chessmuse/api/demo") as response:
                demo = json.load(response)
                assert len(demo["events"]) == 17
            with urlopen(base + demo["paragraphs"][0]["audio"]) as response:
                assert response.headers["Content-Type"] in {"audio/wav", "audio/mpeg"}
                assert len(response.read()) > 1000
            for path in ["/chessmuse/audio/../demo.json", "/chessmuse/demo.py", "/demo.py",
                         "/api/examples", "/style.css", "/chessmuse-other/"]:
                with pytest.raises(HTTPError) as error:
                    urlopen(base + path)
                assert error.value.code == 404
        finally:
            server.shutdown()
            thread.join()


def test_audio_missing_from_manifest_or_unknown_game_404s(demo_audio):
    handler = partial(DemoHandler, library=GameLibrary(demo_audio))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/chessmuse/audio/unknown-game/one.mp3",
                         "/chessmuse/audio/opening-v1/not-in-manifest.mp3",
                         "/chessmuse/audio/opening-v1/../../style.css"):
                with pytest.raises(HTTPError) as error:
                    urlopen(base + path)
                assert error.value.code == 404
        finally:
            server.shutdown()
            thread.join()


def test_import_rejects_oversized_invalid_and_cross_origin_payloads(demo_audio):
    library = GameLibrary(demo_audio)
    jobs = ImportJobs(library)
    handler = partial(DemoHandler, library=library, jobs=jobs)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            oversize = json.dumps({"example_id": "cao-wang-2025"}).encode()
            oversize += b"x" * (MAX_UPLOAD_BYTES + 1 - len(oversize))
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + "/chessmuse/api/import", data=oversize,
                                headers={"Content-Type": "application/json"}))
            assert error.value.code == 413
            for payload, status in (
                    (b"not json", 400), (b'"example_id"', 400),
                    (b'{"example_id": 7}', 400),
                    (b'{"game": {"moves": []}}', 400),
                    (b'{"game": {"moves": ["h2e2", "bogus"]}}', 400)):
                connection = HTTPConnection("127.0.0.1", server.server_port)
                try:
                    connection.request("POST", "/chessmuse/api/import", body=payload,
                                      headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    assert response.status == status, response.read()
                finally:
                    connection.close()
            connection = HTTPConnection("127.0.0.1", server.server_port)
            try:
                connection.request("POST", "/chessmuse/api/import",
                                   body=b'{"example_id": "cao-wang-2025"}',
                                   headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
                response = connection.getresponse()
                assert response.status == 403
            finally:
                connection.close()
        finally:
            server.shutdown()
            thread.join()
            jobs.close()
