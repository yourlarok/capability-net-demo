"""需求意图层：先判断"用户要哪一族能力"，再在族内按相关度 × 质量排序。

族的来源有两个，优先级从高到低：
  a. 调用方声明（find_capability / plan_task 的 capability_type）。调用方是 LLM，语义理解远强于词表——
     "给动画角色配个萌妹声音"它一眼知道是配音。这是主路径。
  b. 线索词表推断（下文）。只在调用方没声明时兜底；对没见过的说法覆盖有限（盲测见 data/eval）。

为什么需要：相关度是字/二字词重叠，"画一个角色立绘"会因"角色"命中"多角色配音"，
"水彩画"会因单字蹭到音频工作站。词重叠回答不了"这是画画还是配音"，所以在打分前加一道族门：

  1. 查询 -> 族权重：按线索词表最长匹配（"画面"整体算视频、"画外音"算语音、"用户画像"算数据，
     不再拆出"画"）。动作/产物词权重高（配音、抠图、海报），上下文名词权重低（视频、图片）。
  2. 卡 -> 族：由领域决定（audio.tts -> speech，audio.music -> music …），一卡可属多族。
  3. 排序：主族（权重最高的族，并列都算）> 次族 > 族外；有族内候选时族外直接不出现。
     识别不出任何族（"帮我想想办法"）时不设门，退回纯打分。
"""
from __future__ import annotations

FAMILIES = {
    "image": "🎨 图像", "video": "🎬 视频", "speech": "🗣 语音/配音", "music": "🎵 音乐",
    "sound": "🔊 音效/音频处理", "text": "📝 文字", "data": "📊 数据/调研", "marketing": "📣 营销投放",
    "commerce": "🛒 电商", "publish": "📤 发布", "game": "🎮 游戏", "3d": "🧊 3D", "code": "💻 开发",
}

# 领域 -> 族；先查完整领域，再查一级前缀
DOMAIN_FAMILIES = {
    "video.storyboard": {"video", "image"}, "video.understand": {"video", "data"},
    "audio.tts": {"speech"}, "audio.asr": {"speech"}, "audio.music": {"music"}, "audio.daw": {"music"},
    "audio.sfx": {"sound"}, "audio.edit": {"sound"}, "audio.repair": {"sound"},
    "design.ui": {"image", "code"}, "image.sprite": {"image", "game"},
}
PREFIX_FAMILIES = {
    "image": {"image"}, "video": {"video"}, "audio": {"sound"}, "text": {"text"}, "office": {"text"},
    "data": {"data"}, "research": {"data"}, "ads": {"marketing"}, "strategy": {"marketing"},
    "commerce": {"commerce"}, "publish": {"publish"}, "game": {"game"}, "3d": {"3d"},
    "devtools": {"code"}, "design": {"image"},
}

A, N, W = 1.5, 1.0, 0.5  # 动作/产物词、一般线索、上下文名词

