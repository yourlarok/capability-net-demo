"""路径约定。

两种运行形态：
  仓库模式  git clone 后 `python demo.py ...`：只读数据与可写状态都在仓库 data/（维护者 / 自托管 hub）
  安装模式  `uvx --from git+... capnet` 或 pip 安装：只读数据随包发布在 capnet/_data/，
            可写状态（hub、草稿、缺口、评审入库的工作流）放 CAPNET_HOME（默认 ~/.capnet）
用户侧状态（安装 id、私有词表、待确认预览）两种形态都在 CAPNET_HOME。
"""
from __future__ import annotations

import os

_PKG = os.path.dirname(os.path.abspath(__file__))
PKG_DATA = os.path.join(_PKG, "_data")
REPO_DATA = os.path.join(os.path.dirname(_PKG), "data")
INSTALLED = os.path.isdir(PKG_DATA)
DATA = PKG_DATA if INSTALLED else REPO_DATA


def capnet_home() -> str:
    return os.environ.get("CAPNET_HOME") or os.path.join(os.path.expanduser("~"), ".capnet")


def state_dir() -> str:
    """可写状态的根：仓库模式为 data/，安装模式为 CAPNET_HOME。"""
    return capnet_home() if INSTALLED else REPO_DATA


def hub_dir() -> str:
    return os.environ.get("CAPNET_HUB_DIR") or os.path.join(state_dir(), "hub")


def drafts_dir() -> str:
    return os.environ.get("CAPNET_DRAFTS_DIR") or os.path.join(state_dir(), "workflows_drafts")


def gaps_path() -> str:
    return os.path.join(state_dir(), "gaps.jsonl")


def user_workflows_dir() -> str | None:
    """安装模式下评审入库的工作流目录（与随包工作流一起加载）；仓库模式直接写 data/workflows。"""
    return os.path.join(capnet_home(), "workflows") if INSTALLED else None
