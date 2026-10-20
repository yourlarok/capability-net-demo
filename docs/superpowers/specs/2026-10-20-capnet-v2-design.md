# capnet v2：应用级能力注册表 + 实践归纳工作流

日期：2026-10-20
状态：已批准（用户口头确认开工，含两处修正）

## 背景

v0 demo（`~/capability-net-demo`）验证了打分公式（Wilson 质量 + 贝叶斯认可 + Wilson CTR + UCB 探索 + 策略乘子）与可选择性交互。v2 把粒度从"扁平工具"升级为"应用市场级应用卡"，并引入工作流层。

## 用户的两处修正（本 spec 的硬约束）

1. **能力入选判据**：不要求 MCP/API。"领域内切实存在且具有应用价值"即可成为卡。`accessibility` 是属性不是门槛：
   - `api / cli / mcp`：agent 可自动调用；
   - `gui`：只能推荐给用户人工操作；
   - `manual`：人工服务/方法论类能力（如"专业配音演员""调色师"），同样只推荐。
   - 路由时 gui/manual 卡可进选项卡，但必须标注"人工使用"，agent 不得假装能调用。
2. **工作流来源**：主来源是"工作完成度的总结"——从已有 skills 链抽象、从与用户协作过程中归纳；n8n/Zapier 类模板库降为次要的参考来源。每条工作流必须记录 `source.kind`（practice-summary / conversation-induction / template-import）与可追溯出处。

## 卡 schema v2

```json
{
  "app_id": "vendor.app-slug",
  "name": "...", "vendor": "...", "description": "...",
  "platforms": ["web", "api", "desktop", "plugin"],
  "pricing": {"model": "free|freemium|paid", "notes": "..."},
  "popularity": {"source": "g2|github|taaft|mcp-official|estimate", "users": null,
                 "reviews": null, "normalized": 0.0},
  "value_evidence": "入选理由：领域内切实存在且有应用价值的证据",
  "capabilities": [{
    "domain": "video.text2video", "intents": ["..."],
    "inputs": ["text"], "outputs": ["mp4"],
    "accessibility": "api|cli|mcp|gui|manual",
    "cost_per_call": 0.5, "latency": "fast|medium|slow"
  }],
  "healthy": true
}
```

- 路由视图 = `(app, capability)` 拍平，entry id = `app_id#domain`；遥测与打分按 entry 维度累积。
- `popularity.normalized`：按源归一化（源内分位数），禁止跨源裸比。门槛按源定义（如 GitHub ≥1k star、G2 ≥50 评论、目录站热度前 20%）。
- v1 扁平卡由 loader 自动包装为单 capability 应用卡，旧数据不丢。

## 源适配器（Phase A 落地 2 个真的）

`capnet/adapters/mcp_registry.py`：拉取 MCP 官方 registry（`https://registry.modelcontextprotocol.io/v0/servers`，公开只读）→ 映射为草稿卡（accessibility=mcp，领域用关键词表初分，intents 待 LLM 富化，`value_evidence` 标注"registry 收录"）→ 直写 `--db` 或输出 `data/registry_drafts/mcp.jsonl`。草稿卡须经评审才入主注册表。

`capnet/adapters/huggingface.py`：按 pipeline_tag 拉取 HF Hub 模型（按下载量降序 + Link 头游标分页），下载量 ≥1000 为入选门槛。pipeline_tag 是作者声明 + 平台校验的权威分类，命中 `DOMAIN_MAP`（47 个标签）的直接 approved，未映射落 misc 草稿。`popularity`={source:hf, users:downloads, reviews:likes, normalized:同标签源内分位数}。

打分侧接线：`search._popularity_prior` 把 `popularity.normalized` 映射为认可度冷启动先验 `0.2+0.7×normalized`（无数据走默认 0.5），贝叶斯强度 20 保证真实遥测累积后覆盖先验。extras/popularity 随 entry 落库（store 含旧库 ALTER 迁移）。

## 工作流 schema v1

```json
{
  "workflow_id": "short-drama-episode",
  "name": "短剧单集生产",
  "industry": "content/short-drama",
  "source": {"kind": "practice-summary", "derived_from": "short-drama-* skills"},
  "stages": [{
    "id": "keyframe", "name": "冻结关键帧",
    "slots": [{
      "slot_id": "image.keyframe", "domain": "image.gen",
      "original_tool": "（原实践所用工具）",
      "replacement_query": "domain:image.gen outputs:png",
      "constraints": {"style_consistency_with": ["storyboard"]}
    }]
  }],
  "completion_signals": ["成片通过 review"]
}
```

- 槽位 `constraints` 记录链式一致性要求（不只是 IO 类型），这是换卡时的硬检查项。
- 归纳机制（skill 层）：任务完成后回顾本次过程，若出现可复用多阶段模式，产出工作流草稿交用户确认后入库；与用户沟通中识别到用户内含流程时同样归纳。两条触发都写进 capnet skill。

## 种子工作流（从用户已有 skills 抽象，practice-summary）

1. `game-dev`：概念 → 世界设计 → 资产（image.sprite / audio.music / audio.sfx 槽位）→ 构建 → QA
2. `short-drama-episode`：剧本 → 资产 → 分镜 → 关键帧(image) → 图生视频 → TTS → 合成
3. `video-recap`：视频理解 → 解说策划 → 剪辑 → 配音 → 合成

## 本次实现范围

- schema v2 + loader/validator + v1 数据迁移；search 走拍平视图，打分公式不变；
- MCP registry + HuggingFace 双适配器 + 单元测试（fixture 测解析，live 冒烟手动跑）；popularity→认可度冷启动先验接线；
- workflows.py + 3 条种子工作流 + 校验；
- capnet skill 文档更新（含工作流归纳触发）；
- 非目标：双轨打分、市场反馈闭环、embedding 召回（后续阶段）。