# (线索词, 族, 权重)。族为空 = 吃掉这段文本但不计入任何族（"意图""地图"不是画画）
_CUES: list[tuple[str, tuple[str, ...], float]] = [
    # —— 消歧：含"画/图/音/声"但不是字面意思的词，必须整体匹配
    ("画面", ("video",), N), ("画质", ("image", "video"), N), ("画外音", ("speech",), A),
    ("用户画像", ("data",), A), ("人群画像", ("data",), A), ("画像", ("data",), N),
    ("动画", ("video",), A), ("图表", ("data",), N), ("思维导图", ("text",), A), ("流程图", ("text",), N),
    ("意图", (), 0), ("试图", (), 0), ("地图", (), 0), ("路线图", (), 0), ("图文", ("image", "text"), N),
    ("录音", ("speech",), W), ("声音克隆", ("speech",), A), ("克隆声音", ("speech",), A),
    ("音乐", ("music",), A), ("音效", ("sound",), A), ("音频", ("sound",), N), ("声音", ("sound",), W),
    ("生成视频", ("video",), A), ("图生视频", ("video",), A), ("文生视频", ("video",), A),
    ("动起来", ("video",), A), ("文生图", ("image",), A), ("生图", ("image",), A), ("出图", ("image",), A),
    # —— 图像
    ("画画", ("image",), A), ("绘画", ("image",), A), ("绘图", ("image",), A), ("画图", ("image",), A),
    ("插画", ("image",), A), ("插图", ("image",), A), ("配图", ("image",), A), ("立绘", ("image",), A),
    ("原画", ("image",), A), ("头像", ("image",), A), ("海报", ("image",), A), ("logo", ("image",), A),
    ("标志", ("image",), N), ("图标", ("image",), A), ("漫画", ("image",), A), ("水彩", ("image",), A),
    ("油画", ("image",), A), ("素描", ("image",), A), ("手绘", ("image",), A), ("概念图", ("image",), A),
    ("封面", ("image",), A), ("壁纸", ("image",), A), ("表情包", ("image",), A), ("像素画", ("image",), A),
    ("精灵图", ("image", "game"), A), ("sprite", ("image", "game"), A), ("tileset", ("image", "game"), A),
    ("抠图", ("image",), A), ("抠", ("image",), A), ("修图", ("image",), A), ("p图", ("image",), A),
    ("主图", ("image", "commerce"), A), ("矢量", ("image",), A), ("截图", ("image",), N),
    ("图片", ("image",), W), ("照片", ("image",), W), ("图像", ("image",), W),
    ("清晰", ("image", "video"), N), ("模糊", ("image", "video"), N), ("糊", ("image", "video"), N),
    ("超分", ("image", "video"), A), ("放大", ("image", "video"), W),
    ("画", ("image",), A), ("绘", ("image",), A), ("图", ("image",), W),
    # —— 视频
    ("短视频", ("video",), N), ("视频", ("video",), W), ("剪辑", ("video",), A), ("剪映", ("video",), A),
    ("分镜", ("video", "image"), A), ("运镜", ("video",), A), ("镜头", ("video",), N), ("影片", ("video",), N),
    ("片子", ("video",), W), ("拍摄", ("video",), N), ("录屏", ("video",), A), ("特效", ("video",), A),
    ("vlog", ("video",), N), ("mv", ("video", "music"), N), ("拉片", ("video",), A),
    # —— 语音
    ("配音", ("speech",), A), ("朗读", ("speech",), A), ("旁白", ("speech",), A), ("播音", ("speech",), A),
    ("有声书", ("speech",), A), ("tts", ("speech",), A), ("语音", ("speech",), N), ("人声", ("speech",), N),
    ("念出来", ("speech",), A), ("读出来", ("speech",), A), ("转文字", ("speech",), A), ("转写", ("speech",), A),
    ("字幕", ("speech", "video"), A), ("口播", ("speech", "video"), N),
    # —— 音乐 / 音效
    ("配乐", ("music",), A), ("作曲", ("music",), A), ("编曲", ("music",), A), ("bgm", ("music",), A),
    ("歌曲", ("music",), A), ("一首歌", ("music",), A), ("旋律", ("music",), A), ("伴奏", ("music",), A),
    ("混音", ("music", "sound"), A), ("歌", ("music",), N),
    ("降噪", ("sound",), A), ("杂音", ("sound",), A), ("噪音", ("sound",), A),
    # —— 文字
    ("文案", ("text",), A), ("写作", ("text",), A), ("小说", ("text",), A), ("剧本", ("text",), A),
    ("脚本", ("text",), N), ("文章", ("text",), W), ("翻译", ("text",), A), ("润色", ("text",), A),
    ("ppt", ("text",), A), ("幻灯片", ("text",), A), ("演示文稿", ("text",), A), ("文字", ("text",), W),
    # —— 数据 / 营销 / 电商 / 发布
    ("数据", ("data",), N), ("报表", ("data",), A), ("调研", ("data",), A), ("趋势", ("data",), A),
    ("热点", ("data",), A), ("竞品", ("data",), A), ("指数", ("data",), N), ("分析", ("data",), W),
    ("投放", ("marketing",), A), ("广告", ("marketing",), A), ("推广", ("marketing",), N),
    ("营销", ("marketing",), N), ("ab测试", ("marketing",), A), ("a/b", ("marketing",), A),
    ("品牌", ("marketing",), W), ("信息流", ("marketing",), A),
    ("直播", ("commerce",), A), ("带货", ("commerce",), A), ("卖货", ("commerce",), A),
    ("电商", ("commerce",), A), ("详情页", ("commerce", "image"), A), ("店铺", ("commerce",), N),
    ("发布", ("publish",), A), ("上传到", ("publish",), N), ("发到", ("publish",), N),
    ("小红书", ("publish",), W), ("播客", ("publish", "speech"), N),
    # —— 游戏 / 3D / 开发
    ("游戏", ("game",), A), ("关卡", ("game",), A), ("3d", ("3d",), A), ("建模", ("3d",), A),
    ("三维", ("3d",), A), ("模型", ("3d",), W),
    ("界面", ("image", "code"), A), ("ui", ("image", "code"), A), ("app", ("code",), W),
    ("网页", ("code",), N), ("代码", ("code",), A), ("编程", ("code",), A),
]
# —— 同义词扩充（按族成组补齐，不针对具体查询）
_CUES += [
    # 图像
    ("像素", ("image",), N), ("像素风", ("image", "game"), A), ("贺卡", ("image",), A), ("头图", ("image",), A),
    ("设定图", ("image",), A), ("人设图", ("image",), A), ("速写", ("image",), A), ("线稿", ("image",), A),
    ("上色", ("image",), A), ("换背景", ("image",), A), ("去背景", ("image",), A), ("背景替换", ("image",), A),
    ("产品图", ("image",), N), ("商品图", ("image",), N), ("绘本", ("image",), W), ("二次元", ("image",), W),
    ("配色", ("image",), N), ("排版", ("image",), N), ("横幅", ("image",), A), ("banner", ("image",), A),
    # 语音
    ("说话", ("speech",), A), ("开口", ("speech",), N), ("声线", ("speech",), A), ("音色", ("speech",), A),
    ("童声", ("speech",), A), ("女声", ("speech",), A), ("男声", ("speech",), A), ("有声", ("speech",), A),
    ("文字稿", ("speech",), A), ("逐字稿", ("speech",), A), ("听写", ("speech",), A), ("播报", ("speech",), A),
    ("讲解", ("speech",), W), ("读", ("speech",), N), ("念", ("speech",), N), ("嗓音", ("speech",), A),
    # 音效 / 音频处理
    ("声效", ("sound",), A), ("拟音", ("sound",), A), ("环境音", ("sound",), A), ("听不清", ("sound",), A),
    ("音量", ("sound",), A), ("响度", ("sound",), A), ("回声", ("sound",), A), ("爆音", ("sound",), A),
    # 音乐
    ("片头音乐", ("music",), A), ("主题曲", ("music",), A), ("曲子", ("music",), A), ("鼓点", ("music",), N),
    ("节奏", ("music",), W), ("音轨", ("music", "sound"), N), ("唱", ("music",), N), ("歌词", ("music", "text"), A),
    # 视频
    ("调色", ("video",), A), ("转场", ("video",), A), ("切片", ("video",), A), ("混剪", ("video",), A),
    ("卡点", ("video",), A), ("素材", ("video",), W), ("宣传片", ("video",), A), ("短片", ("video",), A),
    ("片头", ("video",), N), ("慢动作", ("video",), A), ("抠像", ("video",), A), ("成视频", ("video",), A),
    ("展示视频", ("video",), A), ("跳舞", ("video",), N), ("延时", ("video",), N),
    # 文字
    ("笔记", ("text",), N), ("种草", ("text", "marketing"), N), ("文稿", ("text",), A), ("稿子", ("text",), A),
    ("大纲", ("text",), A), ("标题", ("text",), N), ("邮件", ("text",), A), ("报告", ("text",), N),
    ("复盘", ("data", "text"), N), ("周报", ("text",), A), ("简历", ("text",), A),
    # 数据 / 营销 / 3D / 游戏 / 开发
    ("对比", ("data",), W), ("榜单", ("data",), A), ("统计", ("data",), A),
    ("投流", ("marketing",), A), ("千川", ("marketing",), A), ("出价", ("marketing",), A), ("roi", ("marketing",), N),
    ("手办", ("3d",), A), ("渲染", ("3d",), N), ("unity", ("game",), A), ("unreal", ("game",), A),
    ("godot", ("game",), A), ("道具", ("game",), W), ("小程序", ("code",), A), ("原型", ("image", "code"), N),
    ("前端", ("code",), A),
]
_LEX = sorted(_CUES, key=lambda c: -len(c[0]))
HEAD_BONUS = 0.75
PRIMARY_RATIO = 0.99  # 主族 = 权重最高的族（并列都算）；其余命中族为次族，排在后面但保留


