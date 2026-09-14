#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRED 宏观序列拉取 + 触发器面板生成（免 API key，走 fredgraph.csv 公共端点）。

用法:
  python3 fetch_fred.py --outdir ./data/market_data            # 默认全套
  python3 fetch_fred.py --series DGS10,DFII10 --start 2026-01-01
  python3 fetch_fred.py --panel-only                            # 只重算面板(用已下载CSV)

依赖: pandas, requests
"""
import argparse
import io
import os
import sys

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# 默认全套序列: 宏观触发器面板所需
SERIES = {
    "DGS10": "10Y名义利率",
    "DFII10": "10Y实际利率(TIPS)",
    "T10YIE": "10Y盈亏平衡通胀",
    "BAMLH0A0HYM2": "高收益债利差HY OAS",
    "VIXCLS": "VIX",
    "NASDAQCOM": "纳斯达克",
    "SP500": "标普500",
    "DCOILBRENTEU": "Brent油价",
    "DFF": "联邦基金有效利率(日)",
    "FEDFUNDS": "联邦基金利率(月)",
    "CPIAUCSL": "CPI(季调)",
    "T5YIFR": "5y5y远期通胀",
}


def fetch_one(sid: str, start: str, outdir: str) -> pd.DataFrame:
    """下载单个序列为 CSV 并返回 DataFrame。"""
    path = os.path.join(outdir, f"{sid}.csv")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    if "observation_date" not in r.text[:200]:
        raise ValueError(f"{sid} 返回异常: {r.text[:120]}")
    with open(path, "w") as f:
        f.write(r.text)
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["observation_date"])
    df = df.rename(columns={"observation_date": "date", sid: "v"})
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.dropna().reset_index(drop=True)


def load_local(sid: str, outdir: str) -> pd.DataFrame | None:
    path = os.path.join(outdir, f"{sid}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["observation_date"])
    df = df.rename(columns={"observation_date": "date", sid: "v"})
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.dropna().reset_index(drop=True)


def ytd(df: pd.DataFrame, year: int | None = None):
    year = year or int(df["date"].dt.year.max())
    return df[df["date"].dt.year == year]


def panel(outdir: str) -> str:
    """基于已下载序列计算触发器面板, 返回 markdown。"""
    L: list[str] = []
    d = {sid: load_local(sid, outdir) for sid in SERIES}

    def need(sid):
        df = d.get(sid)
        if df is None or df.empty:
            return None
        return df

    L.append("# 宏观触发器面板（FRED 一手数据）\n")

    # ── 1. 10Y 名义: 夏尔马 5% 触发器 ──
    df = need("DGS10")
    if df is not None:
        last = df.iloc[-1]
        n5 = int((df.tail(60)["v"] >= 5.0).sum())
        status = "🔴 已触发" if n5 >= 5 else ("⏳ 测试中" if last["v"] >= 4.9 else "🟢 未触发")
        L.append(f"## 1. 10Y 名义利率（夏尔马: 连续5日收盘>5% = 果断突破）— {status}\n")
        L.append(f"- 最新收盘: **{last['v']:.2f}%**（{last['date'].date()}，FRED 约滞后 2-4 个交易日）")
        L.append(f"- 近 60 个交易日收盘 ≥5% 天数: **{n5}**\n")

    # ── 2. 10Y 实际利率: 凯投宏观 2.5% 触发器 ──
    df = need("DFII10")
    if df is not None:
        y = ytd(df)
        last = df.iloc[-1]
        days = int((y["v"] >= 2.5).sum())
        status = "🔴 已触发" if last["v"] >= 2.5 else "🟢 未触发"
        L.append(f"## 2. 10Y 实际利率（凯投宏观触发器 ≥2.5%）— {status}\n")
        L.append(f"- 最新: **{last['v']:.2f}%**（{last['date'].date()}）| 年初: {y.iloc[0]['v']:.2f}% | 年内高点: {y['v'].max():.2f}%")
        L.append(f"- 年内 ≥2.5% 天数: **{days}/{len(y)}**")
        # 交叉验证: 名义-盈亏平衡
        d10, bie = need("DGS10"), need("T10YIE")
        if d10 is not None and bie is not None:
            m = pd.merge(d10, bie, on="date", suffixes=("_n", "_i"))
            m["real"] = m["v_n"] - m["v_i"]
            L.append(f"- 交叉验证(名义-盈亏平衡): {m.iloc[-1]['real']:.2f}%（截至 {m.iloc[-1]['date'].date()}）\n")

    # ── 3. 信用利差 ──
    df = need("BAMLH0A0HYM2")
    if df is not None:
        last = df.iloc[-1]["v"]
        avg52 = df.tail(252)["v"].mean()
        y = ytd(df)
        status = "🔴 走阔" if last > 3.0 or last > avg52 + 0.35 else "🟢 无压力"
        L.append(f"## 3. 高收益债利差 HY OAS（>3.0% 或 >52周均值+35bp = 信用传导）— {status}\n")
        L.append(f"- 年初: {y.iloc[0]['v']:.2f}% | 最新: **{last:.2f}%** | 52 周均值: {avg52:.2f}%\n")

    # ── 4. VIX ──
    df = need("VIXCLS")
    if df is not None:
        y = ytd(df)
        status = "🔴 恐慌" if df.iloc[-1]["v"] > 25 else "🟢 平静"
        L.append(f"## 4. VIX（>25 恐慌）— {status}\n")
        L.append(f"- 年初: {y.iloc[0]['v']:.1f} | 年内高: {y['v'].max():.1f} | 最新: **{df.iloc[-1]['v']:.1f}**\n")

    # ── 5. 指数回撤 ──
    idx = [("NASDAQCOM", "纳斯达克"), ("SP500", "标普500")]
    rows = []
    for sid, name in idx:
        df = need(sid)
        if df is None:
            continue
        y = ytd(df)
        y0, peak, last = y.iloc[0]["v"], y["v"].max(), df.iloc[-1]["v"]
        dd = (last / peak - 1) * 100
        status = "🔴 破位" if dd < -10 else ("⏳ 回调" if dd < -5 else "🟢 高位")
        rows.append((name, y0, peak, last, (last / y0 - 1) * 100, dd, status))
    if rows:
        L.append("## 5. 指数位置（距年内峰值 >10% = 破位）\n")
        L.append("| 指数 | 年初 | 峰值 | 最新 | YTD | 距峰值 | 状态 |")
        L.append("|---|---|---|---|---|---|---|")
        for name, y0, pk, ls, ytd_, dd, st in rows:
            L.append(f"| {name} | {y0:.0f} | {pk:.0f} | {ls:.0f} | {ytd_:+.1f}% | {dd:+.1f}% | {st} |")
        L.append("")

    # ── 6. 油价 ──
    df = need("DCOILBRENTEU")
    if df is not None:
        y = ytd(df)
        pct = (df.iloc[-1]["v"] / y.iloc[0]["v"] - 1) * 100
        status = "🔴 冲击" if pct > 30 else "🟢 正常"
        L.append(f"## 6. Brent 油价（YTD >30% = 宏观逆风共犯）— {status}\n")
        L.append(f"- 年初 ${y.iloc[0]['v']:.1f} → 最新 **${df.iloc[-1]['v']:.1f}（{pct:+.1f}%）**\n")

    # ── 7. 联邦基金路径 ──
    df = need("DFF")
    if df is None:
        df = need("FEDFUNDS")
    if df is not None:
        y = ytd(df)
        chg90 = df.iloc[-1]["v"] - df.iloc[-90]["v"] if len(df) >= 90 else float("nan")
        L.append("## 7. 联邦基金利率路径（加息转向判定）\n")
        L.append(f"- 最新: **{df.iloc[-1]['v']:.2f}%** | 年初: {y.iloc[0]['v']:.2f}% | 近90天变动: {chg90:+.2f}pct")
        path = [f"{r['date'].strftime('%y/%m')}:{r['v']:.2f}" for _, r in df.tail(12).iterrows()]
        L.append(f"- 近 12 期轨迹: {' → '.join(path)}")
        L.append("- 判定: 路径反转（暂停→上行）= 🔴 加息转向; 持平 = ⏳; 下行 = 🟢 宽松中\n")

    # ── 8. CPI 与通胀锚 ──
    df = need("CPIAUCSL")
    if df is not None:
        df = df.copy()
        df["yoy"] = df["v"].pct_change(12) * 100
        tail = df.tail(6)
        L.append("## 8. CPI 同比（近 6 个月）\n")
        L.append("| 月份 | YoY |")
        L.append("|---|---|")
        for _, r in tail.iterrows():
            L.append(f"| {r['date'].strftime('%Y-%m')} | {r['yoy']:.2f}% |")
        L.append("")
    df = need("T5YIFR")
    if df is not None:
        y = ytd(df)
        last = df.iloc[-1]["v"]
        status = "🔴 失锚" if last > 2.5 else "🟢 锚定"
        L.append(f"## 9. 5y5y 远期通胀（>2.5% = 失锚，Fed 敢等的底气）— {status}\n")
        L.append(f"- 年初: {y.iloc[0]['v']:.2f}% | 最新: **{last:.2f}%**（截至 {df.iloc[-1]['date'].date()}）\n")

    L.append(f"> 面板生成: FRED 本地 CSV，数据截至各序列最新可用日期（约滞后 2-4 个交易日）。")
    L.append("> 阈值依据: ~/.claude/skills/market-view-verify/references/triggers.md")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="FRED 宏观触发器面板")
    ap.add_argument("--outdir", default=".", help="CSV 与面板输出目录")
    ap.add_argument("--series", default=",".join(SERIES), help="逗号分隔的 FRED 序列")
    ap.add_argument("--start", default="2024-06-01", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--panel-only", action="store_true", help="不下载, 仅用本地 CSV 重算面板")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    if not args.panel_only:
        ok, fail = [], []
        for sid in [s.strip() for s in args.series.split(",") if s.strip()]:
            try:
                df = fetch_one(sid, args.start, args.outdir)
                ok.append(f"{sid}({len(df)}行,至{df.iloc[-1]['date'].date()})")
            except Exception as e:
                fail.append(f"{sid}: {e}")
        print("下载成功:", ", ".join(ok))
        if fail:
            print("下载失败:", "; ".join(fail), file=sys.stderr)

    md = panel(args.outdir)
    out = os.path.join(args.outdir, "MACRO_PANEL.md")
    with open(out, "w") as f:
        f.write(md)
    print(md)
    print(f"\n[面板已保存: {out}]")


if __name__ == "__main__":
    main()
