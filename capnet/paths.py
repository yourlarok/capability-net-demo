"""路径约定：用户侧状态（安装 id、私有词表、待确认预览）在 CAPNET_HOME，网络侧数据在仓库 data/。"""
from __future__ import annotations

import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def capnet_home() -> str:
    return os.environ.get("CAPNET_HOME") or os.path.join(os.path.expanduser("~"), ".capnet")


def hub_dir() -> str:
    return os.environ.get("CAPNET_HUB_DIR") or os.path.join(DATA, "hub")


def drafts_dir() -> str:
    return os.environ.get("CAPNET_DRAFTS_DIR") or os.path.join(DATA, "workflows_drafts")
