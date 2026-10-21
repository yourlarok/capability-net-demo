"""隐私清理：上传轨迹前把私有信息洗掉，只留"客观解决问题的思路"。

两道防线：
1. 规则清理（本模块）：联系方式、证件/卡号、密钥、路径、链接、IP、@账号、金额、《作品名》，
   以及用户自定义私有词表（公司名、产品名、人名……）；
2. 人工确认：清理结果原样展示给用户，用户同意后才上传（见 traces.summarize / hub.submit）。
规则清理是尽力而为，不能识别任意人名——所以第 2 道防线不可省略。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

SURNAMES = ("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程"
            "苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵"
            "万钱严覃武戴莫孔向汤")

# (标签, 正则, 替换) —— 顺序有意义：先长后短、先具体后泛化
RULES: list[tuple[str, re.Pattern, str]] = [
    ("密钥", re.compile(r"\b(?:sk|pk|rk|ghp|gho|github_pat|xox[abp]|AKIA)[-_A-Za-z0-9]{12,}\b"), "[密钥]"),
    ("密钥", re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9])"), "[密钥]"),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[邮箱]"),
    ("链接", re.compile(r"(?:https?|ftp)://[^\s，。；、）)\]】\"']+", re.I), "[链接]"),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[证件号]"),
    ("卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "[卡号]"),
    ("手机", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[电话]"),
    ("电话", re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)"), "[电话]"),
    ("IP", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[IP]"),
    ("路径", re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s，。；、\"'<>|]+"), "[路径]"),
    ("路径", re.compile(r"(?:~|/(?:home|Users|root|mnt|data|var|opt|tmp))/[^\s，。；、\"'<>|]*"), "[路径]"),
    ("账号", re.compile(r"(?<![\w.])@[\w一-鿿]{2,30}"), "[账号]"),
    ("金额", re.compile(r"(?:[¥￥$€£]\s?\d[\d,.]*\s?[万亿千kKwW]?)|(?:\d[\d,.]*\s?(?:万|亿|千)?\s?(?:元|块钱|美元|美金|rmb|RMB|USD))"), "[金额]"),
    ("作品名", re.compile(r"《[^》]{1,40}》"), "《作品》"),
    # 姓 + 称谓（张总/李经理/王老师）：限常见姓氏，避免误伤"汇总""总结"
    ("称谓", re.compile(f"[{SURNAMES}](?:总监|总裁|总|经理|老师|老板|主管|律师|医生|导演|同学|先生|女士|小姐)"), "[某人]"),
]

MAX_NOTE = 160  # 单条思路笔记上限：思路是方法，不是过程记录


@dataclass
class ScrubReport:
    removed: dict[str, int] = field(default_factory=dict)

    def add(self, label: str, n: int = 1) -> None:
        if n:
            self.removed[label] = self.removed.get(label, 0) + n

    def merge(self, other: "ScrubReport") -> None:
        for k, v in other.removed.items():
            self.add(k, v)


def load_private_terms(path: str | None = None) -> list[str]:
    """用户私有词表：每行一个词（公司名/产品名/人名/客户名），# 开头为注释。

    默认读 $CAPNET_PRIVATE_TERMS，其次 $CAPNET_HOME/private_terms.txt（默认 ~/.capnet）。
    """
    from .paths import capnet_home
    path = path or os.environ.get("CAPNET_PRIVATE_TERMS") or os.path.join(capnet_home(), "private_terms.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


def scrub_text(text: str, private_terms: list[str] | None = None,
               report: ScrubReport | None = None, max_len: int | None = MAX_NOTE) -> str:
    report = report if report is not None else ScrubReport()
    out = text or ""
    for term in sorted(private_terms or [], key=len, reverse=True):
        if term and term in out:
            report.add("私有词", out.count(term))
            out = out.replace(term, "[私有]")
    for label, pat, repl in RULES:
        out, n = pat.subn(repl, out)
        report.add(label, n)
    out = re.sub(r"\s+", " ", out).strip()
    if max_len and len(out) > max_len:
        report.add("截断")
        out = out[:max_len].rstrip() + "…"
    return out


def residual_risks(text: str) -> list[str]:
    """清理后仍需人工注意的可疑片段（不自动删，提示用户确认）。"""
    hints = []
    if re.search(r"(?<!\d)\d{6,}(?!\d)", text):
        hints.append("含 6 位以上数字串（订单号/工号？）")
    # CJK 无词边界，\b 永远不命中；改用后缀排除常见合成词（总结/总体/总量……）
    if re.search(r"(?:(?<![汇])总|经理|老师|老板|同学|先生|女士)(?![结体部量计数共])", text):
        hints.append("含称谓，可能指向具体人")
    if re.search(r"(?:公司|集团|工作室|有限)", text):
        hints.append("提到机构，确认没有写出具体名称")
    return hints
