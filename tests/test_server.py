import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from chessmuse.cli import DemoHandler
from chessmuse.demo import compile_demo


def test_demo_and_cached_audio_are_served_without_exposing_source():
    handler = partial(DemoHandler, demo=json.dumps(compile_demo()).encode())
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/") as response:
                assert b'lang="zh-CN"' in response.read()
            with urlopen(base + "/api/demo") as response:
                demo = json.load(response)
                assert len(demo["events"]) == 17
            with urlopen(base + demo["paragraphs"][0]["audio"]) as response:
                assert response.headers["Content-Type"] in {"audio/wav", "audio/mpeg"}
                assert len(response.read()) > 1000
            for path in ["/audio/../demo.json", "/demo.py"]:
                with pytest.raises(HTTPError) as error:
                    urlopen(base + path)
                assert error.value.code == 404
        finally:
            server.shutdown()
            thread.join()
