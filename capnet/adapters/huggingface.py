"""HuggingFace Hub 适配器：按 pipeline_tag 拉取模型，映射为 v2 能力卡。

API: GET https://huggingface.co/api/models?sort=downloads&direction=-1
          &pipeline_tag=<tag>&limit=N[&cursor=...]
分页走响应头 Link: <url>; rel="next"（cursor 不透明，直接取下一页 URL）。

与 MCP 适配器的差异：pipeline_tag 是作者声明 + HF 校验过的权威分类，
且自带下载量/点赞数——满足"切实存在且有应用价值"的入选判据，
命中 DOMAIN_MAP 的直接 approved，未映射的（other 等）落 draft。
popularity.normalized = 源内（同 pipeline_tag）下载量分位数，
在打分侧作为冷启动认可度先验（见 search._popularity_prior）。
"""
from __future__ import annotations

import argparse
import json
import os
import re

from .http_util import fetch_text

API = "https://huggingface.co/api/models"

# pipeline_tag -> (domain, inputs, outputs)
DOMAIN_MAP: dict[str, tuple[str, list[str], list[str]]] = {
    "text-to-image": ("image.gen", ["text"], ["image"]),
    "unconditional-image-generation": ("image.gen", [], ["image"]),
    "image-to-image": ("image.edit", ["image", "text"], ["image"]),
    "image-to-text": ("image.vision", ["image"], ["text"]),
    "image-classification": ("image.vision", ["image"], ["json"]),
    "object-detection": ("image.vision", ["image"], ["json"]),
    "image-segmentation": ("image.vision", ["image"], ["json"]),
    "depth-estimation": ("image.vision", ["image"], ["json"]),
    "image-feature-extraction": ("image.vision", ["image"], ["json"]),
    "keypoint-detection": ("image.vision", ["image"], ["json"]),
    "mask-generation": ("image.vision", ["image"], ["json"]),
    "zero-shot-image-classification": ("image.vision", ["image"], ["json"]),
    "visual-question-answering": ("image.vision", ["image", "text"], ["text"]),
    "text-to-video": ("video.gen", ["text"], ["video"]),
    "image-to-video": ("video.gen", ["image", "text"], ["video"]),
    "video-to-video": ("video.edit", ["video", "text"], ["video"]),
    "video-classification": ("video.vision", ["video"], ["json"]),
    "zero-shot-video-classification": ("video.vision", ["video"], ["json"]),
    "text-to-speech": ("audio.tts", ["text"], ["audio"]),
    "text-to-audio": ("audio.music", ["text"], ["audio"]),
    "automatic-speech-recognition": ("audio.asr", ["audio"], ["text"]),
    "audio-to-audio": ("audio.edit", ["audio"], ["audio"]),
    "audio-classification": ("audio.analysis", ["audio"], ["json"]),
    "voice-activity-detection": ("audio.analysis", ["audio"], ["json"]),
    "image-to-3d": ("3d.mesh", ["image"], ["mesh"]),
    "text-to-3d": ("3d.mesh", ["text"], ["mesh"]),
    "text-generation": ("ai.llm", ["text"], ["text"]),
    "text2text-generation": ("ai.llm", ["text"], ["text"]),
    "image-text-to-text": ("ai.vlm", ["image", "text"], ["text"]),
    "any-to-any": ("ai.multimodal", ["text"], ["text"]),
    "translation": ("text.translate", ["text"], ["text"]),
    "summarization": ("text.summary", ["text"], ["text"]),
    "question-answering": ("text.qa", ["text"], ["text"]),
    "fill-mask": ("text.nlp", ["text"], ["text"]),
    "token-classification": ("text.nlp", ["text"], ["json"]),
    "sentence-similarity": ("text.nlp", ["text"], ["json"]),
    "feature-extraction": ("ai.embedding", ["text"], ["json"]),
    "text-classification": ("text.nlp", ["text"], ["json"]),
    "multiple-choice": ("text.nlp", ["text"], ["text"]),
    "zero-shot-classification": ("text.nlp", ["text"], ["json"]),
    "table-question-answering": ("data.analysis", ["json", "text"], ["text"]),
    "document-question-answering": ("office.docs", ["json", "text"], ["text"]),
    "text-retrieval": ("data.search", ["text"], ["json"]),
    "time-series-forecasting": ("data.analysis", ["json"], ["json"]),
    "tabular-classification": ("data.ml", ["json"], ["json"]),
    "tabular-regression": ("data.ml", ["json"], ["json"]),
    "reinforcement-learning": ("ai.rl", ["json"], ["json"]),
    "robotics": ("ai.robotics", ["json"], ["json"]),
    "graph-ml": ("ai.ml", ["json"], ["json"]),
}

