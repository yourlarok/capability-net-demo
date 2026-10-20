# capnet-demo：能力卡打分公式验证

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
capnet/workflows.py  工作流：阶段 DAG + 槽位抽象（实践归纳来源）
capnet/gaps.py       能力缺口日志：特征词无命中的查询落盘聚合，驱动策展/适配器扩容
capnet/adapters/mcp_registry.py  MCP 官方 registry 适配器（产出草稿卡待评审，可直写 --db）
capnet/adapters/huggingface.py   HuggingFace Hub 适配器（pipeline_tag 权威分域 + 下载量门槛，
                                 命中映射直接 approved；popularity.normalized=源内分位数）
data/cards.json      种子卡（v1 格式，自动包装）
data/pro_tools.json  策展专业工具目录（PS/AE/Suno/剪映/Blender 等 gui/web 卡，
                     补齐采集层天然不可见的人工能力；popularity 标 curated-estimate）
data/user_profile.json 用户领域画像（短剧/网文/视频解说/游戏）
data/workflows/      种子工作流：game-dev / short-drama-episode / video-recap
data/registry_drafts/ 适配器产出的草稿卡
docs/superpowers/specs/2026-10-20-capnet-v2-design.md  v2 设计 spec
```

入选判据（用户裁决）：**领域内切实存在且具有应用价值即可成卡**，accessibility（api/cli/mcp/gui/manual）是属性不是门槛；gui/manual 卡只可推荐人工使用。

冷启动认可度先验：卡带源外部流行度（`popularity.users` + `normalized` 源内分位数）时，打分侧以 `0.2 + 0.7×normalized` 作为认可度的贝叶斯先验（见 `search._popularity_prior`）；无数据（`None`）走全局默认 0.5。真实遥测累积后先验被快速覆盖——外部流行度只解决冷启动排序，不压制后续社区反馈。

