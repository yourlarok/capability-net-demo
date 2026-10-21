# capnet：能力与工作流路由网络

一个能力社区，目标是让 agent 解决"别人已经解决过的问题"时更快。

- **能力卡是底子**：每个工具都写明调用方式与接入渠道（API 的鉴权方式和凭证申请入口、安装命令、下载地址、找人渠道）。
- **成熟 flow 是抓手**：复杂任务 → 别人走通的工作流（阶段/方法/交付物/验收）→ 每步推荐工具。
- **两层**：🏛 基石层是系统维护、评审过的卡和 flow，保持稳定；🌱/✅ 社区层是用户在使用中走出来的 flow，
  自动上架、升级、下架，不能覆盖基石。
- **隐式生长**：每次任务结束，agent 按对话中观察到的行为记录反馈（结果、纠正次数、跳过的阶段、选用的工具），
  用于工具评分、flow 评分和新 flow 注册；用户不需要做任何事。上报方式由用户**事先选定**：自动 / 每次确认 / 不上报，
  先清理私有信息再记录。上报与否不影响路由。

## 一句话安装

对你的 agent（Claude Code / Codex / Gemini CLI / Cursor / Windsurf / Claude Desktop……）说：

> 请阅读 https://raw.githubusercontent.com/yourlarok/capability-net-demo/v3-traces/INSTALL.md ，按里面的说明帮我安装 capnet。

agent 会自己完成：从 GitHub 源码包安装（`uv tool install`，没有 uv 就用 pip，不需要 git、不克隆仓库），
然后 `capnet setup` 探测本机所有客户端并写好 MCP 配置和使用规范，最后把报告给你。撤销：`capnet setup --remove`。

## 接入（任何 agent）

**完整步骤见 [docs/使用教程.md](docs/使用教程.md)。** 同一组 8 个工具 + 一份使用规范（`capnet/agent_rules.md`），四种接法：

- **本地 MCP（stdio）**：`python demo.py config <claude-code|codex|gemini-cli|cursor|windsurf|vscode|claude-desktop|cline> [--write]` 生成/合并配置。
- **远程 MCP（HTTP）**：`python demo.py serve --host 0.0.0.0` → 客户端填 `http://<host>:8765/mcp`（Claude.ai 连接器、Dify、Coze 等）。
- **REST + OpenAPI**：同一服务的 `/openapi.json` 可导入 GPTs Actions / Coze·Dify 插件；`POST /v1/tools/<name>`。
- **Function calling / 库**：`python demo.py tools --format openai|anthropic|gemini`，示例 `examples/function_calling.py`。
- 使用规范：`python demo.py rules`（放进 AGENTS.md / GEMINI.md / .cursor/rules / 系统提示；Claude Code 用 `.claude/skills/capnet`）。
- 社区：`finish_task` 记录隐式反馈；`capnet share auto|ask|off` 设上报方式；同一个 `serve` 端口兼任社区 hub（`GET /v1/flows`），
  客户端设 `CAPNET_HUB_URL` 后自动同步社区 flow。`capnet flows` 查看各 flow 的口碑，`promote <id>` 把社区 flow 评审升为基石。

---

# 附：打分公式

为 agent 能力注册表设计的打分公式 demo。要解决的问题：注册表几千张能力卡，
路由排序不能让"高曝光平庸卡"永远霸榜，也不能让"冷启动好卡"永远没有出头之日，
同时小样本数据不能被当成可信质量。

## 公式

```
Score = (wq·Q + wr·R + wc·C + β·U) × cost_fit × latency_fit × health
检索时 final = 语义相关度 × Score
```

| 分量 | 定义 | 作用 |
|------|------|------|
| Q 质量 | 验收成功率的 Wilson 95% 下界 | 小样本受罚：3/3 打不过 96/100 |
| R 认可度 | 认可率 + 评分均值的贝叶斯平滑（先验强度 20/5） | 无数据卡落向先验而非 0 或满分 |
| C 曝光效率 | 选中率（选中/曝光）的 Wilson 下界 | 曝光只做分母：遏制富者愈富 |
| U 探索项 | √(ln(总选中)/(卡选中+1))，系数 β=0.15 | UCB：新卡保底试错机会；分母用选中数而非曝光数——只被展示不被选中不算试错，否则新卡从未被调用就会耗尽探索预算 |
| 新鲜度 | 事件按 2^(-Δt/半衰期) 加权 | 防止吃老本 |
| 策略乘子 | cost_fit（超预算线性衰减）× latency_fit × health∈{0,1} | 硬约束不进加权项，走乘子 |

默认权重 wq=0.5, wr=0.2, wc=0.3, β=0.15，见 `capnet/scoring.py:Weights`。

## 样例数据埋的三种病理（data/）

- `img.megaimage`：曝光 5200、验收率仅 58% 的营销型 incumbent → 应下沉
- `img.pixelforge`：曝光 260、验收率 87% 的本地专精卡 → 应上浮
- `img.spritewave`：曝光 6 的新入驻隐藏好卡（true_quality 0.93）→ 靠 UCB 获得机会
- `vid.unstable`：历史成绩极好但 healthy=false → 直接归零

## 运行（Python 3.11+，零依赖）

