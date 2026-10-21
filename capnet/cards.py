"""能力卡模型与注册表加载。

schema v2：应用卡（app）+ 能力面（capabilities[]），粒度对齐应用市场；
v1 扁平卡由 loader 自动包装为单能力应用卡，旧数据不丢。
路由视图 = (app, capability) 拍平后的 entry。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ACCESSIBILITY = ("api", "cli", "mcp", "gui", "manual")
AUTO_CALLABLE = ("api", "cli", "mcp")
# v1 transport 是传输方式而非可访问性：http/local 端点 agent 都能直接调
V1_TRANSPORT = {"http": "api", "local": "cli"}

V2_REQUIRED = {"app_id", "name", "capabilities"}
CAP_REQUIRED = {"domain", "intents", "outputs", "accessibility"}
V1_REQUIRED = {"id", "name", "domain", "description", "intents", "outputs", "cost_per_call", "latency"}


@dataclass
class Capability:
    domain: str
    intents: list[str]
    outputs: list[str]
    accessibility: str
    inputs: list[str] = field(default_factory=list)
    cost_per_call: float = 0.0
    latency: str = "medium"
    extras: dict = field(default_factory=dict)  # 透传源特有字段（如 mcp_remotes），不参与打分


@dataclass
class AppCard:
    app_id: str
    name: str
    capabilities: list[Capability]
    vendor: str = ""
    description: str = ""
    platforms: list[str] = field(default_factory=list)
    pricing: dict = field(default_factory=dict)
    popularity: dict = field(default_factory=dict)
    value_evidence: str = ""
    healthy: bool = True
    markets: list[str] = field(default_factory=list)  # 适用市场：cn / global …；空 = 不限
    review_status: str = "approved"  # draft = 适配器产出待评审，approved 才可入主注册表
    true_quality: float = 0.5  # 仅供模拟器抽样，路由侧不可见


@dataclass
class Card:
    """拍平后的路由条目：一个 (app, capability) 对。"""
    id: str
    name: str
    domain: str
    description: str
    intents: list[str]
    outputs: list[str]
    cost_per_call: float
    latency: str
    inputs: list[str] = field(default_factory=list)
    transport: str = "http"
    vendor: str = ""
    healthy: bool = True
    true_quality: float = 0.5
    accessibility: str = "api"
    app_id: str = ""
    extras: dict = field(default_factory=dict)  # 源特有字段（mcp_remotes / hf_pipeline 等）
    popularity: dict = field(default_factory=dict)  # 源外部流行度，作冷启动认可度先验
    markets: list[str] = field(default_factory=list)

    @property
    def searchable_text(self) -> str:
        return " ".join([self.name, self.domain, self.description, " ".join(self.intents)])

    @property
    def auto_callable(self) -> bool:
        return self.accessibility in AUTO_CALLABLE


def _entry_id(app_id: str, cap: Capability, total: int) -> str:
    return app_id if total == 1 else f"{app_id}#{cap.domain}"


def flatten(app: AppCard) -> list[Card]:
    out = []
    total = len(app.capabilities)
    for cap in app.capabilities:
        out.append(Card(
            id=_entry_id(app.app_id, cap, total),
            name=app.name,
            domain=cap.domain,
            description=app.description,
            intents=cap.intents,
            outputs=cap.outputs,
            inputs=cap.inputs,
            cost_per_call=cap.cost_per_call,
            latency=cap.latency,
            transport=cap.accessibility,
            vendor=app.vendor,
            healthy=app.healthy,
            true_quality=app.true_quality,
            accessibility=cap.accessibility,
            app_id=app.app_id,
            extras=cap.extras,
            popularity=app.popularity,
            markets=app.markets,
        ))
    return out


def _parse_v2(item: dict) -> AppCard:
    caps = []
    for j, c in enumerate(item["capabilities"]):
        missing = CAP_REQUIRED - set(c)
        if missing:
            raise ValueError(f"{item['app_id']}.capabilities[{j}] 缺字段: {sorted(missing)}")
        if c["accessibility"] not in ACCESSIBILITY:
            raise ValueError(f"{item['app_id']}.capabilities[{j}] 非法 accessibility: {c['accessibility']}")
        known_cap = {f_ for f_ in Capability.__dataclass_fields__ if f_ != "extras"}
        cap_kwargs = {k: v for k, v in c.items() if k in known_cap}
        cap_kwargs["extras"] = {k: v for k, v in c.items() if k not in known_cap and k != "extras"}
        caps.append(Capability(**cap_kwargs))
    known = {f_ for f_ in AppCard.__dataclass_fields__}
    extra = {k: v for k, v in item.items() if k in known and k != "capabilities"}
    return AppCard(capabilities=caps, **extra)


def _wrap_v1(item: dict) -> AppCard:
    missing = V1_REQUIRED - set(item)
    if missing:
        raise ValueError(f"v1 卡 {item.get('id', '?')} 缺字段: {sorted(missing)}")
    return AppCard(
        app_id=item["id"],
        name=item["name"],
        vendor=item.get("vendor", ""),
        description=item.get("description", ""),
        platforms=[],
        pricing={"model": "unknown", "cost_per_call": item.get("cost_per_call", 0.0)},
        popularity={"source": "seed", "normalized": 0.0},
        value_evidence="v1 种子卡",
        healthy=item.get("healthy", True),
        true_quality=item.get("true_quality", 0.5),
        capabilities=[Capability(
            domain=item["domain"], intents=item["intents"], outputs=item["outputs"],
            inputs=item.get("inputs", []),
            accessibility=V1_TRANSPORT.get(item.get("transport", "api"), item.get("transport", "api")),
            cost_per_call=item.get("cost_per_call", 0.0), latency=item.get("latency", "medium"),
        )],
    )


def parse_app(item: dict) -> AppCard:
    """单条记录 → AppCard，自动识别 v1/v2。"""
    if "capabilities" in item:
        return _parse_v2(item)
    return _wrap_v1(item)


def load_apps(path: str) -> list[AppCard]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [parse_app(item) for item in raw]


def load_cards(path: str) -> list[Card]:
    """加载注册表并拍平为路由条目（兼容 v1/v2）。"""
    out: list[Card] = []
    for app in load_apps(path):
        out.extend(flatten(app))
    return out


def by_id(cards: list[Card]) -> dict[str, Card]:
    return {c.id: c for c in cards}
