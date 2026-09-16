#!/usr/bin/env python3
"""build_dashboard.py — 把本项目研究成果生成为单文件可视化 HTML 仪表盘。

数据契约见同目录 ../SKILL.md。用法：
    python3 build_dashboard.py [--root REPO根] [--out 输出路径] [--open]
"""
import argparse, csv, glob, json, os, re, subprocess, sys
from datetime import datetime

# ---------- 基础工具 ----------

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def slice_after(text, anchor, level=2):
    """返回包含 anchor 的标题行之后、下一个同级标题之前的文本。"""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if anchor in ln and ln.lstrip().startswith("#" * level) and not ln.lstrip().startswith("#" * (level + 1)):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    pat = "\n" + "#" * level + " " if level > 0 else None
    for j in range(start, len(lines)):
        if lines[j].startswith("#" * level + " ") or lines[j].startswith("#" * (level + 1) + " "):
            end = j
            break
    return "\n".join(lines[start:end])

def pipe_table(text):
    """取第一段连续管道表 → 行单元格列表（剔除分隔行）。"""
    rows, in_tbl = [], False
    for ln in text.split("\n"):
        if ln.strip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(set(c) <= {"-", " ", ":"} for c in cells):
                continue
            rows.append(cells)
            in_tbl = True
        elif in_tbl:
            break
    return rows

def num_list(text):
    out = []
    for ln in text.split("\n"):
        m = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m:
            out.append(m.group(1).strip())
    return out

def fallback_table(rows, ncols):
    return rows if rows else [["（未解析到，检查 SKILL.md 数据契约锚点）"] * ncols]

# ---------- 1. companies.csv ----------

def load_companies(root):
    path = os.path.join(root, "data", "companies.csv")
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append({
                "layer": r.get("层级", "").strip(),
                "track": r.get("细分赛道", "").strip(),
                "name": r.get("公司", "").strip(),
                "product": r.get("核心产品", "").strip(),
                "status": r.get("上市状态", "").strip(),
                "code": r.get("代码/市场", "").strip(),
                "foreign": r.get("海外主导者", "").strip(),
                "localization": r.get("国产化率(约)", "").strip(),
                "stars": len(re.findall("★", r.get("卡脖子等级", ""))),
                "evidence": r.get("证据等级", "").strip(),
                "note": r.get("备注", "").strip(),
                "ai_share": r.get("AI收入占比/弹性", "").strip(),
                "supply_risk": r.get("供应链风险", "").strip(),
                "overseas": r.get("海外产能", "").strip(),
            })
    return out

def build_chain(companies):
    layers, layer_idx = [], {}
    for i, c in enumerate(companies):
        if c["layer"] not in layer_idx:
            layer_idx[c["layer"]] = {"layer": c["layer"], "tmap": {}, "tracks": []}
            layers.append(layer_idx[c["layer"]])
        L = layer_idx[c["layer"]]
        if c["track"] not in L["tmap"]:
            L["tmap"][c["track"]] = {"track": c["track"], "localization": "", "stars": 0, "items": []}
            L["tracks"].append(L["tmap"][c["track"]])
        T = L["tmap"][c["track"]]
        T["items"].append(i)
        if not T["localization"] and c["localization"]:
            T["localization"] = c["localization"]
        T["stars"] = max(T["stars"], c["stars"])
    for L in layers:
        L["tracks"] = [{k: t[k] for k in ("track", "localization", "stars", "items")} for t in L["tracks"]]
        del L["tmap"]
    return layers

# ---------- 2. FRED ----------

FRED_META = {
    "DGS10":        ("10Y 名义国债利率", "rate", 5.0),
    "DFII10":       ("10Y 实际利率（TIPS）", "rate", 2.5),
    "BAMLH0A0HYM2": ("高收益债利差 HY OAS", "rate", 3.0),
    "VIXCLS":       ("VIX 波动率指数", "rate", 25.0),
    "DCOILBRENTEU": ("Brent 原油", "index", None),
    "DFF":          ("联邦基金有效利率", "rate", None),
    "FEDFUNDS":     ("联邦基金利率（月均）", "rate", None),
    "NASDAQCOM":    ("纳斯达克综合指数", "index", None),
    "SP500":        ("标普 500", "index", None),
    "T10YIE":       ("10Y 盈亏平衡通胀", "rate", None),
    "T5YIFR":       ("5y5y 远期通胀预期", "rate", None),
    "CPIAUCSL":     ("CPI 指数（季调）", "index", None),
}

