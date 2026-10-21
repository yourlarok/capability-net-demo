# capnet 安装说明（写给 AI agent）

> 你是一个 AI agent，用户让你按这份说明安装 capnet。请**自己完成下面的步骤**，不要让用户手动操作，也**不需要克隆仓库**。
> 每一步都会输出结果；遇到失败时按「排障」处理，确实无法继续再告诉用户。

capnet 是一个能力与工作流路由网络。安装后，你在遇到专业任务时可以调用它，拿到成熟的工作流和合适的工具推荐。它零第三方依赖，只需要 Python 3.11 及以上。

## 第 1 步：安装

先检查环境：`python --version`（或 `python3 --version`）应为 3.11 及以上；再看 `uv --version` 是否可用。

**有 uv（推荐）：**

```bash
uv tool install --force "capnet @ https://github.com/yourlarok/capability-net-demo/archive/refs/heads/v3-traces.zip"
```

**没有 uv：**用 pip 装到用户目录：

```bash
python -m pip install --user --upgrade "capnet @ https://github.com/yourlarok/capability-net-demo/archive/refs/heads/v3-traces.zip"
```

两种方式都是直接下载源码包安装，不需要 git，也不需要克隆仓库。

## 第 2 步：问用户一个问题（上报方式）

capnet 会在每次任务结束时自动记录反馈（结果、纠正次数、跳过的步骤、用了哪些工具），用来给工具和工作流打分、让社区工作流生长。
**收集不用用户操作，但是否上报必须由用户事先决定。** 安装前用一句话问用户（有选择题工具就用选择题）：

> 任务结束后，清理过的匿名解决思路要怎么处理？
> 1. **自动上报**（`auto`）
> 2. **每次先给我看，同意再上报**（`ask`）
> 3. **不上报，只在本机生效**（`off`）
> 选哪个都不影响任何功能，之后随时能改。

用户没有明确选择就不要替他选，第 3 步里省略 `--share` 即可（按不上报处理）。

## 第 3 步：接入本机的 agent 客户端

```bash
capnet setup --share <用户的选择> --dry-run      # 先预演：列出将要改动的文件
capnet setup --share <用户的选择>                # 正式执行
```

如果提示找不到 `capnet` 命令（用户目录的 Scripts/bin 不在 PATH 里），改用 `python -m capnet setup`（用安装时的同一个 python）。

`setup` 会自动探测并配置以下客户端：Claude Code、Codex CLI、Gemini CLI、Cursor、Windsurf、Claude Desktop。

- 只修改 capnet 自己那一项配置，原文件先备份为 `.bak`。
- 重复执行会更新配置，不会重复添加。
- 同时安装使用规范：Claude Code 写入一个 skill；Codex 和 Gemini 在全局说明文件里写入一段带标记的区块。

只想配置某一个客户端时：`capnet setup --client cursor`（参数可重复）。

## 第 4 步：告诉用户结果

把 `capnet setup` 输出的 Markdown 报告**原样**展示给用户，并提醒用户**重启对应的客户端**（或开一个新会话）后生效。

安装完成后可以建议用户这样试：

- 「帮我画一张中秋节海报」：应推荐图像类工具，而不是配音工具。
- 「我们的抖音视频完播率很低，帮我出一个完整的改进计划」：应返回 7 个阶段的工作流和每一步的工具。

## 使用时的约定（安装后你应遵守）

- 调用 `find_capability` / `plan_task` 时，传 `capability_type`，写上你对需求能力类型的判断（image / video / speech / music / sound / text / data / marketing / commerce / publish / game / 3d / code）。
- 返回的能力卡和计划是排版好的 Markdown，**原样展示给用户**。
- 标为"👤 人工"的工具只能推荐给用户操作，不要假装能调用。
- 每张卡都带「接入渠道」。API 工具先检查环境变量，没设置就把申请凭证链接和变量名告诉用户，不要让用户把 Key 贴进对话。
- 任务结束时调用 `finish_task`，按对话中观察到的情况填结果、纠正次数、跳过的阶段，不要为此打扰用户。上报方式是 `ask` 时，必须把预览原样给用户看、同意后才 `submit_trace(consent=true)`。

完整规范：`capnet rules`。

## 更新与卸载

```bash
uv tool install --force "capnet @ https://github.com/yourlarok/capability-net-demo/archive/refs/heads/v3-traces.zip"   # 更新（pip 用户把上面的 pip 命令再跑一次）
capnet setup --remove        # 撤销所有客户端里的 capnet 配置与规范
uv tool uninstall capnet     # 或 python -m pip uninstall capnet
```

## 排障

| 现象 | 处理 |
|---|---|
| 下载 GitHub 超时 | 让用户确认网络或代理；也可以把 URL 换成 `https://codeload.github.com/yourlarok/capability-net-demo/zip/refs/heads/v3-traces` |
| `python` 版本低于 3.11 | 有 uv 时加 `--python 3.12`，uv 会自动下载合适的 Python |
| 某个客户端的配置文件不是合法 JSON | `setup` 不会覆盖它，会在报告里列出；请用户自行检查该文件 |
| 客户端里看不到 capnet | 确认已重启；Claude Code 可以运行 `claude mcp list` 查看 |
| 不想装任何东西 | 如果有人提供了远程服务：Claude Code 用 `claude mcp add --transport http capnet <服务地址>/mcp`，其他客户端在 MCP 设置里填这个 URL |