def query_families(query: str) -> dict[str, float]:
    """最长匹配扫描线索词，返回 {族: 权重}；识别不出返回空 dict。

    权重 = 该族最强线索 + 0.1 × 线索总和 + 中心词加成。中文名词短语是"修饰在前、中心在后"：
    "像素风游戏的循环背景音乐"要的是音乐，"像素风/游戏"只是修饰——所以最后一个强线索（动作/产物词）
    所属的族额外加权，而不是简单累加（累加会让修饰词多的族胜出）。
    """
    q = query.lower()
    hits: list[tuple[tuple[str, ...], float]] = []
    i = 0
    while i < len(q):
        for cue, fams, w in _LEX:
            if q.startswith(cue, i):
                if fams:
                    hits.append((fams, w))
                i += len(cue)
                break
        else:
            i += 1
    best: dict[str, float] = {}
    total: dict[str, float] = {}
    for fams, w in hits:
        for f in fams:
            best[f] = max(best.get(f, 0.0), w)
            total[f] = total.get(f, 0.0) + w
    out = {f: best[f] + 0.1 * total[f] for f in best}
    head = next((fams for fams, w in reversed(hits) if w >= A), None)
    for f in head or ():
        out[f] += HEAD_BONUS
    return out


def declared_or_inferred(query: str | None, declared: list[str] | None) -> dict[str, float]:
    valid = [f for f in declared or [] if f in FAMILIES]
    if valid:
        return {f: 1.0 for f in valid}
    return query_families(query) if query else {}


def card_families(domain: str) -> set[str]:
    return DOMAIN_FAMILIES.get(domain) or PREFIX_FAMILIES.get(domain.split(".")[0], set())


def tier(domain: str, qfam: dict[str, float]) -> int:
    """2 = 主族，1 = 次族，0 = 族外。qfam 为空时全部算 2（不设门）。"""
    if not qfam:
        return 2
    top = max(qfam.values())
    best = max((qfam.get(f, 0.0) for f in card_families(domain)), default=0.0)
    if best <= 0.0:
        return 0
    return 2 if best >= top * PRIMARY_RATIO else 1


def describe(qfam: dict[str, float]) -> str:
    if not qfam:
        return "未识别出明确的能力类型"
    top = max(qfam.values())
    prim = [f for f, w in sorted(qfam.items(), key=lambda x: -x[1]) if w >= top * PRIMARY_RATIO]
    return " + ".join(FAMILIES.get(f, f) for f in prim)