def load_fred(root):
    out = {}
    md = os.path.join(root, "data", "market_data")
    for path in sorted(glob.glob(os.path.join(md, "*.csv"))):
        key = os.path.splitext(os.path.basename(path))[0]
        if key not in FRED_META:
            continue
        dates, vals = [], []
        with open(path, encoding="utf-8") as f:
            rdr = csv.reader(f)
            header = next(rdr, None)
            if not header or "date" not in header[0].lower():
                continue
            for row in rdr:
                if len(row) < 2 or row[1].strip() in ("", ".", "NA"):
                    continue
                try:
                    dates.append(row[0]); vals.append(float(row[1]))
                except ValueError:
                    continue
        if len(vals) < 2:
            continue
        label, kind, threshold = FRED_META[key]
        latest, latest_date = vals[-1], dates[-1]
        year = latest_date[:4]
        ytd_ref = next((v for d, v in zip(dates, vals) if d[:4] == year), vals[0])
        chg = latest - ytd_ref
        ytd_change = f"{chg:+.1f}%" if kind == "index" else f"{chg:+.2f}pp"
        step = max(1, len(vals) // 550)
        s_dates, s_vals = dates[::step], vals[::step]
        if s_dates[-1] != dates[-1]:
            s_dates.append(dates[-1]); s_vals.append(vals[-1])
        out[key] = {"label": label, "kind": kind, "threshold": threshold,
                    "dates": s_dates, "values": [round(v, 2) for v in s_vals],
                    "latest": round(latest, 2), "latest_date": latest_date,
                    "ytd_change": ytd_change}
    return out

# ---------- 3. MACRO_PANEL / QUOTES ----------

LEVEL = {"🟢": "ok", "⏳": "watch", "🔴": "hit"}

def load_macro(root):
    path = os.path.join(root, "data", "market_data", "MACRO_PANEL.md")
    if not os.path.exists(path):
        return {"sections": []}
    sections = []
    for block in re.split(r"^## ", read(path), flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"(\d+)\.\s+(.+?)(?:\s+—\s+(🟢|⏳|🔴)\s*(\S+))?\s*$", head.strip())
        if not m:
            continue
        latest = None
        lm = re.search(r"最新[^*\n]*?\*\*([^*]+)\*\*", body)
        if lm:
            latest = lm.group(1).strip()
        tbl = pipe_table(body)
        bullets = [re.sub(r"^-\s+", "", ln).strip() for ln in body.split("\n") if ln.strip().startswith("- ")]
        sections.append({"no": int(m.group(1)), "name": m.group(2).strip(),
                         "level": LEVEL.get(m.group(3)) if m.group(3) else None,
                         "status": (m.group(4) or "").strip(),
                         "latest": latest, "bullets": bullets,
                         "table": tbl if len(tbl) > 1 else None})
    return {"sections": sections}

def load_quotes(root):
    path = os.path.join(root, "data", "market_data", "QUOTES_SNAPSHOT.md")
    if not os.path.exists(path):
        return []
    groups = []
    for block in re.split(r"^## ", read(path), flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        rows = pipe_table(body)
        if len(rows) > 1:
            groups.append({"group": head.strip(), "rows": rows[1:]})
    return groups

# ---------- 4. 文档（03/06/07） ----------

def find_doc(root, prefix):
    hits = glob.glob(os.path.join(root, f"{prefix}_*.md"))
    return hits[0] if hits else None

def load_doc03(root):
    p = find_doc(root, "03")
    if not p:
        return {}
    t = read(p)
    return {
        "equipment": fallback_table(pipe_table(slice_after(t, "〇、半导体设备")), 4),
        "aClass": fallback_table(pipe_table(slice_after(t, "二、A 类")), 6),
        "bClass": fallback_table(pipe_table(slice_after(t, "三、B 类")), 6),
        "cClass": fallback_table(pipe_table(slice_after(t, "四、C 类")), 3),
        "tracking": num_list(slice_after(t, "五、跟踪清单")),
    }

def load_doc06(root):
    p = find_doc(root, "06")
    if not p:
        return {}
    t = read(p)
    vm = re.search(r"\*\*综合判断：(.*?)\*\*", t)
    return {
        "chain": pipe_table(slice_after(t, "一、结论")),
        "verdict": pipe_table(slice_after(t, "五、性质 vs 时点分离表")),
        "watchlist": pipe_table(slice_after(t, "六、观察清单")),
        "verdict_text": vm.group(1).strip() if vm else "",
    }

def load_doc07(root):
    p = find_doc(root, "07")
    if not p:
        return {}
    t = read(p)
    sdllmtk = []
    for row in pipe_table(slice_after(t, "一、先修正前提"))[1:]:
        if len(row) < 3:
            continue
        vm = re.search(r"\$?\s*([\d.]+)", row[2].replace("**", ""))
        if vm:
            sdllmtk.append({"date": row[1].strip("* "), "value": float(vm.group(1)),
                            "note": row[0].strip("* ")})
    scenarios = []
    for m in re.finditer(r"^### 情景([一二三])：(.+)$", t, flags=re.M):
        title = m.group(2)
        pm = re.search(r"~?(\d+)(?:-(\d+))?%", title)
        name = re.sub(r"（[^）]*）.*$", "", title).strip(" —")
        after = t[m.end():m.end() + 600]
        dm = re.search(r"-\s*\*\*形态\*\*：(.+)", after)
        scenarios.append({"name": name,
                          "prob": int(pm.group(1)) if pm else 0,
                          "probHi": int(pm.group(2)) if pm and pm.group(2) else None,
                          "text": dm.group(1).strip() if dm else ""})
    scenarios.sort(key=lambda s: s["name"])
    return {
        "sdllmtk": sdllmtk,
        "list": scenarios,
        "tiers": fallback_table(pipe_table(slice_after(t, "四、脆弱性排序")), 4),
        "timeline": fallback_table(pipe_table(slice_after(t, "五、前瞻时间表")), 3),
        "predictions": fallback_table(pipe_table(slice_after(t, "六、可证伪的前瞻判断")), 4),
        "addon_t": pipe_table(slice_after(t, "八、监控仪表盘增补")),
    }

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--out", default=None)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    companies = load_companies(root)
    if not companies:
        sys.exit("❌ data/companies.csv 解析为空，检查文件与列名")
    fred = load_fred(root)
    macro = load_macro(root)
    quotes = load_quotes(root)
    sub = load_doc03(root)
    causal = load_doc06(root)
    scen = load_doc07(root)
    addon = scen.pop("addon_t", [])
    if len(addon) > 1:
        scen["addon"] = addon

    payload = {
        "meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "source_repo": os.path.basename(root)},
        "companies": companies,
        "chain": build_chain(companies),
        "fred": fred,
        "macro": macro,
        "quotes": quotes,
        "substitution": sub,
        "causal": causal,
        "scenarios": scen,
    }

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template = read(os.path.join(skill_dir, "assets", "template.html"))
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("__DATA_JSON__", js)
    if "__DATA_JSON__" in html:
        sys.exit("❌ 模板占位符未替换")

    out = args.out or os.path.join(root, "dashboard", "index.html")
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    n_tables = sum(len(v) > 0 for v in sub.values()) + len(causal) + len(scen)
    print(f"✅ {out}  ({os.path.getsize(out)/1024:.0f} KB)")
    print(f"   公司 {len(companies)} | 赛道 {sum(len(l['tracks']) for l in payload['chain'])} | "
          f"FRED {len(fred)} 序列 | 触发器 {len(macro['sections'])} | 行情组 {len(quotes)} | 文档表 {n_tables}")
    for name, val in [("03 设备表", sub.get("equipment")), ("03 A类", sub.get("aClass")),
                      ("03 B类", sub.get("bClass")), ("03 C类", sub.get("cClass")),
                      ("03 跟踪", sub.get("tracking")), ("06 链环", causal.get("chain")),
                      ("07 SDLLMTK", scen.get("sdllmtk")), ("07 情景", scen.get("list")),
                      ("07 预测", scen.get("predictions")), ("07 增补", scen.get("addon"))]:
        n = len(val) if val else 0
        flag = "✅" if n else "⚠️ "
        print(f"   {flag} {name}: {n}")
    if args.open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, out], check=False)

if __name__ == "__main__":
    main()
