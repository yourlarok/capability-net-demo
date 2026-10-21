# capnet 批量领域评测

- 题量 36（域内 30 / 域外 6）
- 工作流命中@1：**97%**
- 域外拒识率：**100%**
- 命中计划的槽位覆盖：**100%**
- 已填槽位的自动化率：**48%**（其余只能推荐人工工具）
- 缺卡槽位：无

## 逐条

| ✓ | 领域 | 任务 | 期望 | 实得 | 匹配度 | 覆盖 | 自动 |
|---|---|---|---|---|---|---|---|
| ✅ | marketing | 我们公司的产品宣传视频播放量很低，帮我出一个完善的视频改进计划 | marketing-video-improve | marketing-video-improve | 0.52 | 100% | 44% |
| ✅ | marketing | 投了一周信息流广告，素材 CTR 只有 0.8%，怎么优化这条广告视频 | marketing-video-improve | marketing-video-improve | 0.34 | 100% | 44% |
| ✅ | marketing | 抖音视频完播率太低了，前三秒就划走，怎么改 | marketing-video-improve | marketing-video-improve | 0.32 | 100% | 44% |
| ✅ | marketing | 帮我分析一下这条推广视频为什么转化差，给出优化方案 | marketing-video-improve | marketing-video-improve | 0.33 | 100% | 44% |
| ❌ | marketing | Our product promo video gets views but nobody clicks the link, how to fix it | marketing-video-improve | 无 | 0.03 | - | - |
| ✅ | marketing | 老板说我们的片子没人看，下个月要拿出改版思路 | marketing-video-improve | marketing-video-improve | 0.23 | 100% | 44% |
| ✅ | brand | 下个季度要发布新品，帮我做一个整合营销传播方案 | brand-campaign | brand-campaign | 0.50 | 100% | 25% |
| ✅ | brand | 给一个国潮茶饮品牌策划一场线上 campaign | brand-campaign | brand-campaign | 0.28 | 100% | 25% |
| ✅ | brand | 公司想提升品牌知名度，预算 50 万，怎么规划 | brand-campaign | brand-campaign | 0.14 | 100% | 25% |
| ✅ | social | 想做一个小红书美妆账号，从零起号怎么做 | account-growth | account-growth | 0.43 | 100% | 40% |
| ✅ | social | 我的抖音账号三个月才 200 粉，怎么涨粉 | account-growth | account-growth | 0.30 | 100% | 40% |
| ✅ | social | 个人 IP 怎么打造，打算做职场类内容 | account-growth | account-growth | 0.25 | 100% | 40% |
| ✅ | commerce | 新上架一款保温杯，需要做主图视频和带货短视频 | ecommerce-product-video | ecommerce-product-video | 0.36 | 100% | 25% |
| ✅ | commerce | 淘宝店的商品详情页和主图怎么做得更有吸引力 | ecommerce-product-video | ecommerce-product-video | 0.39 | 100% | 25% |
| ✅ | commerce | 帮我给一款蓝牙耳机做抖音带货素材 | ecommerce-product-video | ecommerce-product-video | 0.18 | 100% | 25% |
| ✅ | live | 昨晚直播场观 2 万但只卖了 30 单，帮我复盘 | live-commerce-review | live-commerce-review | 0.32 | 100% | 33% |
| ✅ | live | 直播间留不住人，主播话术怎么优化 | live-commerce-review | live-commerce-review | 0.50 | 100% | 33% |
| ✅ | edu | 我想把 Python 入门做成一套网课视频 | course-video | course-video | 0.24 | 100% | 50% |
| ✅ | edu | 公司内部要做新员工培训视频，怎么制作 | course-video | course-video | 0.27 | 100% | 50% |
| ✅ | edu | 给初中物理录一节讲浮力的教学视频 | course-video | course-video | 0.28 | 100% | 50% |
| ✅ | podcast | 想开一档聊科技创业的播客，第一期怎么做 | podcast-episode | podcast-episode | 0.12 | 100% | 60% |
| ✅ | podcast | 录好的访谈音频有底噪，还要出 shownotes 发小宇宙 | podcast-episode | podcast-episode | 0.39 | 100% | 60% |
| ✅ | game | 做一个像素风的猫咪 RTS 小游戏原型 | game-dev | game-dev | 0.55 | 100% | 100% |
| ✅ | game | 帮我做个独立游戏，需要美术和音效 | game-dev | game-dev | 0.32 | 100% | 100% |
| ✅ | drama | 做一集 AI 漫剧，从剧本到成片 | short-drama-episode | short-drama-episode | 0.36 | 100% | 67% |
| ✅ | drama | 把我的网文第一章改编成竖屏短剧 | short-drama-episode | short-drama-episode | 0.15 | 100% | 67% |
| ✅ | recap | 给《星际穿越》做一条电影解说视频 | video-recap | video-recap | 0.43 | 100% | 67% |
| ✅ | recap | 把这部老剧剪成 10 分钟剧情解说 | video-recap | video-recap | 0.33 | 100% | 67% |
| ✅ | ambiguous | 做一个品牌宣传片 | brand-campaign|marketing-video-improve | marketing-video-improve | 0.34 | 100% | 44% |
| ✅ | ambiguous | 给课程做推广视频，提高报名转化 | marketing-video-improve|course-video | marketing-video-improve | 0.29 | 100% | 44% |
| ✅ | out | 帮我审查一份房屋租赁合同的风险条款 | （域外） | 无 | 0.00 | - | - |
| ✅ | out | 设计一个三居室的装修方案 | （域外） | 无 | 0.00 | - | - |
| ✅ | out | 公司年报的财务报表审计怎么做 | （域外） | 无 | 0.00 | - | - |
| ✅ | out | 制定一个马拉松训练计划 | （域外） | 无 | 0.00 | - | - |
| ✅ | out | 量子计算云服务选型 | （域外） | 无 | 0.00 | - | - |
| ✅ | out | 给医院做一套排班系统 | （域外） | 无 | 0.00 | - | - |