```bash
python3 demo.py import --cards data/cards.json --seeds data/telemetry_seed.json --db data/registry.db  # 建库
python3 demo.py search "给游戏画像素风精灵图" --db data/registry.db --markdown   # store 快速通道
python3 demo.py stats --db data/registry.db && python3 demo.py approve image.gen --db data/registry.db
python3 demo.py rank --domain image.sprite --explain     # 看分量分解（内存模式）
python3 demo.py search "给游戏画像素风精灵图" --explain    # 语义检索 + 精排
python3 demo.py search "给游戏画像素风精灵图" --options    # 输出给用户选择的选项卡 JSON
python3 demo.py search "给游戏画像素风精灵图" --markdown   # Markdown 卡片（粘贴进回复，对话窗口内渲染）
python3 demo.py board "画精灵图" "背景音乐" "游戏音效" --open  # 多缺口选择板：终端卡片 + HTML
python3 demo.py search "配一段游戏主题曲" --max-cost 0.05  # 成本乘子生效
python3 demo.py simulate --domain image.sprite --rounds 600 --explain  # 排名演化
python3 demo.py plan "产品宣传视频播放量很低，帮我出一个完善的视频改进计划"  # 任务 → 工作流 → 逐阶段方法 + 工具填槽
python3 demo.py eval                                      # 批量领域评测（dev / holdout / blind 三套，见 data/eval/）
python3 demo.py induce traces.jsonl                       # 从执行轨迹归纳工作流草稿
python3 demo.py summarize draft.json                       # 执行记录 → 清理后的预览 + preview_id（不上传）
python3 demo.py submit <preview_id> --yes                 # 用户同意后上传（--no 删除预览）
python3 demo.py hub serve | refresh | stats               # 汇总端
python3 demo.py mcp                                       # stdio MCP server
python3 -m unittest discover -s tests -v                 # 测试（含 2 万条目性能压测）
```

`simulate` 模拟 planner 按 softmax(final) 选卡、按卡的隐藏 true_quality 抽样验收结果，
观察几百轮后 spritewave（冷启动好卡）是否爬上榜首、megaimage 是否让位。

## 结构（v2）

```
capnet/cards.py      schema v2：应用卡 + 能力面，拍平为路由条目（v1 自动包装兼容）
capnet/store.py      SQLite 持久化注册表：倒排索引召回、遥测衰减聚合、草稿评审队列（4 万条目标毫秒级）
capnet/domains.py    领域分类 + 用户领域画像（有界亲和乘子，可检查可关闭）
capnet/tokens.py     分词与字段权重（内存/持久化两路共用）
capnet/scoring.py    公式引擎（Wilson / 贝叶斯 / UCB / 衰减 / 乘子）
capnet/search.py     漏斗：健康+领域过滤 → 字段加权语义召回 → 打分精排 → as_options 选项卡
capnet/render.py     渲染：Markdown 卡片（对话内）/ 终端 ANSI 卡片 / 自包含 HTML 选择板
capnet/telemetry.py  事件日志 + 半衰期衰减聚合
capnet/simulate.py   planner 行为模拟器
capnet/workflows.py  工作流：阶段（方法/交付物/验收）+ 槽位；检索 = 任务类型门 + 实义证据门 + 字段加权相关度
capnet/registry.py   注册表门面：cards + pro_tools + catalog/*.json + 遥测 + 工作流一次加载
capnet/planner.py    规划器：任务 → 工作流 → 填槽（主 domain 优先、按 app 去重）→ Markdown 计划 + 轨迹模板
capnet/traces.py     执行轨迹：credit（拆遥测）/ induce（按独立贡献者归纳工作流）/ redact（脱敏）
capnet/evaluate.py   批量领域评测：命中@1、域外拒识、槽位覆盖、自动化率
capnet/privacy.py    隐私清理规则 + 私有词表 + 残留风险提示
capnet/hub.py        两段式同意上传（指纹校验）+ 汇总端 ingest/refresh/HTTP 服务
capnet/mcp_server.py stdio MCP server（零依赖 JSON-RPC）
.claude/skills/capnet/SKILL.md  agent 使用规范：何时路由、人工工具边界、如何汇总思路、必须征得同意
capnet/gaps.py       能力缺口日志：特征词无命中的查询落盘聚合，驱动策展/适配器扩容
capnet/adapters/mcp_registry.py  MCP 官方 registry 适配器（产出草稿卡待评审，可直写 --db）
capnet/adapters/huggingface.py   HuggingFace Hub 适配器（pipeline_tag 权威分域 + 下载量门槛，
                                 命中映射直接 approved；popularity.normalized=源内分位数）
data/cards.json      种子卡（v1 格式，自动包装）
data/pro_tools.json  策展专业工具目录（PS/AE/Suno/剪映/Blender 等 gui/web 卡，
                     补齐采集层天然不可见的人工能力；popularity 标 curated-estimate）
data/user_profile.json 用户领域画像（短剧/网文/视频解说/游戏）
data/workflows/      10 条工作流：3 条实践归纳（游戏/短剧/解说）+ 7 条行业策展（营销视频改进/品牌/账号增长/电商/直播/课程/播客）
data/catalog/        业务向能力目录（营销数据/投放/电商/教育/发布 + 人工服务卡）
data/eval/           评测集与报告：queries(dev) / holdout / blind + sample_marketing_plan.md
data/registry_drafts/ 适配器产出的草稿卡
docs/superpowers/specs/2026-10-20-capnet-v2-design.md  v2 设计 spec
```

入选判据（用户裁决）：**领域内切实存在且具有应用价值即可成卡**，accessibility（api/cli/mcp/gui/manual）是属性不是门槛；gui/manual 卡只可推荐人工使用。

冷启动认可度先验：卡带源外部流行度（`popularity.users` + `normalized` 源内分位数）时，打分侧以 `0.2 + 0.7×normalized` 作为认可度的贝叶斯先验（见 `search._popularity_prior`）；无数据（`None`）走全局默认 0.5。真实遥测累积后先验被快速覆盖——外部流行度只解决冷启动排序，不压制后续社区反馈。

