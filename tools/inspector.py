#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语法检察官 v1.0
高中英语语法切片·逐字稿质量自动检查器

用法：
    python3 inspector.py <逐字稿.docx> [更多.docx ...]
    python3 inspector.py --all           检查 docs/ 下全部逐字稿
"""

import re
import sys
import glob
import subprocess
from collections import Counter

# ══════════════════════════════════════════════════════════
#  第一章  违禁词：低龄化包装、营销腔、书面标记
# ══════════════════════════════════════════════════════════
BANNED = {
    "口诀": "禁用口诀儿歌，高中生不吃这套",
    "儿歌": "禁用口诀儿歌",
    "闯关": "禁用游戏化包装，改用「例题」",
    "通关": "禁用游戏化包装",
    "解锁": "禁用「解锁技能」这类包装",
    "太棒": "禁用夸张感叹",
    "真棒": "禁用夸张感叹",
    "不见不散": "禁用低龄化收尾，用「下节课见」",
    "小宝贝": "面向高中生，禁用低龄称呼",
    "小朋友": "面向高中生，禁用低龄称呼",
    "宝宝": "面向高中生，禁用低龄称呼",
    "例句：": "书面标记，口播稿不出现",
    "彻底夯实": "营销腔，直接说「下面是六道题」",
    "超重要": "营销腔",
    "百试百灵": "夸大表述",
    "黄金判据": "包装词，直说「判断标准」",
}

# ══════════════════════════════════════════════════════════
#  第二章  术语红线：说错就是硬伤
# ══════════════════════════════════════════════════════════
TERM_RED = [
    (r"because\s*是连词", "because 是引导词不是连词；连词专指 and/but/or/so"),
    (r"although\s*是连词", "although 是引导词不是连词"),
    (r"when\s*是连词", "when 是引导词不是连词"),
    (r"lie\s*短", "lie 与 lay 同为三个字母，长短记忆法失效；应用「带字母 a 的及物」"),
    (r"短的那个不及物", "长短记忆法对 lie/lay 失效，禁用"),
    (r"make、let、have\s*和感官动词.{0,20}被动之后要把\s*to\s*还回来",
     "let/have 一般不用被动；只有 make 与感官动词适用「被动还 to」"),
    (r"\bme is\b", "检验句用了宾格作主语；应换成人名或表人名词"),
    (r"\bhim is\b(?!.{0,30}错)", "检验句用了宾格作主语；应换成人名或表人名词"),
    (r"\bus are\b", "检验句用了宾格作主语；应换成人名或表人名词"),
    (r"was happened|were happened", "happen 为不及物动词，无被动语态", "改错|错在|错了|错误|不能用被动|应改为|写成|改成|是错的"),
    (r"加 is 的办法|加一个 is", "统一表述为「加 be 动词检验法」"),
    (r"换成主格", "检验句应直接换人名/表人名词，不引入主格转换规则"),
]

# ══════════════════════════════════════════════════════════
#  第三章  互动标记：讲题必须跟学生对话
# ══════════════════════════════════════════════════════════
INTERACT = [
    "我问你", "你先自己", "先自己", "找到了吗", "对吧", "别急", "我猜", "暂停",
    "你看", "想想", "你自己", "听好", "跟我", "我知道你", "有没有", "是不是",
    "怎么办", "行吗", "通吗", "有错吗", "还记得", "我们一起", "先别", "念一遍",
    "你想", "看出来了吗", "发现", "问自己", "试试", "扫一眼", "拿笔", "记一下",
]

# 段落连接词：丝滑度指标
CONNECT = ["好，", "好。", "那", "来，", "接着", "现在", "所以", "但是", "不过",
           "再", "然后", "反过来", "顺便", "另外", "最后", "第一步", "既然", "既然如此"]

# ══════════════════════════════════════════════════════════
#  第四章  结构规格
# ══════════════════════════════════════════════════════════
SPEC = {"课前测": 6, "例题": 2, "随堂练习题": 6}
BLANK = "____________"


def extract(path):
    if path.endswith(".docx"):
        return subprocess.run(["extract-text", path], capture_output=True, text=True).stdout
    return open(path, encoding="utf-8").read()


def zh(t):
    return len(re.findall(r"[\u4e00-\u9fff]", t))


def check(path):
    t = extract(path)
    paras = [p.strip() for p in t.split("\n") if p.strip()]
    name = path.split("/")[-1]
    issues, warns, passes = [], [], []
    total_zh = zh(t)

    # ── 1 违禁词 ──
    hit = [(w, r) for w, r in BANNED.items() if w in t]
    if hit:
        for w, r in hit:
            issues.append(f"【违禁词】出现「{w}」——{r}")
    else:
        passes.append("违禁词：无")

    # ── 2 术语红线 ──
    tr = []
    for rule in TERM_RED:
        pat, msg = rule[0], rule[1]
        exempt = rule[2] if len(rule) > 2 else None
        for m in re.finditer(pat, t):
            ctx = t[max(0, m.start() - 60):m.start() + 60].replace("\n", " ")
            if exempt and re.search(exempt, ctx):
                continue   # 讲解错误用法时的正当引用，放行
            tr.append(f"【术语红线】{msg}\n            位置：…{ctx[35:110]}…")
            break
    issues.extend(tr)
    if not tr:
        passes.append("术语红线：无")

    # ── 3 罗列体（连续同模板段落）──
    runs, cnt, start = [], 0, 0
    for i, p in enumerate(paras):
        if re.match(r"^第[一二三四五六七八九十]+[种组对个类步条]", p):
            if cnt == 0:
                start = i
            cnt += 1
        else:
            if cnt >= 3:
                runs.append((start, cnt, paras[start][:26]))
            cnt = 0
    if cnt >= 3:
        runs.append((start, cnt, paras[start][:26]))
    # 排除小结/检查点里的「第一…第五」条目式收尾（允许）
    real = [r for r in runs if not re.match(r"^第[一二三四五]条?，", r[2])]
    for _, n, sample in real:
        issues.append(f"【罗列体】连续 {n} 段同模板开头，读起来像讲义：「{sample}…」")
    if not real:
        passes.append("罗列体：无")

    # ── 4 题量规格 ──
    for k, want in SPEC.items():
        got = len(set(re.findall(rf"{k}第?(\d)题?", t)))
        if got != want:
            issues.append(f"【题量】{k} 应为 {want} 道，实际 {got} 道")
    if all(len(set(re.findall(rf"{k}第?(\d)题?", t))) == v for k, v in SPEC.items()):
        passes.append(f"题量：课前测6 + 例题2 + 随堂6")

    # ── 5 填空符与选项行 ──
    if "　　　　" in t:
        issues.append("【题面】用全角空格占位，必须改为下划线 " + BLANK)
    opt_bad = [p for p in paras if re.search(r"[。？]\s*A[.、]\s*\S+.{0,60}B[.、]", p)]
    if opt_bad:
        issues.append(f"【题面】{len(opt_bad)} 处选择题选项未独立成行：「{opt_bad[0][:34]}…」")
    if "　　　　" not in t and not opt_bad:
        passes.append("题面：下划线与选项行规范")

    # ── 6 题目衔接语 ──
    no_lead = []
    for i, p in enumerate(paras):
        if re.match(r"^\*{0,2}(课前测第\d题|例题\d|随堂练习题\d)\*{0,2}$", p):
            prev = paras[i - 1] if i else ""
            ok = len(prev) < 46 and any(
                k in prev for k in ["第一", "第二", "第三", "第四", "第五", "最后",
                                    "接下来", "先看", "来看", "接着", "我们先"])
            if not ok:
                no_lead.append(p.strip("*"))
    if no_lead:
        issues.append(f"【衔接】{len(no_lead)} 道题前没有口语衔接语：{', '.join(no_lead[:4])}")
    else:
        passes.append("题目衔接：14 道题全部有衔接语")

    # ── 7 答案先于推理 ──
    早报 = []
    for i, p in enumerate(paras):
        if re.match(r"^\*{0,2}(课前测第\d题|例题\d|随堂练习题\d)", p):
            # 题号后第 2 段（跳过题干/选项）若已出现答案，判定为先报答案
            seg = paras[i + 1:i + 4]
            for j, sp in enumerate(seg):
                if sp.startswith("题目：") or re.match(r"^A[.、]", sp):
                    continue
                if re.search(r"(答案是|这道题的答案|正确答案)", sp) and j <= 1:
                    早报.append(p.strip("*"))
                break
    if 早报:
        issues.append(f"【先报答案】{len(早报)} 处一上来就给结论：{', '.join(早报[:4])}")
    else:
        passes.append("讲题顺序：先推理后答案")

    # ── 8 互动密度 ──
    ic = sum(t.count(k) for k in INTERACT)
    per_k = ic / max(total_zh, 1) * 1000
    if per_k < 8:
        issues.append(f"【互动不足】互动标记密度 {per_k:.1f}/千字，低于 8。讲解偏念稿")
    elif per_k < 12:
        warns.append(f"互动密度 {per_k:.1f}/千字，偏低（建议 ≥12）")
    else:
        passes.append(f"互动密度：{per_k:.1f}/千字")

    # 逐题互动检查
    weak = []
    blocks = re.split(r"\*{0,2}(?:课前测第\d题|例题\d|随堂练习题\d)\*{0,2}", t)
    for idx, b in enumerate(blocks[1:], 1):
        b = b[:1400]
        n = sum(b.count(k) for k in INTERACT)
        if n < 2:
            weak.append(f"第{idx}块({n})")
    if weak:
        issues.append(f"【互动不足】{len(weak)} 道题的讲评互动标记少于 2 个：{', '.join(weak[:6])}")
    elif not weak:
        passes.append("逐题互动：每道题讲评均有互动")

    # ── 9 丝滑度：段落连接词 ──
    body = [p for p in paras if len(p) > 12 and not p.startswith("题目：")
            and not re.match(r"^\*{0,2}(答案|解析|课前测|例题|随堂)", p)]
    linked = sum(1 for p in body if any(p.startswith(cw) for cw in CONNECT))
    ratio = linked / max(len(body), 1) * 100
    if ratio < 12:
        issues.append(f"【不够丝滑】仅 {ratio:.0f}% 的段落有承接词开头，段与段是硬切")
    elif ratio < 18:
        warns.append(f"段落承接率 {ratio:.0f}%，可再加连接词")
    else:
        passes.append(f"段落承接率：{ratio:.0f}%")

    # ── 10 句长：书面化检测 ──
    sents = [s for s in re.split(r"[。！？]", t) if zh(s) > 0]
    avg = sum(zh(s) for s in sents) / max(len(sents), 1)
    long_n = sum(1 for s in sents if zh(s) > 55)
    if avg > 32:
        issues.append(f"【书面化】平均句长 {avg:.0f} 字，偏长。口播稿应多用短句")
    elif avg > 27:
        warns.append(f"平均句长 {avg:.0f} 字，可再断碎一些")
    else:
        passes.append(f"平均句长：{avg:.0f} 字")
    if long_n > 6:
        warns.append(f"{long_n} 句超过 55 字，建议拆开")

    # ── 11 格式残留 ──
    for bad, why in [("|", "疑似表格残留"), ("•", "项目符号"), ("- ", "列表符号")]:
        if bad in t and t.count(bad) > 3:
            warns.append(f"疑似 {why}：出现 {t.count(bad)} 次「{bad}」")

    return name, total_zh, issues, warns, passes


def report(path):
    name, n, issues, warns, passes = check(path)
    print("─" * 66)
    print(f"  {name}   {n} 字")
    print("─" * 66)
    if issues:
        print(f"  不合格 {len(issues)} 项：")
        for x in issues:
            print(f"    ✗ {x}")
    if warns:
        print(f"  提醒 {len(warns)} 项：")
        for x in warns:
            print(f"    ! {x}")
    if passes:
        print(f"  通过 {len(passes)} 项：")
        for x in passes:
            print(f"    ✓ {x}")
    verdict = "合格" if not issues else "退回重做"
    print(f"\n  判定：{verdict}\n")
    return len(issues)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--all":
        files = sorted(glob.glob("docs/Level*/*.docx")) or sorted(glob.glob("*.docx"))
    else:
        files = args
    print("\n" + "=" * 66)
    print("  语法检察官 v1.0 · 高中英语语法切片逐字稿检查")
    print("=" * 66 + "\n")
    bad = sum(report(f) for f in files)
    print("=" * 66)
    print(f"  共检查 {len(files)} 份，问题合计 {bad} 项")
    print("=" * 66 + "\n")
    sys.exit(1 if bad else 0)
