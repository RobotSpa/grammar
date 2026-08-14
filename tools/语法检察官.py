#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语法检察官
高中英语语法切片 · 逐字稿质量智能体

一条命令，全自动：构建 → 审查 → 自动修正源码 → 重建 → 复审，循环到合格为止。

    python3 语法检察官.py                 处理全部课程，自动修正
    python3 语法检察官.py 03 05           只处理指定课次
    python3 语法检察官.py --check         只审查，不修正
    python3 语法检察官.py --commit        全部合格后自动提交 GitHub
    python3 语法检察官.py --watch         守候模式：源码一改动就自动跑

修正直接作用在 .js 源码上，所以重新生成也不会丢。
每轮修正前自动备份，构建失败立即回滚。
"""

import os
import re
import io
import sys
import json
import time
import glob
import datetime
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "..", "proj")
DOCS = os.path.join(ROOT, "docs", "Level1")
RULES = os.path.join(ROOT, "tools", "铁律.json")
LOG = os.path.join(ROOT, "tools", "审查日志.md")


def load_rules():
    with io.open(RULES, encoding="utf-8") as f:
        return json.load(f)


def save_rules(r):
    r["更新于"] = datetime.date.today().isoformat()
    with io.open(RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)


RB = load_rules()
IRON = RB["铁律"]
TH = RB["阈值"]

# ══════════════════════════════════════════════════════════
#  第一章  违禁词：低龄化包装、营销腔、书面标记
# ══════════════════════════════════════════════════════════
BANNED = RB["违禁词"]

# ══════════════════════════════════════════════════════════
#  第二章  术语红线：说错就是硬伤
# ══════════════════════════════════════════════════════════
TERM_RED = [tuple(x) for x in RB["术语红线"]]

# ══════════════════════════════════════════════════════════
#  第三章  互动标记：讲题必须跟学生对话
# ══════════════════════════════════════════════════════════
INTERACT = RB["互动词表"]

# 段落连接词：丝滑度指标
CONNECT = tuple(RB["承接词表"])

# ══════════════════════════════════════════════════════════
#  第四章  结构规格
# ══════════════════════════════════════════════════════════
SPEC = RB["题量规格"]
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
        if re.match(r"^第[一二三四五六七八九十]+[种组对个类]", p):
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
    if per_k < TH["互动密度_每千字"]:
        issues.append(f"【互动不足】互动标记密度 {per_k:.1f}/千字，低于 " + str(TH["互动密度_每千字"]) + "。讲解偏念稿")
    elif per_k < TH["互动密度_目标"]:
        warns.append(f"互动密度 {per_k:.1f}/千字，偏低（建议 ≥{TH['互动密度_目标']}）")
    else:
        passes.append(f"互动密度：{per_k:.1f}/千字")

    # 逐题互动检查
    weak = []
    blocks = re.split(r"\*{0,2}(?:课前测第\d题|例题\d|随堂练习题\d)\*{0,2}", t)
    for idx, b in enumerate(blocks[1:], 1):
        b = b[:1400]
        n = sum(b.count(k) for k in INTERACT)
        if n < TH["逐题互动_每题最少"]:
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
    if ratio < TH["段落承接率"]:
        issues.append(f"【不够丝滑】仅 {ratio:.0f}% 的段落有承接词开头，段与段是硬切")
    elif ratio < TH["段落承接率_目标"]:
        warns.append(f"段落承接率 {ratio:.0f}%，可再加连接词")
    else:
        passes.append(f"段落承接率：{ratio:.0f}%")

    # ── 10 句长：书面化检测 ──
    sents = [s for s in re.split(r"[。！？]", t) if zh(s) > 0]
    avg = sum(zh(s) for s in sents) / max(len(sents), 1)
    long_n = sum(1 for s in sents if zh(s) > TH["超长句_字数线"])
    if avg > TH["平均句长_上限"]:
        issues.append(f"【书面化】平均句长 {avg:.0f} 字，偏长。口播稿应多用短句")
    elif avg > TH["平均句长_目标"]:
        warns.append(f"平均句长 {avg:.0f} 字，可再断碎一些")
    else:
        passes.append(f"平均句长：{avg:.0f} 字")
    if long_n > TH["超长句_上限句数"]:
        warns.append(f"{long_n} 句超过 55 字，建议拆开")

    # ── 11 格式残留 ──
    for bad, why in [("|", "疑似表格残留"), ("•", "项目符号"), ("- ", "列表符号")]:
        if bad in t and t.count(bad) > 3:
            warns.append(f"疑似 {why}：出现 {t.count(bad)} 次「{bad}」")

    return name, total_zh, issues, warns, passes



# ══════════════════════════════════════════════
#  修正素材库
# ══════════════════════════════════════════════

# 罗列体：把「第N种/组/对」改成自然口语开头
ENUM_OPEN = {
    "种": ["最简单的是", "然后是", "再往下是", "还有一种是", "最后一种是"],
    "组": ["最基本的是", "然后是", "再往下是", "最后一组是"],
    "对": ["先说", "再说", "最后是"],
    "个": ["先说一个，", "再说一个，", "还有一个，", "最后一个，"],
    "类": ["最常见的是", "然后是", "再往下是", "最后一类是"],
}

# 题目衔接语
LEAD_PRE = ["我们先来看第一题。", "好，第一题就到这儿。接下来第二题。", "来，第三题。",
            "第四题。这道题稍微绕一点，你仔细看。", "接着看第五题。",
            "最后一道，第六题。做完这道我们就正式开讲。"]
LEAD_EX = ["我们先看第一道例题。", "好，接下来第二道例题。"]
LEAD_HW = ["我们先来看第一题。", "接下来第二题。", "来，第三题。",
           "第四题。", "第五题，接着看。", "最后一题了。"]

# 讲题开场的互动句（按题型轮换，避免雷同）
OPEN_INTERACT = RB["互动词表"]

# 段落承接词（语义中性，前置安全）
CONNECT_IN = ["那", "好，", "接着说，", "再看，"]

# 已在 inspector 中定义，这里复用判定


def sh(cmd, cwd=None):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)


def lessons(filter_ids=None):
    out = []
    for js in sorted(glob.glob(os.path.join(SRC, "lesson*.js"))):
        b = os.path.basename(js)
        if b in ("lib.js",) or "_old" in b:
            continue
        m = re.search(r"lesson(\d+)", b)
        if not m:
            continue
        n = m.group(1)[:2]
        if filter_ids and n not in filter_ids:
            continue
        out.append((n, js))
    # 去重：同一课次只保留版本号最大的源码（lesson01_v9 > lesson01_v2 > lesson01）
    best = {}
    for n, js in out:
        v = re.search(r"_v(\d+)\.js$", js)
        ver = int(v.group(1)) if v else 0
        if n not in best or ver > best[n][0]:
            best[n] = (ver, js)
    return sorted((n, j) for n, (v, j) in best.items())


def build(js):
    r = sh(f"node {os.path.basename(js)}", cwd=SRC)
    m = re.search(r"saved:?\s*(.*)", r.stdout)
    return r.returncode == 0


def docx_of(n):
    g = glob.glob(os.path.join(DOCS, f"*第{n}课*.docx"))
    return g[0] if g else None


def sync(n):
    """把 outputs 里刚生成的 docx 同步进仓库"""
    src = glob.glob(f"/mnt/user-data/outputs/*第{n}课*.docx")
    if src:
        sh(f'cp "{src[0]}" "{DOCS}/"')
        return True
    return False


# ══════════════════════════════════════════════
#  修正器：直接改 .js 源码
# ══════════════════════════════════════════════

def para_strings(s):
    """只取 add(P("...")) 里的段落——这是唯一可安全插入的位置"""
    return [m.group(1) for m in re.finditer(r'add\(P\("([^"\\]{6,})"\)\)', s)
            if len(re.findall(r"[\u4e00-\u9fff]", m.group(1))) >= 5]


def all_text(s):
    """取全部会被渲染的中文段落（锚定提取，两种写法）"""
    out = []
    for pat in (r'add\(P\("([^"\\]{6,})"\)\)', r'\n\s+"([^"\\]{6,})",'):
        for m in re.finditer(pat, s):
            t = m.group(1)
            if len(re.findall(r"[\u4e00-\u9fff]", t)) < 5:
                continue
            if t.startswith(("题目", "答案", "解析", "A.", "B.", "C.")):
                continue
            out.append(t)
    return out


def strings_of(s):
    return [type("M", (), {"group": lambda self, i, t=t: t})() for t in para_strings(s)]


def fix_enumeration(s, log):
    """罗列体 → 自然口语开头"""
    n = 0
    for cls, pool in ENUM_OPEN.items():
        pat = re.compile(r'(add\(P\(["`])第([一二三四五六七八九十])' + cls + r'[，,]?\s*')
        hits = list(pat.finditer(s))
        if len(hits) < 3:
            continue
        order = "一二三四五六七八九十"
        for m in reversed(hits):
            idx = order.index(m.group(2))
            rep = pool[min(idx, len(pool) - 1)]
            if idx == len(hits) - 1 and len(pool) > 1:
                rep = pool[-1]
            s = s[:m.start()] + m.group(1) + rep + s[m.end():]
            n += 1
    if n:
        log.append(f"罗列体：改写 {n} 处模板开头")
    return s


def fix_leads(s, log):
    """题目缺衔接语 → 插入"""
    n = 0
    for i, txt in enumerate(LEAD_PRE, 1):
        tag = f'n: "课前测第{i}题",'
        if tag in s and "lead:" not in s[max(0, s.index(tag) - 90):s.index(tag)]:
            s = s.replace(tag, f'lead: "{txt}",\n  {tag}', 1); n += 1
        tag2 = f'add(F("课前测第{i}题"));'
        if tag2 in s and f'add(P("{txt}"));\n' not in s:
            prev = s[max(0, s.index(tag2) - 120):s.index(tag2)]
            if not any(k in prev for k in ["第一题", "第二题", "第三题", "接下来", "先看"]):
                s = s.replace(tag2, f'add(P("{txt}"));\n{tag2}', 1); n += 1
    for i, txt in enumerate(LEAD_EX, 1):
        tag = f'n: "例题{i}",'
        if tag in s and "lead:" not in s[max(0, s.index(tag) - 90):s.index(tag)]:
            s = s.replace(tag, f'lead: "{txt}",\n  {tag}', 1); n += 1
    for i, txt in enumerate(LEAD_HW, 1):
        tag = f'n: "随堂练习题{i}",'
        if tag in s and "lead:" not in s[max(0, s.index(tag) - 90):s.index(tag)]:
            s = s.replace(tag, f'lead: "{txt}",\n    {tag}', 1); n += 1
    if n:
        log.append(f"题目衔接：补入 {n} 句衔接语")
    return s


def fix_block_interaction(s, log):
    """逐题互动不足 → 在该题讲解开头补入互动句（数组与字符串两种写法都支持）"""
    n = 0

    def need_lines(blk):
        have = sum(blk[:1400].count(k) for k in INTERACT)
        return max(0, 3 - have)   # 留一格余量，避免渲染后落回临界

    def ins_arr(m):
        nonlocal n
        blk = m.group(0)
        k = need_lines(blk)
        if not k:
            return blk
        lines = "".join(f'\n      "{OPEN_INTERACT[(n + i) % len(OPEN_INTERACT)]}",' for i in range(k))
        n += k
        return blk.replace("say: [", "say: [" + lines, 1)

    s = re.sub(r'say: \[.*?\n\s*\]', ins_arr, s, flags=re.S)

    def ins_str(m):
        nonlocal n
        body = m.group(1)
        k = need_lines(body)
        if not k:
            return m.group(0)
        lines = ", ".join(f'"{OPEN_INTERACT[(n + i) % len(OPEN_INTERACT)]}"' for i in range(k))
        n += k
        return f'say: [{lines}, "{body}"]'

    s = re.sub(r'say: "([^"]{10,})"', ins_str, s)

    if n:
        log.append(f"逐题互动：补入 {n} 句互动开场")
    return s


def fix_manual_blocks(s, log):
    """手写 add(F(\"课前测第N题\")) 体裁：在题干之后插入互动开场"""
    n = 0
    targets = [f'add(F("课前测第{i}题"));' for i in range(1, 7)] + \
              [f'add(F("例题{i}"));' for i in range(1, 3)]
    for tag in targets:
        if tag not in s:
            continue
        at = s.index(tag)
        seg = s[at + len(tag):at + len(tag) + 1500]
        have = sum(seg.count(k) for k in INTERACT)
        if have >= 4:
            continue
        # 跳过题干行与选项行，定位到第一段讲解之前
        rest = s[at + len(tag):]
        m = re.match(r'\s*add\(F\(.*?\n(\s*add\(F\("A[^\n]*\n)?', rest, re.S)
        off = at + len(tag) + (m.end() if m else 0)
        ins = ""
        for _ in range(4 - have):
            ins += f'add(P("{OPEN_INTERACT[n % len(OPEN_INTERACT)]}"));\n'
            n += 1
        s = s[:off] + ins + s[off:]
    if n:
        log.append(f"逐题互动（手写体裁）：补入 {n} 句")
    return s


def fix_connect(s, log, need=0.22):
    """段落承接率不足 → 给正文段落加承接词（按文本替换，避免索引漂移）"""
    body = [t for t in all_text(s) if len(t) > 14]
    if not body:
        return s
    have = sum(1 for t in body if any(t.startswith(c) for c in CONNECT))
    gap = int(len(body) * need) - have
    if gap <= 0:
        return s
    cand = [t for t in body
            if not any(t.startswith(c) for c in tuple(CONNECT) + tuple(CONNECT_IN))
            and not t.startswith("师：")
            and (s.count(f'add(P("{t}"))') == 1 or s.count(f'"{t}",') == 1
                 or s.count(f'"{t}"') == 1)]
    if not cand:
        return s
    step = max(1, len(cand) // max(gap, 1))
    picked = cand[::step][:gap]
    done = 0
    for k, t in enumerate(picked):
        cw = CONNECT_IN[k % len(CONNECT_IN)]
        # 只在两种明确锚定的位置插入，杜绝匹配到字符串之间的空隙
        for a, b in ((f'add(P("{t}"))', f'add(P("{cw}{t}"))'),
                     (f'"{t}",',        f'"{cw}{t}",'),
                     (f'"{t}"',         f'"{cw}{t}"')):
            if s.count(a) == 1:
                s = s.replace(a, b, 1); done += 1; break
    if not done:
        return s
    log.append(f"段落承接：{done} 段加入承接词")
    return s


def fix_density(s, log, target=12.0):
    """整体互动密度不足 → 按缺口在正文小节间插入互动追问"""
    adds = ["这一点你听明白了吗？", "你先在心里过一遍。", "跟上了吗？我们接着说。",
            "这里你要特别注意，我问你，到这儿有没有问题？", "你自己想想是不是这个道理。",
            "我知道你可能还有点绕，别急。", "你看，是不是清楚多了？"]
    body = para_strings(s)
    txt = "".join(all_text(s))
    N = len(re.findall(r"[\u4e00-\u9fff]", txt))
    cur = sum(txt.count(k) for k in INTERACT)
    gap = int(target / 1000 * N) - cur
    if gap <= 0:
        return s
    long_ = [t for t in body if len(t) > 26 and s.count(f'add(P("{t}"))') == 1]
    if len(long_) < 5:
        return s
    k = min(gap, max(1, len(long_) // 4))
    step = max(1, len(long_) // k)
    picks = long_[::step][:k]
    for i, t in enumerate(picks):
        line = adds[i % len(adds)]
        s = s.replace(f'add(P("{t}"))', f'add(P("{line}"));\nadd(P("{t}"))', 1)
    log.append(f"互动密度：插入 {len(picks)} 句追问（缺口 {gap}）")
    return s


def fix_simple(s, log):
    """违禁词 / 术语红线 / 题面格式的确定性替换"""
    n = 0
    simple = {
        "加 is 的办法": "加 be 动词的办法",
        "加一个 is": "加一个 be 动词",
        "换成主格": "换成它指的那个人",
        "黄金判据": "判断标准",
        "百试百灵": "很好用",
        "彻底夯实": "巩固",
        "超重要": "重要",
        "太棒啦": "很好",
        "不见不散": "下节课见",
    }
    for a, b in simple.items():
        if a in s:
            s = s.replace(a, b); n += 1
    if "　　　　" in s:
        s = s.replace("　　　　　", "____________").replace("　　　　", "____________"); n += 1
    if n:
        log.append(f"确定性替换：{n} 类")
    return s


# 问题描述关键词 → 修正函数
REPAIRS = [
    (("罗列体",),                       fix_enumeration),
    (("衔接",),                         fix_leads),
    (("不够丝滑", "段落承接率"),          fix_connect),
    # 互动类不做自动代笔：机器只会灌同一套模板，反而把稿子做假。
    # 检出即退回，必须由人按当题学情重写。
    (("违禁词", "术语红线", "题面"),      fix_simple),
]


def repair(js, issues):
    s = io.open(js, encoding="utf-8").read()
    log, applied = [], set()
    for it in issues:
        for keys, fn in REPAIRS:
            if any(k in it for k in keys) and fn not in applied:
                s = fn(s, log)
                applied.add(fn)
    io.open(js, "w", encoding="utf-8").write(s)
    return log


# ══════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════

def run(ids=None, auto=True, commit=False):
    print("\n" + "═" * 68)
    print("  语法检察官 · 智能体　　自动构建 → 审查 → 修正 → 复审")
    print("═" * 68)
    print("  【铁律】" + IRON[:34] + "……")
    print(f"  规则库 v{RB['版本']}　违禁 {len(BANNED)} · 红线 {len(TERM_RED)} · "
          f"互动词 {len(INTERACT)} · 硬伤 {len(RB['硬伤清单'])}")
    print("═" * 68)
    rows = []

    todo = lessons(ids)
    if not todo:
        print("  未找到逐字稿源码"); return 1

    fail = 0
    for n, js in todo:
        print(f"\n【第 {n} 课】{os.path.basename(js)}")
        if not build(js):
            print("  ✗ 源码构建失败，已跳过（可能是上一轮修正引入语法错误）")
            fail += 1; continue
        sync(n)
        d = docx_of(n)
        if not d:
            print("  ✗ 未生成 docx"); fail += 1; continue

        for rnd in range(1, 6):
            backup = io.open(js, encoding="utf-8").read()
            name, zh, issues, warns, passes = check(d)
            if not issues:
                print(f"  第{rnd}轮审查：合格　（{zh} 字，{len(passes)} 项通过）")
                rows.append((n, zh, [], passes))
                break
            print(f"  第{rnd}轮审查：{len(issues)} 项不合格")
            for x in issues:
                print("      ✗ " + x.split("\n")[0])
            if not auto:
                fail += 1; break
            log = repair(js, issues)
            if not log:
                if any("互动" in x for x in issues):
                    print("      ! 互动不足属内容问题，机器不代笔。")
                    print("        请按当题学情手写：预判学生会怎么错、用他的话说出来、给具体场景。")
                else:
                    print("      ! 无可自动修正项，需人工重写")
                fail += 1; break
            for l in log:
                print("      → " + l)
            if not build(js):
                print("      ✗ 修正后构建失败，已回滚本轮修改")
                io.open(js, "w", encoding="utf-8").write(backup)
                build(js); fail += 1; break
            sync(n)
        else:
            print("  ✗ 五轮仍未通过，需人工介入"); fail += 1

    write_log(rows)
    print("\n" + "═" * 68)
    print(f"  处理 {len(todo)} 节　·　未通过 {fail} 节　·　已留档 {os.path.basename(LOG)}")
    print("═" * 68)

    if commit and not fail:
        sh("git add -A && git commit -q -m '智能体自动审查修正' && git push -q origin main", cwd=ROOT)
        print("  已自动提交 GitHub\n")
    return fail




# ══════════════════════════════════════════════
#  自学习：把新踩的坑沉淀进规则库
# ══════════════════════════════════════════════

def learn(kind, value, reason="", source=""):
    """把一条新教训写进铁律，此后永久生效"""
    r = load_rules()
    kind = kind.lower()
    ok = False
    if kind in ("ban", "违禁"):
        r["违禁词"][value] = reason or "新增违禁表述"
        ok = "违禁词"
    elif kind in ("term", "术语"):
        r["术语红线"].append([value, reason or "术语红线", None])
        ok = "术语红线"
    elif kind in ("interact", "互动"):
        if value not in r["互动词表"]:
            r["互动词表"].append(value)
        ok = "互动词表"
    elif kind in ("connect", "承接"):
        if value not in r["承接词表"]:
            r["承接词表"].append(value)
        ok = "承接词表"
    elif kind in ("fact", "硬伤"):
        r["硬伤清单"].append(value)
        ok = "硬伤清单"
    else:
        print("  类别只能是：违禁 / 术语 / 互动 / 承接 / 硬伤"); return 1
    r["学习记录"].append({
        "日期": datetime.date.today().isoformat(),
        "来源": source or "人工登记",
        "教训": reason or value,
        "落地": f"写入{ok}",
    })
    r["版本"] += 1
    save_rules(r)
    print(f"\n  已学会。写入{ok}：{value}")
    print(f"  规则库版本 → v{r['版本']}，此后每次审查自动生效。\n")
    return 0


def show_rules():
    r = load_rules()
    print("\n" + "═" * 68)
    print("  铁律")
    print("═" * 68)
    for line in wrap_cn(r["铁律"], 60):
        print("  " + line)
    print(f"\n  规则库 v{r['版本']}　更新于 {r['更新于']}")
    print(f"  违禁词 {len(r['违禁词'])} 条　术语红线 {len(r['术语红线'])} 条　"
          f"互动词 {len(r['互动词表'])} 个　承接词 {len(r['承接词表'])} 个　"
          f"硬伤 {len(r['硬伤清单'])} 条")
    print(f"\n  不可代笔项：{'、'.join(r['不可代笔项'])}")
    print("\n  学习记录（最近 8 条）")
    print("  " + "─" * 64)
    for x in r["学习记录"][-8:]:
        print(f"  {x['日期']}　{x['来源']}")
        for line in wrap_cn(x["教训"], 56):
            print("      " + line)
        print(f"      → {x['落地']}")
    print()


def wrap_cn(t, n):
    return [t[i:i + n] for i in range(0, len(t), n)]


def write_log(rows):
    """每次审查留档，便于回看哪类问题反复出现"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    old = io.open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else "# 审查日志\n"
    lines = [f"\n## {now}　规则库 v{RB['版本']}\n"]
    for n, zh, issues, passes in rows:
        mark = "合格" if not issues else f"退回（{len(issues)} 项）"
        lines.append(f"- **第 {n} 课**　{zh} 字　{mark}")
        for x in issues:
            lines.append(f"  - {x.split(chr(10))[0]}")
    io.open(LOG, "w", encoding="utf-8").write(old + "".join(l + "\n" for l in lines))



def watch():
    """守候模式：监听源码改动，一改就自动审查修正"""
    print("\n守候中……源码一有改动就自动审查修正。Ctrl+C 退出。\n")
    seen = {}
    for _, js in lessons():
        seen[js] = os.path.getmtime(js)
    while True:
        try:
            time.sleep(2)
            for n, js in lessons():
                mt = os.path.getmtime(js)
                if seen.get(js) != mt:
                    seen[js] = mt
                    print(f"\n检测到改动：{os.path.basename(js)}")
                    run([n])
                    seen[js] = os.path.getmtime(js)
        except KeyboardInterrupt:
            print("\n已退出守候。\n"); return


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--rules" in a:
        show_rules(); sys.exit(0)
    if "--learn" in a:
        i = a.index("--learn")
        rest = a[i + 1:]
        if len(rest) < 2:
            print("\n  用法：--learn <类别> <内容> [原因] [来源]")
            print("  类别：违禁 / 术语 / 互动 / 承接 / 硬伤\n")
            sys.exit(1)
        sys.exit(learn(rest[0], rest[1],
                       rest[2] if len(rest) > 2 else "",
                       rest[3] if len(rest) > 3 else ""))
    if "--watch" in a:
        watch(); sys.exit(0)
    ids = [x for x in a if x.isdigit()]
    sys.exit(run(ids or None, auto="--check" not in a, commit="--commit" in a))
