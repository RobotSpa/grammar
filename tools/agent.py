#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语法检察官 · 智能体 v1.0

一条命令，全自动：构建 → 审查 → 自动修正源码 → 重建 → 复审，循环到合格为止。

    python3 tools/agent.py              # 全部课程，自动修正
    python3 tools/agent.py --check      # 只审查不修正
    python3 tools/agent.py --commit     # 修正后自动提交 GitHub
    python3 tools/agent.py 03 05        # 只处理指定课次

修正作用在 .js 源码上，所以修好之后重新生成也不会丢。
"""

import os
import re
import io
import sys
import glob
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "..", "proj")          # 逐字稿源码目录
DOCS = os.path.join(ROOT, "docs", "Level1")
sys.path.insert(0, os.path.join(ROOT, "tools"))
from inspector import check  # noqa

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
OPEN_INTERACT = [
    "这道题你先自己做一遍，做完再听我讲。",
    "先别急着往下看，你自己想十秒。",
    "来，跟我一起走一遍流程。",
    "这道题你先在心里过一遍，我再说。",
    "先自己判断一下，暂停五秒。",
    "你先看清楚题干，我们一步一步来。",
]

# 段落承接词（语义中性，前置安全）
CONNECT_IN = ["那", "好，", "接着说，", "再看，"]

# 已在 inspector 中定义，这里复用判定
from inspector import BANNED, INTERACT, CONNECT  # noqa


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
    (("道题的讲评互动",),                fix_block_interaction),
    (("道题的讲评互动",),                fix_manual_blocks),
    (("不够丝滑", "段落承接率"),          fix_connect),
    (("互动标记密度",),                  fix_density),
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
                break
            print(f"  第{rnd}轮审查：{len(issues)} 项不合格")
            for x in issues:
                print("      ✗ " + x.split("\n")[0])
            if not auto:
                fail += 1; break
            log = repair(js, issues)
            if not log:
                print("      ! 无可自动修正项，需人工重写"); fail += 1; break
            for l in log:
                print("      → " + l)
            if not build(js):
                print("      ✗ 修正后构建失败，已回滚本轮修改")
                io.open(js, "w", encoding="utf-8").write(backup)
                build(js); fail += 1; break
            sync(n)
        else:
            print("  ✗ 五轮仍未通过，需人工介入"); fail += 1

    print("\n" + "═" * 68)
    print(f"  处理 {len(todo)} 节　·　未通过 {fail} 节")
    print("═" * 68)

    if commit and not fail:
        sh("git add -A && git commit -q -m '智能体自动审查修正' && git push -q origin main", cwd=ROOT)
        print("  已自动提交 GitHub\n")
    return fail


if __name__ == "__main__":
    a = sys.argv[1:]
    ids = [x for x in a if x.isdigit()]
    sys.exit(run(ids or None,
                 auto="--check" not in a,
                 commit="--commit" in a))