# 这些 tag 是格式/许可/部署元信息，不进检索面，进 extras
TAG_BLOCKED_PREFIX = ("license:", "arxiv:", "deploy:", "region:", "dataset:", "doi:")
TAG_BLOCKED_EXACT = {
    "transformers", "diffusers", "safetensors", "onnx", "gguf", "coreml", "jax",
    "pytorch", "tf", "tensorboard", "endpoints_compatible", "custom_code",
    "autotrain_compatible", "text-generation-inference", "adapter-transformers",
}
FORMATS = {"safetensors", "onnx", "gguf", "coreml", "openvino", "ckpt"}

MIN_DOWNLOADS_DEFAULT = 1000  # 用户裁决：排除基本无人使用的


def _intent_tokens(model: dict) -> list[str]:
    """检索面：名字尾部词 + pipeline_tag 词 + 有意义的标签（滤掉格式/许可噪音）。"""
    out: list[str] = []

    def add(w: str) -> None:
        w = w.strip().lower()
        if len(w) > 2 and w not in out:
            out.append(w)

    tag = model.get("pipeline_tag") or ""
    for w in tag.split("-"):
        add(w)
    tail = model.get("id", "").split("/")[-1]
    for w in re.split(r"[-_.]", tail):
        add(w)
    for t in model.get("tags", []):
        tl = t.lower()
        if tl.startswith(TAG_BLOCKED_PREFIX) or tl in TAG_BLOCKED_EXACT:
            continue
        for w in re.split(r"[-_\s]", tl):
            add(w)
    return out[:12]


def model_to_app(model: dict, normalized: float) -> dict:
    """纯函数：单条 HF 模型记录 -> v2 卡。normalized 为源内下载量分位数。"""
    mid = model.get("id") or model.get("modelId", "")
    tag = model.get("pipeline_tag") or "other"
    author = model.get("author") or (mid.split("/")[0] if "/" in mid else "")
    downloads = model.get("downloads") or 0
    likes = model.get("likes") or 0
    library = model.get("library_name") or ""
    gated = bool(model.get("gated"))
    mapped = DOMAIN_MAP.get(tag)
    domain, inputs, outputs = mapped if mapped else ("misc", ["text"], ["json"])
    formats = sorted(FORMATS & {t.lower() for t in model.get("tags", [])})
    desc = (f"HuggingFace {tag} 模型"
            + (f"（{library}）" if library else "")
            + f"，作者 {author}，下载 {downloads}，点赞 {likes}"
            + ("。需申请访问（gated）" if gated else ""))
    return {
        "app_id": f"hf.{mid}",
        "name": mid.split("/")[-1],
        "vendor": author,
        "description": desc,
        "platforms": ["api"],
        "pricing": {"model": "free"},
        "popularity": {"source": "hf", "users": downloads, "reviews": likes,
                       "normalized": round(normalized, 4)},
        "value_evidence": ("HF Hub 在册，pipeline_tag 权威分类；"
                           f"下载量 {downloads}（≥{MIN_DOWNLOADS_DEFAULT} 门槛）满足应用价值判据"),
        "review_status": "approved" if mapped else "draft",
        "healthy": True,
        "capabilities": [{
            "domain": domain,
            "intents": _intent_tokens(model),
            "inputs": inputs,
            "outputs": outputs,
            "accessibility": "api",
            "cost_per_call": 0.0,
            "latency": "medium",
            "hf_pipeline": tag,
            "hf_library": library,
            "hf_gated": gated,
            "hf_url": f"https://huggingface.co/{mid}",
            "hf_formats": formats,
        }],
    }


def percentile_ranks(models: list[dict]) -> dict[str, float]:
    """源内分位数：下载量在同批模型中的相对位置，顶部为 1.0。"""
    n = len(models)
    if n <= 1:
        return {m.get("id", ""): 1.0 for m in models}
    order = sorted(range(n), key=lambda i: models[i].get("downloads") or 0)
    out: dict[str, float] = {}
    for rank, i in enumerate(order):
        out[models[i].get("id", "")] = rank / (n - 1)
    return out


