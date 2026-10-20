"""适配器共用的 HTTP 抓取：指数退避重试 + urllib→curl 回退。

macOS Python.org 框架 Python 缺系统钥匙串证书，urllib 必然 SSL 失败，
此时回退 curl；网络瞬断（curl exit 56 等）靠重试覆盖。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

RETRIES = 5
BACKOFF = 2.0


def _curl(url: str, dump_headers: bool) -> str:
    args = ["curl", "-sfL"]
    if dump_headers:
        args += ["-D", "-"]
    args.append(url)
    out = subprocess.run(args, capture_output=True, check=True, timeout=90)
    return out.stdout.decode("utf-8", "replace")


def fetch_text(url: str, dump_headers: bool = False, retries: int = RETRIES) -> str:
    """GET 文本。dump_headers=True 时返回 '响应头\\r\\n\\r\\n正文'（重定向只留最后一组）。"""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "capnet-demo"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
                if dump_headers:
                    head = "".join(f"{k}: {v}\r\n" for k, v in resp.headers.items())
                    return head + "\r\n" + body
                return body
        except Exception as e:
            last = e
            try:
                return _curl(url, dump_headers)
            except Exception as e2:
                last = e2
        time.sleep(BACKOFF ** attempt)
    raise last  # type: ignore[misc]


def fetch_json(url: str, retries: int = RETRIES) -> dict:
    return json.loads(fetch_text(url, retries=retries))
