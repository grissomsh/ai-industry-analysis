#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股(腾讯) + 美股(新浪) 行情快照 → markdown 对照表。

用途: 验证"板块结构"——是普跌(撤离)还是分化(轮动), 以及 A 股海外链 vs 国产链联动。

用法:
  python3 fetch_quotes.py --mode both                    # 默认: AI算力双链
  python3 fetch_quotes.py --mode cn --cn sz300308,sh688256
  python3 fetch_quotes.py --mode us --us NVDA,MSFT
依赖: requests
"""
import argparse
import os
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ── 默认标的: AI 算力产业链双链对照 ──
CN_OVERSEAS = {  # 海外算力链(收入与美股 AI capex 挂钩)
    "sz300308": "中际旭创", "sz300502": "新易盛", "sz300394": "天孚通信",
    "sz002463": "沪电股份", "sz300476": "胜宏科技",
}
CN_DOMESTIC = {  # 国产算力链(驱动=国内智算capex+国产化政策)
    "sh688256": "寒武纪", "sh688795": "摩尔线程", "sh688802": "沐曦股份",
    "sh603019": "中科曙光", "sh688041": "海光信息",
}
US_CHIPS = ["NVDA", "AMD", "AVGO"]                # 卖家: 芯片
US_HYPERSCALERS = ["MSFT", "GOOGL", "AMZN", "META"]  # 买家: 超大规模厂商


def cn_quotes(codes: list[str]) -> str:
    """腾讯行情: GBK 编码; 字段 f[3]现价 f[32]涨跌% f[39]PE-TTM f[44]流通市值 f[45]总市值(亿)。"""
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    r = requests.get(url, timeout=20, headers=UA)
    r.encoding = "gbk"
    rows = []
    for m in re.finditer(r'v_(\w+)="([^"]+)"', r.text):
        f = m.group(2).split("~")
        if len(f) < 46:
            continue
        try:
            rows.append({
                "code": m.group(1), "name": f[1], "price": float(f[3]),
                "pct": float(f[32]), "pe": f[39] or "-", "mcap": f[45] or "-",
            })
        except (ValueError, IndexError):
            continue
    if not rows:
        return "⚠️ A股行情拉取失败（腾讯接口）\n"
    lines = ["| 代码 | 公司 | 现价 | 今日涨跌 | PE-TTM | 总市值(亿) |", "|---|---|---|---|---|---|"]
    for x in rows:
        lines.append(f"| {x['code']} | {x['name']} | {x['price']:.2f} | {x['pct']:+.2f}% | {x['pe']} | {x['mcap']} |")
    return "\n".join(lines) + "\n"


def us_quotes(codes: list[str]) -> str:
    """新浪美股: 须带 Referer; 锚点字段 f[0]名称 f[1]现价 f[2]涨跌% f[3]时间。
    市值用数值扫描定位(f[10]~f[15] 中 1e10~1e13 量级的数)——字段位置随批次漂移且含空字段,
    固定索引解析会在 float('') 上炸掉整行(实测坑)。"""
    h = dict(UA, Referer="https://finance.sina.com.cn")
    url = "https://hq.sinajs.cn/list=" + ",".join(f"gb_{c.lower()}" for c in codes)
    r = requests.get(url, timeout=20, headers=h)
    r.encoding = "gbk"
    rows = []
    for line in r.text.strip().split("\n"):
        if '="' not in line:
            continue
        sym = line.split("=")[0].split("_")[-1].upper()
        f = line.split('"')[1].split(",")
        if len(f) < 16:
            continue
        try:
            price, pct = float(f[1]), float(f[2])
        except (ValueError, IndexError):
            continue
        mcap_tn = None
        for x in f[10:16]:
            try:
                v = float(x)
            except ValueError:
                continue
            if 1e10 <= v <= 1e13:  # 市值: 100亿~10万亿美元
                mcap_tn = v / 1e12
                break
        rows.append({
            "sym": sym, "name": f[0], "price": price, "pct": pct,
            "time": f[3], "mcap_tn": mcap_tn,
        })
    if not rows:
        return "⚠️ 美股行情拉取失败（新浪接口, 检查 Referer）\n"
    lines = ["| 代码 | 公司 | 现价 | 涨跌 | 市值(万亿$) | 快照时间 |", "|---|---|---|---|---|---|"]
    for x in rows:
        mcap = f"{x['mcap_tn']:.2f}" if x["mcap_tn"] else "-"
        lines.append(f"| {x['sym']} | {x['name']} | {x['price']:.2f} | {x['pct']:+.2f}% | {mcap} | {x['time']} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["cn", "us", "both"], default="both")
    ap.add_argument("--cn", help="自定义A股代码(逗号分隔), 如 sz300308,sh688256")
    ap.add_argument("--us", help="自定义美股代码, 如 NVDA,MSFT")
    ap.add_argument("--outdir", default=".", help="输出目录")
    args = ap.parse_args()

    md = ["# 行情结构快照\n"]
    if args.mode in ("cn", "both"):
        if args.cn:
            codes = [c.strip() for c in args.cn.split(",")]
            md.append("## A股（自定义）\n")
            md.append(cn_quotes(codes))
        else:
            md.append("## A股 · 海外算力链（光模块/PCB，跟随美股 AI capex）\n")
            md.append(cn_quotes(list(CN_OVERSEAS)))
            md.append("\n## A股 · 国产算力链（跟随国内智算 capex + 国产化政策）\n")
            md.append(cn_quotes(list(CN_DOMESTIC)))
    if args.mode in ("us", "both"):
        if args.us:
            codes = [c.strip().upper() for c in args.us.split(",")]
            md.append("## 美股（自定义）\n")
            md.append(us_quotes(codes))
        else:
            md.append("## 美股 · 芯片（AI 卖家）\n")
            md.append(us_quotes(US_CHIPS))
            md.append("\n## 美股 · 超大规模厂商（AI 买家）\n")
            md.append(us_quotes(US_HYPERSCALERS))

    md.append("\n## 结构判读规则\n")
    md.append("- **轮动**（非撤离）: 芯片/卖家跌 + 超大规模/买家涨 → 市场定价\"买家少花钱=FCF改善\", 定价范式从奖建转奖省")
    md.append("- **撤离**: 芯片与买家普跌 → 风险偏好整体收缩")
    md.append("- **A股联动验证**: 海外链与美股芯片同向、国产链独立走强 → \"国产算力脱钩\"假设成立当日即可验证")
    out = os.path.join(args.outdir, "QUOTES_SNAPSHOT.md")
    os.makedirs(args.outdir, exist_ok=True)
    with open(out, "w") as f:
        f.write("\n".join(md))
    print("\n".join(md))
    print(f"\n[快照已保存: {out}]")


if __name__ == "__main__":
    main()