def _fetch_page(url: str) -> tuple[list[dict], str | None]:
    """返回 (本页模型, 下一页 URL)。带重试（http_util），Link 头取下一页。"""
    raw = fetch_text(url, dump_headers=True)
    head, _, body = raw.rpartition("\r\n\r\n")
    link = ""
    for line in head.splitlines():
        if line.lower().startswith("link:"):
            link = line.split(":", 1)[1].strip()
    return json.loads(body), _next_link(link)


def _next_link(header: str) -> str | None:
    m = re.search(r'<([^>]+)>\s*;\s*rel="next"', header or "")
    return m.group(1) if m else None


def iter_models(tag: str, limit: int = 100, min_downloads: int = MIN_DOWNLOADS_DEFAULT,
                per_tag: int = 1000, max_pages: int = 50):
    """按下载量降序遍历某 pipeline_tag 的模型，低于门槛或到量即停。"""
    url = f"{API}?sort=downloads&direction=-1&limit={limit}&pipeline_tag={tag}"
    seen = 0
    for _ in range(max_pages):
        if not url or seen >= per_tag:
            break
        page, url = _fetch_page(url)
        if not page:
            break
        for m in page:
            if (m.get("downloads") or 0) < min_downloads:
                return  # 已按下载量降序，后面的都更低
            yield m
            seen += 1
            if seen >= per_tag:
                break


def collect_tag(tag: str, min_downloads: int, per_tag: int) -> list[dict]:
    """拉单个标签并计算源内分位数。"""
    models = list(iter_models(tag, min_downloads=min_downloads, per_tag=per_tag))
    pct = percentile_ranks(models)
    return [model_to_app(m, pct.get(m.get("id", ""), 0.0)) for m in models]


def collect(tags: list[str], min_downloads: int, per_tag: int) -> list[dict]:
    apps: list[dict] = []
    for tag in tags:
        apps.extend(collect_tag(tag, min_downloads, per_tag))
        print(f"  {tag}: 累计 {len(apps)} 张", flush=True)
    return apps


def _done_path(db: str) -> str:
    return db + ".hf_done"


def _write_db(db: str, apps: list[dict]) -> int:
    from ..cards import parse_app
    from ..store import Store
    store = Store(db)
    approved = [parse_app(a) for a in apps if a["review_status"] == "approved"]
    n = store.upsert_apps(approved)
    for a in apps:
        if a["review_status"] != "approved":
            store.upsert_draft(parse_app(a))
            n += 1
    store.close()
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="拉取 HuggingFace 模型生成 v2 能力卡")
    p.add_argument("--tags", default=",".join(sorted(DOMAIN_MAP)),
                   help="逗号分隔的 pipeline_tag 列表，默认全部已映射标签")
    p.add_argument("--include-unmapped", action="store_true",
                   help="同时拉取未映射标签（落 misc 草稿）")
    p.add_argument("--min-downloads", type=int, default=MIN_DOWNLOADS_DEFAULT)
    p.add_argument("--per-tag", type=int, default=1000)
    p.add_argument("--out", default="data/registry_drafts/hf.jsonl")
    p.add_argument("--db", default=None, help="直写 SQLite 注册表")
    args = p.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.include_unmapped and "other" not in tags:
        tags.append("other")

    if args.db:
        from ..store import Store
        # 标签级断点：已完成标签记入侧车文件，崩溃后续跑自动跳过
        done: list[str] = []
        if os.path.exists(_done_path(args.db)):
            with open(_done_path(args.db), encoding="utf-8") as f:
                done = json.load(f)
            if done:
                print(f"从断点续拉：已完成 {len(done)} 个标签", flush=True)
        total = 0
        try:
            for tag in tags:
                if tag in done:
                    continue
                apps = collect_tag(tag, args.min_downloads, args.per_tag)
                total += _write_db(args.db, apps)
                done.append(tag)
                with open(_done_path(args.db), "w", encoding="utf-8") as f:
                    json.dump(done, f)
                print(f"  {tag}: {len(apps)} 张（累计写入 {total}）", flush=True)
        finally:
            store = Store(args.db)
            store.rebuild_idf()
            store.close()
        if os.path.exists(_done_path(args.db)):
            os.remove(_done_path(args.db))
        print(f"共写入 {total} 张卡 -> {args.db}")
        return

    apps = collect(tags, args.min_downloads, args.per_tag)
    n_approved = sum(1 for a in apps if a["review_status"] == "approved")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for a in apps:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    print(f"共 {len(apps)} 张卡（approved {n_approved} / draft {len(apps) - n_approved}）"
          f" -> {args.out}")


if __name__ == "__main__":
    main()
