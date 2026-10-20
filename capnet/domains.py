"""领域分类与用户领域画像：个性化路由层。

画像显式声明用户的行业与领域权重，路由时以有界乘子倾斜
（1 + boost_max * weight），不掩盖相关度错误，可检查、可关闭。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

TOP_DOMAINS = [
    "image", "video", "audio", "text", "3d",
    "devtools", "data", "browser", "office", "cloud", "ai", "misc",
]


@dataclass
class UserProfile:
    industries: list[str] = field(default_factory=list)
    domain_weights: dict[str, float] = field(default_factory=dict)
    boost_max: float = 0.15

    def affinity(self, domain: str) -> float:
        """领域亲和乘子，∈ [1.0, 1+boost_max]。未命中画像 = 1.0（不倾斜）。"""
        w = self.domain_weights.get(domain)
        if w is None:
            w = self.domain_weights.get(domain.split(".")[0] + ".*", 0.0)
        return 1.0 + self.boost_max * max(0.0, min(1.0, w))


def load_profile(path: str | None) -> UserProfile:
    if not path or not os.path.exists(path):
        return UserProfile()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return UserProfile(
        industries=data.get("industries", []),
        domain_weights=data.get("domain_weights", {}),
        boost_max=data.get("boost_max", 0.15),
    )
