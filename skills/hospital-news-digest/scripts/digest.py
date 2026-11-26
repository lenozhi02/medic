#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医疗资讯汇总技能 · 主脚本

三个数据入口：
  A. 医院官网新闻监测   bjbug /api/v1/search/by-date
  B. 医院公众号日报     bjbug /api/v1/hospital-wechat/results
  C. 两个腾讯文档门户页（仅作交叉核对，需由 Agent 用 MCP read_page_source 落地为文件）

子命令：
  run            取数 → （可选）交叉核对 → 输出 .md + .docx
  collect        仅取数，落 data_api.json
  parse-portal   把门户页 HTML 解析成结构化 JSON
  check          仅做交叉核对

用法示例：
  python3 digest.py run --start 20260810 --end 20260812 --keyword 疫苗,卒中
  python3 digest.py run --start 2026-08-10 --end 2026-08-12 \
      --portal-official portal_official.html --portal-wechat portal_wechat.html
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
from collections import Counter

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，请先 pip install requests")

BASE = os.environ.get("BJBug_BASE", "http://www.bjbug.cn:5000")
UA = "Mozilla/5.0 (compatible; hospital-news-digest/1.0)"
PAGE_SIZE = 500          # 接口单页上限实测 1000 可用，取 500 稳妥
TIMEOUT = 60
RETRIES = 3

PORTAL_NAMES = {
    "official": "医院官网新闻监测",
    "wechat": "医院公众号日报",
}
PORTAL_URLS = {
    "official": "https://docs.qq.com/page/DUVJqSUV0a2piQkhw",
    "wechat": "https://docs.qq.com/page/DUXJFWXRGUXdRYVNB",
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def norm_date(s: str, field: str = "日期") -> str:
    """支持 20260810 / 2026-08-10 / 2026/08/10 → 2026-08-10"""
    if s is None:
        raise ValueError(f"{field} 不能为空")
    t = str(s).strip().replace("/", "-")
    if re.fullmatch(r"\d{8}", t):
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    raise ValueError(f"{field} 格式无法识别：{s}（支持 20260810 或 2026-08-10）")


def get_json(path: str, params: dict):
    """带重试的 GET，返回 dict。失败抛异常，绝不返回空壳。"""
    url = f"{BASE}{path}"
    last = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT,
                             headers={"User-Agent": UA})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"接口返回错误：{data['error']}")
            return data
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"请求失败 {path} params={params}：{last}")


def split_keywords(raw):
    if not raw:
        return []
    parts = re.split(r"[,，;；\s]+", str(raw).strip())
    return [p for p in (x.strip() for x in parts) if p]


def dedup_by_url(rows):
    seen, out = set(), []
    for r in rows:
        key = (r.get("url") or "").strip() or f"__nourl_{r.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# --------------------------------------------------------------------------
# A. 官网新闻（bjbug /api/v1/search/by-date）
# --------------------------------------------------------------------------

def fetch_official(start: str, end: str, keywords=None, include_duplicates=0):
    common = dict(start_date=start, end_date=end, region="all",
                  channel="all",                  # 关键：空串会返回 0 条
                  include_duplicates=include_duplicates)
    keywords = keywords or [""]
    rows, stats = [], {}
    for kw in keywords:
        offset = 0
        total = None
        while True:
            params = dict(common, limit=PAGE_SIZE, offset=offset)
            if kw:
                params["keyword"] = kw
            data = get_json("/api/v1/search/by-date", params)
            if total is None:
                total = data.get("total") or 0
                stats = {
                    "total": total,
                    "institution_stats": data.get("institution_stats") or [],
                    "start_date": data.get("start_date"),
                    "end_date": data.get("end_date"),
                }
            batch = data.get("data") or []
            rows.extend(batch)
            offset += PAGE_SIZE
            if not batch or offset >= (data.get("total") or 0):
                break
    rows = dedup_by_url(rows)
    return rows, stats


# --------------------------------------------------------------------------
# B. 公众号日报（bjbug /api/v1/hospital-wechat/results）
# --------------------------------------------------------------------------

def fetch_wechat(start: str, end: str, keywords=None, dedupe=1):
    keywords = keywords or [""]
    rows, stats = [], {}
    for kw in keywords:
        offset = 0
        limit = 100
        while True:
            params = dict(start_date=start, end_date=end,
                          dedupe=dedupe, limit=limit, offset=offset)
            if kw:
                params["keyword"] = kw
            data = get_json("/api/v1/hospital-wechat/results", params)
            if not stats:
                stats = {
                    "total": data.get("total") or 0,
                    "institutions": data.get("institutions") or [],
                    "report_count": data.get("report_count"),
                    "reports": data.get("reports") or [],
                }
            batch = data.get("data") or []
            rows.extend(batch)
            offset += limit
            if not batch or offset >= (data.get("total") or 0):
                break
    rows = dedup_by_url(rows)
    return rows, stats


def fetch_wechat_batches(start: str, end: str):
    """批次状态：用来判断「今日无新增」还是「未完成检索」"""
    data = get_json("/api/v1/hospital-wechat/reports",
                    dict(limit=60, start_date=start, end_date=end))
    return data.get("data") or [], data.get("total") or 0


# --------------------------------------------------------------------------
# C. 门户页解析（供交叉核对）
# --------------------------------------------------------------------------

def _load_html(path: str) -> str:
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    # MCP read_page_source 返回 {"html": "...", "version": ...}
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "html" in obj:
                return obj["html"]
        except Exception:                            # noqa: BLE001
            pass
    return raw


CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cache")


def resolve_portal(arg: str, kind: str):
    """把 --portal-* 的取值解析成实际文件路径。

    arg 三种形态：
      "auto"（默认）  取 cache/ 下最新的 portal_<kind>_*.json，没有则返回 None
      "none" / ""     明确不用门户，返回 None
      其它            当作路径，文件不存在直接报错，不静默降级
    """
    arg = (arg or "auto").strip()
    if arg.lower() in ("none", "off", "no", "-"):
        return None
    if arg.lower() != "auto":
        if not os.path.exists(arg):
            raise SystemExit(f"[!] 门户快照不存在：{arg}\n"
                             f"    用 --portal-{kind} none 可跳过交叉核对")
        return arg
    if not os.path.isdir(CACHE_DIR):
        return None
    hits = sorted(glob.glob(os.path.join(CACHE_DIR, f"portal_{kind}_*.json")),
                  key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def snapshot_meta(path: str) -> dict:
    """快照自身的版本与落盘时间，用于在报告里标注「核对基于哪份快照」。"""
    if not path:
        return {}
    meta = {"path": os.path.basename(path)}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            if obj.get("version") is not None:
                meta["version"] = obj["version"]
            if obj.get("total_lines") is not None:
                meta["total_lines"] = obj["total_lines"]
    except Exception:                                # noqa: BLE001
        try:
            raw = open(path, "r", encoding="utf-8", errors="replace").read()
            m = re.search(r'"version"\s*:\s*(\d+)', raw[::-1])
            if m:
                meta["version"] = int(m.group(1)[::-1])
        except Exception:                            # noqa: BLE001
            pass
    m2 = re.search(r"(\d{8}-\d{4})", os.path.basename(path))
    if m2:
        g = m2.group(1)
        meta["fetched_at"] = (f"{g[:4]}-{g[4:6]}-{g[6:8]} {g[9:11]}:{g[11:]}")
    else:
        meta["fetched_at"] = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    return meta


def parse_portal_official(path: str):
    """官网门户内联 <script id="__NEWS_DATA__"> JSON"""
    html = _load_html(path)
    m = re.search(r'id="__NEWS_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("未找到 __NEWS_DATA__ 数据块，门户页面结构可能已变更")
    pack = json.loads(m.group(1))
    items = pack if isinstance(pack, list) else (pack.get("items") or [])
    return {
        "kind": "official",
        "generated_at": pack.get("generated_at") if isinstance(pack, dict) else "",
        "items": items,
    }


def parse_portal_wechat(path: str):
    """公众号日报门户内联 const DATA = {...}"""
    html = _load_html(path)
    i = html.find("const DATA")
    if i < 0:
        raise ValueError("未找到 const DATA 数据块，门户页面结构可能已变更")
    j = html.find("{", i)
    obj, _ = json.JSONDecoder().raw_decode(html[j:])
    days = obj.get("days") or []
    items = []
    for d in days:
        for it in (d.get("items") or []):
            it = dict(it)
            it.setdefault("report_date", d.get("date"))
            it["_batch_total"] = d.get("total")
            it["_logged_in"] = d.get("logged_in")
            items.append(it)
    return {"kind": "wechat", "days": days, "items": items}


# --------------------------------------------------------------------------
# 交叉核对
# --------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    t = re.sub(r"\s+", "", str(t or ""))
    t = re.sub(r"[…\.]+$", "", t)
    return t


def _title_pref(t: str, n=24) -> str:
    return _norm_title(t)[:n]


def crosscheck(official_rows, wechat_rows, po=None, pw=None, start=None, end=None,
               portal_official_rows=None, keywords=None):
    """返回结构化核对结果。po/pw 为 None 時标记「未执行」，绝不写「一致」。"""
    res = {"official": None, "wechat": None}

    if po is not None or portal_official_rows is not None:
        items = (portal_official_rows if portal_official_rows is not None
                 else (po.get("items") or []))
        # 门户是全量内联，优先按日期窗口过滤；无窗口时退回页面自带的「是否区间内」标记
        in_range = []
        for it in items:
            d = str(it.get("_date") or it.get("发布时间") or it.get("采集日期") or "")[:10]
            if start and end:
                if start <= d <= end:
                    in_range.append(it)
            elif it.get("是否区间内") is True:
                in_range.append(it)
        api_n = len(official_rows)
        portal_n = len(in_range)
        def _t(it):
            return it.get("title") if "title" in it else it.get("新闻标题")

        api_keys = {_title_pref(r.get("title")) for r in official_rows}
        portal_keys = {_title_pref(_t(it)) for it in in_range}
        only_api = [r.get("title") for r in official_rows
                    if _title_pref(r.get("title")) not in portal_keys]
        only_portal = [_t(it) for it in in_range
                       if _title_pref(_t(it)) not in api_keys]
        res["official"] = {
            "portal_generated_at": po.get("generated_at", ""),
            "snapshot": po.get("_snapshot") or {},
            "portal_total_all": len(items),
            "portal_in_range": portal_n,
            "api_total": api_n,
            "diff": portal_n - api_n,
            "only_api": only_api[:50],
            "only_portal": only_portal[:50],
            "only_api_n": len(only_api),
            "only_portal_n": len(only_portal),
            "status": ("两侧均无数据" if portal_n == 0 and api_n == 0 else
                       "一致" if (portal_n == api_n and not only_api and not only_portal)
                       else ("数量一致，条目有差异" if portal_n == api_n else "数量不一致")),
        }

    if pw is not None:
        kws = [k for k in (keywords or []) if k]
        items = []
        for it in (pw.get("items") or []):
            rd = str(it.get("report_date") or "")
            if start and end and not (start <= rd <= end):
                continue
            if kws:
                blob = (f"{it.get('title') or ''} {it.get('account') or ''} "
                        f"{it.get('institution') or ''} {it.get('desc') or ''}")
                if not any(k in blob for k in kws):
                    continue
            items.append(it)
        api_n = len(wechat_rows)
        portal_n = len(items)
        api_keys = {_title_pref(r.get("title")) for r in wechat_rows}
        portal_keys = {_title_pref(it.get("title")) for it in items}
        only_api = [r.get("title") for r in wechat_rows
                    if _title_pref(r.get("title")) not in portal_keys]
        only_portal = [it.get("title") for it in items
                       if _title_pref(it.get("title")) not in api_keys]
        res["wechat"] = {
            "portal_batches": len(pw.get("days") or []),
            "snapshot": pw.get("_snapshot") or {},
            "portal_total_all": sum(len(d.get("items") or []) for d in (pw.get("days") or [])),
            "portal_in_range": portal_n,
            "api_total": api_n,
            "diff": portal_n - api_n,
            "only_api": only_api[:50],
            "only_portal": only_portal[:50],
            "only_api_n": len(only_api),
            "only_portal_n": len(only_portal),
            "status": ("两侧均无数据" if portal_n == 0 and api_n == 0 else
                       "一致" if (portal_n == api_n and not only_api and not only_portal)
                       else ("数量一致，条目有差异" if portal_n == api_n else "数量不一致")),
        }
    return res


# --------------------------------------------------------------------------
# 数据整理
# --------------------------------------------------------------------------

CHANNEL_CN = {"baidu": "百度", "wechat": "公众号", "official": "官网", "all": "全部"}
SENTIMENT_CN = {"normal": "正常", "positive": "正面", "negative": "负面",
                "risk": "风险", "unknown": "未知"}

# 商业推广信号词：命中即标注，供人工复核（不自动删除，避免误伤真新闻）
AD_HINTS = ["贸易", "有限公司", "经销", "代理商", "招商", "批发", "加盟",
            "优惠", "促销", "特价", "价格", "购买", "订购", "热线", "包邮",
            "招生", "培训班", "报名"]


def _json_maybe(v):
    if isinstance(v, (dict, list)):
        return v
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:                                # noqa: BLE001
        return None


def prepare_official(rows):
    out = []
    for r in rows:
        r = dict(r)
        r["_date"] = str(r.get("publish_date") or r.get("created_at") or "")[:10]
        senti = _json_maybe(r.get("llm_sentiment_json")) or {}
        r["_tone"] = senti.get("tone") or ""
        r["_risk_flags"] = senti.get("risk_flags") or []
        r["_tags"] = _json_maybe(r.get("llm_tags")) or []
        ents = _json_maybe(r.get("llm_entities")) or {}
        r["_ents"] = ents
        blob = f"{r.get('title') or ''} {r.get('real_domain') or ''} {r.get('source') or ''}"
        r["_ad_hits"] = [w for w in AD_HINTS if w in blob]
        out.append(r)
    out.sort(key=lambda x: (x.get("_date") or "", x.get("id") or 0), reverse=True)
    return out


def normalize_portal_official(po, start=None, end=None, keep_nourl=False,
                              keywords=None):
    """把门户 __NEWS_DATA__ 条目转成与接口同构的行，供合并与渲染。

    keywords 非空时按「标题 / 栏目 / 医院」本地匹配（OR）。门户数据是内联全量，
    接口的 keyword 是服务端检索，两边匹配字段不同，命中数不会完全相等。
    """
    kws = [k for k in (keywords or []) if k]
    out = []
    for x in (po.get("items") or []):
        d = str(x.get("发布时间") or x.get("采集日期") or "")[:10]
        if start and end and not (start <= d <= end):
            continue
        if kws:
            blob = (f"{x.get('新闻标题') or ''} {x.get('所属栏目') or ''} "
                    f"{x.get('医院') or ''}")
            if not any(k in blob for k in kws):
                continue
        url = (x.get("新闻链接") or "").strip()
        if not url and not keep_nourl:
            continue
        blob = f"{x.get('新闻标题') or ''} {url}"
        out.append({
            "title": x.get("新闻标题") or "",
            "institution_name": x.get("医院") or "",
            "publish_date": x.get("发布时间") or "",
            "created_at": x.get("采集日期") or "",
            "tags_level1": x.get("所属栏目") or "",
            "tags_level2": "",
            "real_domain": re.sub(r"^https?://([^/]+).*$", r"\1", url),
            "source": x.get("来源通道") or "官网直采",
            "channel": "official",
            "url": url,
            "_date": d,
            "_tags": [],
            "_ents": {},
            "_ad_hits": [w for w in AD_HINTS if w in blob],
            "_src": "portal",
        })
    out.sort(key=lambda r: r["_date"], reverse=True)
    return out


def prepare_wechat(rows):
    out = []
    for r in rows:
        r = dict(r)
        ts = r.get("timestamp")
        if ts:
            try:
                r["_date"] = dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            except Exception:                        # noqa: BLE001
                r["_date"] = str(r.get("report_date") or "")
        else:
            r["_date"] = str(r.get("report_date") or "")
        out.append(r)
    out.sort(key=lambda x: (x.get("report_date") or "", x.get("timestamp") or 0), reverse=True)
    return out


def summarize(official, wechat, extra_official=None):
    def top(counter, n=20):
        return counter.most_common(n)

    inst = Counter(r.get("institution_name") or r.get("institution_code") or "未知"
                   for r in official)
    chan = Counter(CHANNEL_CN.get(r.get("channel"), r.get("channel") or "未知")
                   for r in official)
    tag1 = Counter(r.get("tags_level1") or "未分类" for r in official)
    dom = Counter(r.get("real_domain") or r.get("source") or "-" for r in official)
    w_inst = Counter(r.get("institution") or "未知" for r in wechat)
    w_acc = Counter(r.get("account") or "未知" for r in wechat)
    w_day = Counter(r.get("report_date") or "-" for r in wechat)
    def _valid(d):
        return bool(d) and "2024-01-01" <= d <= dt.date.today().isoformat()

    qc_rows = list(official) + list(extra_official or [])
    bad_dates = [
        {"title": r.get("title") or "",
         "publish_date": r.get("publish_date") or "",
         "created_at": (r.get("created_at") or "")[:10],
         "src": r.get("_src") or "api"}
        for r in qc_rows if not _valid(r.get("_date"))
    ]
    vd = [r["_date"] for r in official if _valid(r.get("_date"))]

    return {
        "official_n": len(official),
        "wechat_n": len(wechat),
        "official_inst": top(inst),
        "official_inst_n": len(inst),
        "official_chan": top(chan, 5),
        "official_tag1": top(tag1, 8),
        "official_domain": top(dom, 6),
        "wechat_inst": top(w_inst),
        "wechat_inst_n": len(w_inst),
        "wechat_acc": top(w_acc, 8),
        "wechat_day": sorted(w_day.items()),
        "official_date_range": (min(vd) if vd else "—", max(vd) if vd else "—"),
        "wechat_date_range": (min((r["_date"] for r in wechat if r.get("_date")), default="—"),
                              max((r["_date"] for r in wechat if r.get("_date")), default="—")),
        "bad_dates": bad_dates,
        "ad_flagged": [{"title": r.get("title") or "", "url": r.get("url") or "",
                        "hits": r.get("_ad_hits") or [], "src": r.get("_src") or "api"}
                       for r in qc_rows if r.get("_ad_hits")],
        "no_url_official": sum(1 for r in official if not r.get("url")),
        "no_url_wechat": sum(1 for r in wechat if not r.get("url")),
    }


# --------------------------------------------------------------------------
# Markdown 渲染
# --------------------------------------------------------------------------

def _md_escape(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def _clip(s, n):
    s = str(s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def render_md(ctx):
    L = []
    a = L.append
    meta = ctx["meta"]
    s = ctx["summary"]
    kw = "、".join(meta["keywords"]) if meta["keywords"] else "无（全量）"

    a(f"# 医疗资讯汇总报告｜{meta['start']} 至 {meta['end']}")
    a("")
    a("| 项目 | 内容 |")
    a("|---|---|")
    a(f"| 日期范围 | {meta['start']} ~ {meta['end']} |")
    a(f"| 关键词 | {kw} |")
    a(f"| 生成时间 | {meta['generated_at']} |")
    a(f"| 数据源 | bjbug 医疗舆情系统 API（官网新闻 / 公众号日报） |")
    a(f"| 门户核对 | {meta['portal_note']} |")
    total_line = (f"官网直采 {s['official_n']} + 检索补充 {s['official_api_only_n']} · "
                  f"公众号日报 {s['wechat_n']}" if s.get("has_portal")
                  else f"官网新闻 {s['official_n']} · 公众号日报 {s['wechat_n']}")
    a(f"| 本次收录 | {total_line}（单位：条） |")
    a("")
    a("> 每条均附原文链接，来源为接口落库记录，未做二次创作。无链接条目已剔除并计数（见文末口径说明）。")
    a("")

    # 一、总体概览
    a("## 一、总体概览")
    a("")
    a("### 1.1 两类数据量")
    a("")
    a("| 类别 | 条数 | 覆盖机构数 | 日期跨度 | 门户核对 |")
    a("|---|---|---|---|---|")
    co = ctx["check"].get("official")
    cw = ctx["check"].get("wechat")
    off_n = (f"{s['official_n']}（直采）+ {s['official_api_only_n']}（补充）"
             if s.get("has_portal") else s['official_n'])
    a(f"| 医院官网新闻监测 | {off_n} | {s['official_inst_n']} | "
      f"{s['official_date_range'][0]} ~ {s['official_date_range'][1]} | "
      f"{(co or {}).get('status', '未执行')} |")
    a(f"| 医院公众号日报 | {s['wechat_n']} | {s['wechat_inst_n']} | "
      f"{s['wechat_date_range'][0]} ~ {s['wechat_date_range'][1]} | "
      f"{(cw or {}).get('status', '未执行')} |")
    a("")
    if s["official_n"] == 0 and s["wechat_n"] == 0:
        a("> ⚠ 两个通道在本区间均为 0 条。请先确认接口是否正常（见第四节批次状态），"
          "不要把「未完成检索」写成「无新闻」。")
        a("")

    has_any = s["official_n"] or s["wechat_n"]
    if has_any:
        a("### 1.2 官网新闻 · 机构分布")
        a("")
        if s["official_inst"]:
            a("| 机构 | 条数 |")
            a("|---|---|")
            for k, v in s["official_inst"]:
                a(f"| {_md_escape(k)} | {v} |")
        else:
            a("_本区间无官网条目。_")
        a("")
        a("### 1.3 官网新闻 · 检索通道")
        a("")
        if s["official_chan"]:
            a("| 检索通道 | 条数 |")
            a("|---|---|")
            for k, v in s["official_chan"]:
                a(f"| {k} | {v} |")
            ch = s["official_chan"][0][0] if len(s["official_chan"]) == 1 else "多个"
            a("")
            a(f"> 本次条目全部来自「{ch}」检索通道。注意：接口 `channel` 记录的是**检索通道**，"
              "门户页标注的「来源通道：官网直采」是**落地方式**，两者口径不同，不能混用。")
        else:
            a("_无数据。_")
        a("")
        a("### 1.4 官网新闻 · 来源域名")
        a("")
        if s["official_domain"]:
            a("| 来源域名 | 条数 |")
            a("|---|---|")
            for k, v in s["official_domain"]:
                a(f"| {_md_escape(k)} | {v} |")
        else:
            a("_无数据。_")
        a("")
        a("### 1.5 官网新闻 · 一级标签分布")
        a("")
        if s["official_tag1"]:
            a("| 标签 | 条数 |")
            a("|---|---|")
            for k, v in s["official_tag1"]:
                a(f"| {_md_escape(k)} | {v} |")
        else:
            a("_无标签数据。_")
        a("")
        a("### 1.6 公众号日报 · 机构分布")
        a("")
        if s["wechat_inst"]:
            a("| 机构 | 条数 |")
            a("|---|---|")
            for k, v in s["wechat_inst"]:
                a(f"| {_md_escape(k)} | {v} |")
        else:
            a("_本区间无记录。_")
        a("")
        if s["wechat_acc"]:
            a("### 1.7 公众号日报 · 公众号来源 Top")
            a("")
            a("| 公众号 | 条数 |")
            a("|---|---|")
            for k, v in s["wechat_acc"]:
                a(f"| {_md_escape(k)} | {v} |")
            a("")

    # 二、官网新闻清单
    a("## 二、医院官网新闻监测 · 条目明细")
    a("")
    if s.get("has_portal"):
        a(f"本节分两块：**官网直采** {s['official_n']} 条（门户 news.json，直采原文），"
          f"**检索通道补充** {s['official_api_only_n']} 条（仅接口有，可能为二手转载）。")
    else:
        a(f"共 {s['official_n']} 条，全部来自接口检索通道（本次未提供门户快照，可能包含二手转载）。")
    a("")

    def _off_item(i, r):
        a(f"#### {i}. {_md_escape(r.get('title'))}")
        a("")
        a(f"- **机构**：{r.get('institution_name') or r.get('institution_code') or '—'}"
          f" ｜ **发布时间**：{r.get('publish_date') or '—'}"
          f" ｜ **采集日期**：{r.get('created_at') or '—'}")
        a(f"- **通道**：{CHANNEL_CN.get(r.get('channel'), r.get('channel') or '—')}"
          f" ｜ **来源**：{r.get('source') or r.get('real_domain') or '—'}"
          f" ｜ **检索词**：{r.get('query_keyword') or '—'}")
        tagline = " ｜ ".join(x for x in [
            f"栏目/标签：{r.get('tags_level1') or '未分类'}"
            + (f" / {r.get('tags_level2')}" if r.get('tags_level2') else ""),
            ("AI标签：" + "、".join(r["_tags"])) if r.get("_tags") else "",
            ("情感：" + SENTIMENT_CN.get(r.get("sentiment_risk"), r.get("sentiment_risk") or "")
             + (f"（{r['_tone']}）" if r.get("_tone") else "")) if r.get("sentiment_risk") else "",
            ("风险信号：" + "、".join(r["_risk_flags"])) if r.get("_risk_flags") else "",
            (f"重复：是，组 {r.get('duplicate_group_id')}") if r.get("duplicate_group_id") else "",
        ] if x)
        a(f"- {tagline}")
        if r.get("llm_summary"):
            a(f"- **AI 摘要**：{_clip(r.get('llm_summary'), 300)}")
        if r.get("abstract"):
            a(f"- **原文摘要**：{_clip(r.get('abstract'), 300)}")
        ents = r.get("_ents") or {}
        if ents:
            parts = []
            for k, label in [("hospitals", "医院"), ("departments", "科室"),
                             ("people", "人物"), ("technologies", "技术"),
                             ("diseases", "疾病"), ("drugs", "药物"), ("devices", "器械")]:
                if ents.get(k):
                    parts.append(f"{label}：" + "、".join(map(str, ents[k][:4])))
            if parts:
                a(f"- **实体**：{' ｜ '.join(parts)}")
        if r.get("_ad_hits"):
            a(f"- ⚠ **疑似推广**（命中：{'、'.join(r['_ad_hits'])}），建议人工复核后决定去留")
        url = r.get("url") or ""
        a(f"- **原文**：{('[' + _clip(r.get('title'), 40) + '](' + url + ')') if url else '⚠ 无链接，已标记'}")
        if url:
            a(f"  <{url}>")
        a("")

    if s.get("has_portal"):
        a(f"### 2.1 官网直采（门户 news.json）｜{s['official_n']} 条")
        a("")
        if ctx["official"]:
            for i_, r in enumerate(ctx["official"], 1):
                _off_item(i_, r)
        else:
            a("_本区间门户无条目。_")
            a("")
        a(f"### 2.2 检索通道补充（接口独有）｜{s['official_api_only_n']} 条")
        a("")
        if ctx["official_api_only"]:
            a("> ⚠ 以下条目仅存在于接口检索通道（百度），门户未收录，可能为二手转载或尚未同步。"
              "按项目铁律 2，采用前需追溯到白名单原文。")
            a("")
            for i_, r in enumerate(ctx["official_api_only"], 1):
                _off_item(i_, r)
        else:
            a("_无。接口条目已被门户完全覆盖。_")
            a("")
    else:
        a(f"### 2.1 检索通道（接口）｜{s['official_n']} 条")
        a("")
        if ctx["official"]:
            for i_, r in enumerate(ctx["official"], 1):
                _off_item(i_, r)
        else:
            a("_本区间无条目。_")
            a("")

    # 三、公众号日报
    a("## 三、医院公众号日报 · 条目明细")
    a("")
    a(f"共 {s['wechat_n']} 条，按批次日期倒序。")
    a("")
    for i, r in enumerate(ctx["wechat"], 1):
        a(f"#### {i}. {_md_escape(r.get('title'))}")
        a("")
        a(f"- **监测机构**：{r.get('institution') or '—'}"
          f" ｜ **公众号**：{r.get('account') or '—'}"
          f" ｜ **批次日期**：{r.get('report_date') or '—'}")
        a(f"- **发布时间**：{r.get('_date') or '—'}"
          f"（原文标注「{r.get('date_str') or '—'}」）"
          f" ｜ **入库时间**：{r.get('generated_at') or '—'}")
        if r.get("desc"):
            a(f"- **摘要**：{_clip(r.get('desc'), 300)}")
        url = r.get("url") or ""
        a(f"- **原文**：{('[打开原文](' + url + ')') if url else '⚠ 无链接，已标记'}")
        if url:
            a(f"  <{url}>")
        a("")

    # 四、交叉核对
    a("## 四、门户页交叉核对")
    a("")
    if not co and not cw:
        a("> **未执行**：本次未用门户页快照。若需核对，助手可读取门户源码刷新 cache/ "
          "（见 SKILL.md「门户快照」一节），再由脚本比对。")
        a("> 未经核对，不代表数据有误，但也不能声称「与门户一致」。")
        a("")
    else:
        a("| 类别 | 接口条数 | 门户条数（区间内） | 差值 | 仅接口有 | 仅门户有 | 结论 |")
        a("|---|---|---|---|---|---|---|")
        for key, name in [("official", "官网新闻"), ("wechat", "公众号日报")]:
            c = ctx["check"].get(key)
            if not c:
                a(f"| {name} | — | — | — | — | — | 未执行 |")
                continue
            a(f"| {name} | {c['api_total']} | {c['portal_in_range']} | "
              f"{c['diff']:+d} | {c['only_api_n']} | {c['only_portal_n']} | {c['status']} |")
        a("")
        if meta.get("portal_stale"):
            for x in meta["portal_stale"]:
                a(f"> ⚠ **快照可能已过期**：{x}。门户尚未覆盖的新条目会落在「检索通道补充」里，"
                  "这是时点差，不是数据错误。需要最新门户数据时请刷新快照。")
                a("")
        a("**怎么读这张表**")
        a("")
        a("- **官网侧**：门户是「官网直采」（news.json），接口是「检索通道（百度）」，两者**非同源**。"
          "标题大面积不重合属正常，本节核对的是两条通道的覆盖差异，不是数据对错。"
          "报告正文已按「直采 / 补充」分开列，未混算。")
        a(f"- **公众号侧**：两侧同源（同一批检索产物），差异主要来自批次时点与去重设置"
          f"（接口取数时间 {meta['generated_at']}）。")
        a("- **口径**：对外统计一律以接口/Supabase 为准；门户只作旁证。")
        a("")
        for key, name in [("official", "官网新闻"), ("wechat", "公众号日报")]:
            c = ctx["check"].get(key)
            if not c:
                continue
            if c.get("only_api") or c.get("only_portal"):
                a(f"**{name} · 差异明细（各列前 50 条）**")
                a("")
                if c.get("only_api"):
                    a("- 仅接口有：")
                    for t in c["only_api"]:
                        a(f"  - {_clip(t, 80)}")
                if c.get("only_portal"):
                    a("- 仅门户有：")
                    for t in c["only_portal"]:
                        a(f"  - {_clip(t, 80)}")
                a("")
            if key == "official":
                a(f"> 门户快照生成时间：{c.get('portal_generated_at') or '未提供'}；"
                  f"门户内联全量 {c.get('portal_total_all')} 条。")
                a("")
            if key == "wechat":
                a(f"> 门户快照含 {c.get('portal_batches')} 个批次，全量 {c.get('portal_total_all')} 条。")
                a("")

    # 五、批次状态与口径
    a("## 五、检索状态与口径说明")
    a("")
    b = ctx.get("batches") or []
    if b:
        a("### 5.1 公众号日报批次状态")
        a("")
        a("| 批次日期 | 生成时间 | 完成机构 | 有结果机构 | 文章数 | 登录态 | 异常 |")
        a("|---|---|---|---|---|---|---|")
        for x in b[:20]:
            li = x.get("logged_in")
            li_cn = "正常" if li is True else ("失效" if li is False else "未知")
            err = "；".join(x.get("errors") or []) or "—"
            a(f"| {x.get('report_date') or '—'} | {x.get('generated_at') or '—'} | "
              f"{x.get('completed_groups')}/{x.get('groups')} | {x.get('groups_with_result')} | "
              f"{x.get('total')} | {li_cn} | {_md_escape(_clip(err, 60))} |")
        a("")
        bad = [x for x in b if x.get("logged_in") is False]
        if bad:
            a(f"> ⚠ 有 {len(bad)} 个批次登录态失效（如 {bad[0].get('report_date')}），"
              "该批次结果为 0 属「未完成检索」，不是「无新闻」。")
            a("")
    a("### 5.2 口径")
    a("")
    a("- **时间窗口**：按接口 `start_date` / `end_date` 闭区间过滤；官网以 `publish_date` 为准，"
      "公众号以批次日期 `report_date` 为准。")
    a("- **关键词**：多个关键词分别请求后按 URL 去重合并（OR 逻辑）；接口为模糊匹配，"
      "可能命中标题以外的摘要/正文。")
    a("- **去重**：官网默认剔除重复组；公众号默认 `dedupe=1`。")
    a(f"- **无链接剔除**：官网 {ctx['dropped_no_url'][0]} 条、公众号 {ctx['dropped_no_url'][1]} 条"
      "（按项目铁律「拿不到链接不收」，正文清单已排除）。")
    a("- **通道参数**：官网接口 `channel` 必须传 `all`，传空串会返回 0 条——这是接口已知行为。")
    a("- **数据层 vs 展示层**：腾讯文档门户为展示层，统计一律以接口/Supabase 为准；"
      "门户仅用于交叉核对。")
    a("- **官网双通道**：门户 `news.json` 为「官网直采」，接口 `search/by-date` 为「检索通道（百度）」，"
      "两者**非同源**；本报告分列为「官网直采」与「检索通道补充」，未合并计数。")
    a("")
    a("### 5.3 数据质量提示")
    a("")
    bd = s.get("bad_dates") or []
    if bd:
        a(f"本次数据中有 {len(bd)} 条**发布时间缺失或明显异常**（早于 2024 或为空），"
          "已按采集日期排序展示，原文照录未改写：")
        a("")
        a("| 标题 | 来源 | publish_date | 采集日期 |")
        a("|---|---|---|---|")
        for x in bd:
            src = "官网直采" if x.get("src") == "portal" else "检索通道"
            a(f"| {_md_escape(_clip(x['title'], 50))} | {src} | "
              f"{x['publish_date'] or '（空）'} | {x['created_at'] or '—'} |")
    else:
        a("发布时间字段完整，未发现异常。")
    a("")
    ad = s.get("ad_flagged") or []
    if ad:
        a(f"另有 {len(ad)} 条命中商业推广信号词，已在正文条目标 ⚠，此处汇总供复核：")
        a("")
        a("| 标题 | 来源 | 命中词 |")
        a("|---|---|---|")
        for x in ad:
            src = "官网直采" if x.get("src") == "portal" else "检索通道"
            a(f"| {_md_escape(_clip(x['title'], 50))} | {src} | {'、'.join(x['hits'])} |")
        a("")
        a("> 标记只说明标题/域名出现商业词，不等于判定为广告；按项目铁律「宁剔勿留」，"
          "建议人工确认后剔除。")
        a("")
    a("---")
    a("")
    a(f"数据源：{BASE}/api/v1 ｜ 门户：{PORTAL_URLS['official']} ｜ {PORTAL_URLS['wechat']}")
    return "\n".join(L)


# --------------------------------------------------------------------------
# DOCX 渲染
# --------------------------------------------------------------------------

def render_docx(ctx, path):
    import docx
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.section import WD_SECTION
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    meta = ctx["meta"]
    s = ctx["summary"]
    doc = docx.Document()

    # 页面与默认字体
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.4)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.2)

    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    _rpr = normal.element.get_or_add_rPr()
    _rpr.get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    normal.paragraph_format.line_spacing = 1.4

    def set_cn_font(run, name="宋体", size=10.5, bold=False, color=None):
        run.font.name = name
        run.font.size = Pt(size)
        run.font.bold = bold
        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)

    def add_hyperlink(paragraph, url, text, color="1F5FBF", size=8.5, bold=False):
        part = paragraph.part
        r_id = part.relate_to(url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK,
                              is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), r_id)
        r = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        rf = OxmlElement("w:rFonts")
        rf.set(qn("w:ascii"), "宋体")
        rf.set(qn("w:hAnsi"), "宋体")
        rf.set(qn("w:eastAsia"), "宋体")
        rPr.append(rf)
        c = OxmlElement("w:color")
        c.set(qn("w:val"), color)
        rPr.append(c)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rPr.append(u)
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size * 2)))
        rPr.append(sz)
        if bold:
            rPr.append(OxmlElement("w:b"))
        r.append(rPr)
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        h.append(r)
        paragraph._p.append(h)

    def h1(text):
        p = doc.add_paragraph(style="Heading 1")
        p.paragraph_format.space_before = Pt(16)
        p.paragraph_format.space_after = Pt(8)
        set_cn_font(p.add_run(text), "微软雅黑", 15, True, "1A3A6B")
        # 下划边框
        pPr = p._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:color"), "1A3A6B")
        pbdr.append(bottom)
        pPr.append(pbdr)
        return p

    def h2(text):
        p = doc.add_paragraph(style="Heading 2")
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        set_cn_font(p.add_run(text), "微软雅黑", 11.5, True, "24466E")
        return p

    def para(text="", size=10.5, color=None, bold=False, indent=0, space_after=2, italic=False):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.space_before = Pt(0)
        if indent:
            p.paragraph_format.first_line_indent = Cm(indent)
        r = p.add_run(text)
        set_cn_font(r, "宋体", size, bold, color)
        r.font.italic = italic
        return p

    def kv(label, value, size=9.5):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.left_indent = Cm(0.4)
        set_cn_font(p.add_run(f"{label} "), "微软雅黑", size, True, "4A5568")
        set_cn_font(p.add_run(str(value)), "宋体", size, False, "2D3748")
        return p

    def table(headers, rows, widths=None):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        tblPr = t._tbl.tblPr
        tblW = OxmlElement("w:tblW")
        tblW.set(qn("w:w"), "5000")
        tblW.set(qn("w:type"), "pct")
        tblPr.append(tblW)
        hdr = t.rows[0].cells
        for i, hx in enumerate(headers):
            hdr[i].text = ""
            p = hdr[i].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_cn_font(p.add_run(hx), "微软雅黑", 9, True, "FFFFFF")
            shd = OxmlElement("w:shd")
            shd.set(qn("w:fill"), "1A3A6B")
            hdr[i]._tc.get_or_add_tcPr().append(shd)
        trPr = t.rows[0]._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:tblHeader"))
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                p = cells[i].paragraphs[0]
                p.paragraph_format.space_after = Pt(1)
                set_cn_font(p.add_run(str(v)), "宋体", 8.5, False, "2D3748")
        if widths:
            for r_ in t.rows:
                for i, w in enumerate(widths):
                    r_.cells[i].width = Cm(w)
        return t

    # ---------------- 封面区 ----------------
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(40)
    set_cn_font(p.add_run("医疗资讯汇总报告"), "微软雅黑", 24, True, "1A3A6B")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(24)
    set_cn_font(p.add_run(f"{meta['start']} — {meta['end']}"), "微软雅黑", 13, False, "4A5568")

    kw = "、".join(meta["keywords"]) if meta["keywords"] else "无（全量）"
    info = [
        ("日期范围", f"{meta['start']} 至 {meta['end']}"),
        ("关键词", kw),
        ("生成时间", meta["generated_at"]),
        ("收录条数", (f"官网直采 {s['official_n']} + 检索补充 {s['official_api_only_n']}"
                      f" ｜ 公众号日报 {s['wechat_n']}")
                     if s.get("has_portal")
                     else f"官网新闻 {s['official_n']} ｜ 公众号日报 {s['wechat_n']}"),
        ("数据源", "bjbug 医疗舆情系统 API（官网新闻 / 公众号日报）"),
        ("门户核对", meta["portal_note"]),
    ]
    t = table(["项目", "内容"], info, widths=[3.4, 12.8])
    doc.add_paragraph()

    # 目录域（Word/WPS 打开后自动或右键更新）
    p_toc = doc.add_paragraph()
    p_toc.paragraph_format.space_after = Pt(6)
    set_cn_font(p_toc.add_run("目  录"), "微软雅黑", 11, True, "1A3A6B")
    p_fld = doc.add_paragraph()
    r = p_fld.add_run()
    fld1 = OxmlElement("w:fldChar"); fld1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    fld2 = OxmlElement("w:fldChar"); fld2.set(qn("w:fldCharType"), "separate")
    tt = OxmlElement("w:t"); tt.text = "打开后按 F9 或右键「更新域」生成目录"
    fld3 = OxmlElement("w:fldChar"); fld3.set(qn("w:fldCharType"), "end")
    for el in (fld1, instr, fld2, tt, fld3):
        r._r.append(el)
    # 让 Word 打开时提示更新域
    try:
        settings = doc.settings.element
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        settings.append(upd)
    except Exception:                                # noqa: BLE001
        pass
    doc.add_page_break()

    # ---------------- 一、总体概览 ----------------
    h1("一、总体概览")
    h2("1.1 两类数据量")
    co = ctx["check"].get("official")
    cw = ctx["check"].get("wechat")
    off_n2 = (f"{s['official_n']}（直采）+{s['official_api_only_n']}（补充）"
              if s.get("has_portal") else s["official_n"])
    table(["类别", "条数", "覆盖机构", "日期跨度", "门户核对"], [
        ["医院官网新闻监测", off_n2, s["official_inst_n"],
         f"{s['official_date_range'][0]} ~ {s['official_date_range'][1]}",
         (co or {}).get("status", "未执行")],
        ["医院公众号日报", s["wechat_n"], s["wechat_inst_n"],
         f"{s['wechat_date_range'][0]} ~ {s['wechat_date_range'][1]}",
         (cw or {}).get("status", "未执行")],
    ], widths=[4.0, 1.8, 2.2, 4.8, 3.0])

    if s["official_n"] == 0 and s["wechat_n"] == 0:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        set_cn_font(p.add_run("注意：两个通道在本区间均为 0 条，请先看第五节批次状态，"
                              "区分「无新增」与「检索未完成」。"), "宋体", 9.5, True, "B00020")

    has_any = s["official_n"] or s["wechat_n"]
    if s["official_inst"]:
        h2("1.2 官网新闻 · 机构分布")
        table(["机构", "条数"], [[k, v] for k, v in s["official_inst"]], widths=[11.0, 4.0])

    if s["official_chan"]:
        h2("1.3 官网新闻 · 检索通道")
        table(["检索通道", "条数"], [[k, v] for k, v in s["official_chan"]],
              widths=[11.0, 4.0])
        ch = s["official_chan"][0][0] if len(s["official_chan"]) == 1 else "多个"
        para(f"本次条目全部来自「{ch}」检索通道。接口 channel 记录的是检索通道，"
             f"门户页的「来源通道：官网直采」是落地方式，两者口径不同。", 8, "718096")

    if s["official_domain"]:
        h2("1.4 官网新闻 · 来源域名")
        table(["来源域名", "条数"], [[k, v] for k, v in s["official_domain"]],
              widths=[11.0, 4.0])

    if s["official_tag1"]:
        h2("1.5 官网新闻 · 一级标签分布")
        table(["标签", "条数"], [[k, v] for k, v in s["official_tag1"]], widths=[11.0, 4.0])

    if s["wechat_inst"]:
        h2("1.6 公众号日报 · 机构分布")
        table(["监测机构", "条数"], [[k, v] for k, v in s["wechat_inst"]], widths=[11.0, 4.0])

    if s["wechat_acc"]:
        h2("1.7 公众号日报 · 公众号来源 Top")
        table(["公众号", "条数"], [[k, v] for k, v in s["wechat_acc"]], widths=[11.0, 4.0])

    # ---------------- 二、官网新闻明细 ----------------
    doc.add_page_break()
    h1("二、医院官网新闻监测 · 条目明细")
    if s.get("has_portal"):
        para(f"本节分两块：官网直采 {s['official_n']} 条（门户 news.json，直采原文），"
             f"检索通道补充 {s['official_api_only_n']} 条（仅接口有，可能为二手转载）。", 9.5, "4A5568")
    else:
        para(f"共 {s['official_n']} 条，全部来自接口检索通道（本次未提供门户快照，可能包含二手转载）。",
             9.5, "4A5568")

    def _off_item(i, r):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(9)
        p.paragraph_format.space_after = Pt(2)
        set_cn_font(p.add_run(f"{i}. "), "微软雅黑", 10.5, True, "1A3A6B")
        set_cn_font(p.add_run(str(r.get("title") or "")), "微软雅黑", 10.5, True, "1A202C")

        kv("机构", f"{r.get('institution_name') or r.get('institution_code') or '—'}"
                  f"  ｜  发布 {r.get('publish_date') or '—'}"
                  f"  ｜  采集 {r.get('created_at') or '—'}")
        kv("通道", f"{CHANNEL_CN.get(r.get('channel'), r.get('channel') or '—')}"
                  f"  ｜  来源 {r.get('source') or r.get('real_domain') or '—'}"
                  f"  ｜  检索词 {r.get('query_keyword') or '—'}")
        tags = (r.get("tags_level1") or "未分类")
        if r.get("tags_level2"):
            tags += f" / {r['tags_level2']}"
        if r.get("_tags"):
            tags += "  ｜  AI：" + "、".join(map(str, r["_tags"]))
        senti = SENTIMENT_CN.get(r.get("sentiment_risk"), r.get("sentiment_risk") or "—")
        if r.get("_tone"):
            senti += f"（{r['_tone']}）"
        extra = []
        if r.get("sentiment_risk"):
            extra.append(f"情感 {senti}")
        if r.get("_risk_flags"):
            extra.append(f"风险 {'、'.join(r['_risk_flags'])}")
        if r.get("duplicate_group_id"):
            extra.append("重复组 " + str(r.get("duplicate_group_id")))
        kv("栏目", "  ｜  ".join([tags] + extra))
        if r.get("llm_summary"):
            kv("AI 摘要", _clip(r["llm_summary"], 220))
        if r.get("abstract"):
            kv("原文摘要", _clip(r["abstract"], 220))
        ents = r.get("_ents") or {}
        if ents:
            parts = []
            for k, label in [("hospitals", "医院"), ("departments", "科室"), ("people", "人物"),
                             ("technologies", "技术"), ("diseases", "疾病"),
                             ("drugs", "药物"), ("devices", "器械")]:
                if ents.get(k):
                    parts.append(f"{label}：" + "、".join(map(str, ents[k][:4])))
            if parts:
                kv("实体", " ｜ ".join(parts))
        if r.get("_ad_hits"):
            para(f"⚠ 疑似推广（命中：{'、'.join(r['_ad_hits'])}），建议人工复核后决定去留",
                 8.5, "B00020", bold=True, space_after=1)
        url = r.get("url") or ""
        pp = doc.add_paragraph()
        pp.paragraph_format.space_after = Pt(1)
        pp.paragraph_format.left_indent = Cm(0.4)
        set_cn_font(pp.add_run("原文 "), "微软雅黑", 9.5, True, "4A5568")
        if url:
            add_hyperlink(pp, url, "点击打开原文 ↗", size=9)
            pp2 = doc.add_paragraph()
            pp2.paragraph_format.space_after = Pt(2)
            pp2.paragraph_format.left_indent = Cm(0.4)
            set_cn_font(pp2.add_run(url), "宋体", 7.5, False, "718096")
        else:
            set_cn_font(pp.add_run("⚠ 无链接（已标记，建议人工核）"), "宋体", 9, True, "B00020")

    if s.get("has_portal"):
        h2(f"2.1 官网直采（门户 news.json）｜{s['official_n']} 条")
        if ctx["official"]:
            for i_, r in enumerate(ctx["official"], 1):
                _off_item(i_, r)
        else:
            para("本区间门户无条目。", 9, "718096")
        h2(f"2.2 检索通道补充（接口独有）｜{s['official_api_only_n']} 条")
        if ctx["official_api_only"]:
            para("⚠ 以下条目仅存在于接口检索通道（百度），门户未收录，可能为二手转载或尚未同步。"
                 "按项目铁律 2，采用前需追溯到白名单原文。", 8.5, "B00020", bold=True)
            for i_, r in enumerate(ctx["official_api_only"], 1):
                _off_item(i_, r)
        else:
            para("无。接口条目已被门户完全覆盖。", 9, "718096")
    else:
        h2(f"2.1 检索通道（接口）｜{s['official_n']} 条")
        if ctx["official"]:
            for i_, r in enumerate(ctx["official"], 1):
                _off_item(i_, r)
        else:
            para("本区间无条目。", 9, "718096")

    # ---------------- 三、公众号日报明细 ----------------
    doc.add_page_break()
    h1("三、医院公众号日报 · 条目明细")
    para(f"共 {s['wechat_n']} 条，按批次日期倒序。", 9.5, "4A5568")
    for i, r in enumerate(ctx["wechat"], 1):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(9)
        p.paragraph_format.space_after = Pt(2)
        set_cn_font(p.add_run(f"{i}. "), "微软雅黑", 10.5, True, "7A3E00")
        set_cn_font(p.add_run(str(r.get("title") or "")), "微软雅黑", 10.5, True, "1A202C")

        kv("机构", f"{r.get('institution') or '—'}"
                  f"  ｜  公众号 {r.get('account') or '—'}"
                  f"  ｜  批次 {r.get('report_date') or '—'}")
        kv("时间", f"发布 {r.get('_date') or '—'}（原文标注「{r.get('date_str') or '—'}」）"
                  f"  ｜  入库 {r.get('generated_at') or '—'}")
        if r.get("desc"):
            kv("摘要", _clip(r["desc"], 260))
        url = r.get("url") or ""
        pp = doc.add_paragraph()
        pp.paragraph_format.space_after = Pt(1)
        pp.paragraph_format.left_indent = Cm(0.4)
        set_cn_font(pp.add_run("原文 "), "微软雅黑", 9.5, True, "4A5568")
        if url:
            add_hyperlink(pp, url, "点击打开原文 ↗", size=9)
            pp2 = doc.add_paragraph()
            pp2.paragraph_format.space_after = Pt(2)
            pp2.paragraph_format.left_indent = Cm(0.4)
            set_cn_font(pp2.add_run(url), "宋体", 7.5, False, "718096")
        else:
            set_cn_font(pp.add_run("⚠ 无链接（已标记，建议人工核）"), "宋体", 9, True, "B00020")

    # ---------------- 四、交叉核对 ----------------
    doc.add_page_break()
    h1("四、门户页交叉核对")
    if not co and not cw:
        para("未执行：本次未提供门户页快照。核对需先用 MCP read_page_source 读取两个门户页并"
             "落地为文件，再由脚本比对。", 9.5, "B00020", bold=True)
        para("提示：未经核对不等于数据有误，但也不能声称「与门户一致」。", 9.5, "4A5568")
    else:
        if meta.get("portal_stale"):
            for x in meta["portal_stale"]:
                para(f"⚠ 快照可能已过期：{x}。门户尚未覆盖的新条目会落在「检索通道补充」里，"
                     f"属时点差；需要最新门户数据时请刷新快照。", 9, "B45309", bold=True)
        rows = []
        for key, name in [("official", "官网新闻"), ("wechat", "公众号日报")]:
            c = ctx["check"].get(key)
            if not c:
                rows.append([name, "—", "—", "—", "—", "—", "未执行"])
                continue
            rows.append([name, c["api_total"], c["portal_in_range"], f"{c['diff']:+d}",
                         c["only_api_n"], c["only_portal_n"], c["status"]])
        table(["类别", "接口条数", "门户条数", "差值", "仅接口", "仅门户", "结论"], rows,
              widths=[3.2, 2.0, 2.0, 1.6, 1.6, 1.6, 4.0])
        para("怎么读这张表：官网侧门户为「官网直采」、接口为「检索通道（百度）」，两者非同源，"
             f"标题大面积为不重合属正常，本节核对的是通道覆盖差异；公众号侧同源，差异来自批次时点"
             f"（取数 {meta['generated_at']}）与去重。对外统计一律以接口为准。",
             8.5, "718096")
        doc.add_paragraph()
        for key, name in [("official", "官网新闻"), ("wechat", "公众号日报")]:
            c = ctx["check"].get(key)
            if not c:
                continue
            if c.get("only_api") or c.get("only_portal"):
                h2(f"{name} · 差异明细（各列最多 50 条）")
                if c.get("only_api"):
                    para("仅接口有：", 9, "4A5568", bold=True)
                    for t_ in c["only_api"]:
                        para("· " + _clip(t_, 90), 8.5, "2D3748", space_after=0)
                if c.get("only_portal"):
                    para("仅门户有：", 9, "4A5568", bold=True)
                    for t_ in c["only_portal"]:
                        para("· " + _clip(t_, 90), 8.5, "2D3748", space_after=0)
            if key == "official":
                para(f"门户快照生成时间：{c.get('portal_generated_at') or '未提供'}；"
                     f"门户内联全量 {c.get('portal_total_all')} 条。", 8.5, "718096")
            if key == "wechat":
                para(f"门户快照含 {c.get('portal_batches')} 个批次，"
                     f"全量 {c.get('portal_total_all')} 条。", 8.5, "718096")

    # ---------------- 五、口径 ----------------
    h1("五、检索状态与口径说明")
    b = ctx.get("batches") or []
    if b:
        h2("5.1 公众号日报批次状态")
        rows = []
        for x in b[:20]:
            li = x.get("logged_in")
            li_cn = "正常" if li is True else ("失效" if li is False else "未知")
            err = "；".join(x.get("errors") or []) or "—"
            rows.append([x.get("report_date") or "—", x.get("generated_at") or "—",
                         f"{x.get('completed_groups')}/{x.get('groups')}",
                         x.get("groups_with_result"), x.get("total"), li_cn, _clip(err, 40)])
        table(["批次日期", "生成时间", "完成机构", "有结果", "文章数", "登录态", "异常"], rows,
              widths=[2.4, 3.2, 2.0, 1.5, 1.5, 1.4, 4.0])
        bad = [x for x in b if x.get("logged_in") is False]
        if bad:
            para(f"注意：{len(bad)} 个批次登录态失效（如 {bad[0].get('report_date')}），"
                 f"该批次 0 条属「未完成检索」，不是「无新闻」。", 9.5, "B00020", bold=True)

    h2("5.2 口径")
    for line in [
        "时间窗口：接口 start_date / end_date 闭区间；官网以 publish_date 为准，公众号以批次日期 report_date 为准。",
        "关键词：多个关键词分别请求后按 URL 去重合并（OR 逻辑）；接口为模糊匹配，可能命中标题以外的摘要/正文。",
        "去重：官网默认剔除重复组；公众号默认 dedupe=1。",
        f"无链接剔除：官网 {ctx['dropped_no_url'][0]} 条、公众号 {ctx['dropped_no_url'][1]} 条（按「拿不到链接不收」，清单已排除）。",
        "通道参数：官网接口 channel 必须传 all，传空串会返回 0 条（已实测的接口行为）。",
        "数据层 vs 展示层：腾讯文档门户为展示层，统计以接口/Supabase 为准，门户仅用于交叉核对。",
        "官网双通道：门户 news.json 为「官网直采」，接口 search/by-date 为「检索通道（百度）」，两者非同源；本报告分列为「直采」与「检索通道补充」，未合并计数。",
    ]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.left_indent = Cm(0.4)
        set_cn_font(p.add_run("· "), "宋体", 9.5, True, "1A3A6B")
        set_cn_font(p.add_run(line), "宋体", 9.5, False, "2D3748")

    bd = s.get("bad_dates") or []
    h2("5.3 数据质量提示")
    if bd:
        para(f"本次数据中有 {len(bd)} 条发布时间缺失或明显异常（早于 2024 或为空），"
             f"已按采集日期排序展示，原文照录未改写：", 9, "B00020", bold=True)
        table(["标题", "来源", "publish_date", "采集日期"],
              [[_clip(x["title"], 40),
                "官网直采" if x.get("src") == "portal" else "检索通道",
                x["publish_date"] or "（空）", x["created_at"] or "—"]
               for x in bd], widths=[7.8, 2.2, 3.0, 3.0])
    else:
        para("发布时间字段完整，未发现异常。", 9, "4A5568")

    ad = s.get("ad_flagged") or []
    if ad:
        para(f"另有 {len(ad)} 条命中商业推广信号词，已在正文条目标 ⚠，此处汇总供复核：",
             9, "B00020", bold=True)
        table(["标题", "来源", "命中词"],
              [[_clip(x["title"], 42),
                "官网直采" if x.get("src") == "portal" else "检索通道",
                "、".join(x["hits"])] for x in ad],
              widths=[9.0, 2.4, 4.6])
        para("标记只说明标题/域名出现商业词，不等于判定为广告；按「宁剔勿留」，"
             "建议人工确认后剔除。", 8, "718096")

    # 页脚
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(footer.add_run(f"医疗资讯汇总 · {meta['start']} ~ {meta['end']} · "
                               f"生成于 {meta['generated_at']}"), "宋体", 8, False, "A0AEC0")

    doc.save(path)
    return path


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def build_context(args):
    start = norm_date(args.start, "起始日期")
    end = norm_date(args.end, "截止日期")
    if start > end:
        raise SystemExit(f"起始日期晚于截止日期：{start} > {end}")

    keywords = split_keywords(args.keyword)
    print(f"[1/4] 取数：{start} ~ {end}，关键词={keywords or '无（全量）'}", flush=True)

    off_rows, off_stats = fetch_official(start, end, keywords)
    wx_rows, wx_stats = fetch_wechat(start, end, keywords)
    try:
        batches, _ = fetch_wechat_batches(start, end)
    except Exception as e:                            # noqa: BLE001
        batches = []
        print(f"  ! 批次状态获取失败：{e}（不影响条目，但文档会标注）", flush=True)

    print(f"  官网 {len(off_rows)} 条 / 公众号 {len(wx_rows)} 条", flush=True)

    po = pw = None
    po_path = resolve_portal(args.portal_official, "official")
    pw_path = resolve_portal(args.portal_wechat, "wechat")
    if po_path:
        m = snapshot_meta(po_path)
        print(f"[2/4] 门户快照（官网）：{m.get('path')}  version={m.get('version')}  "
              f"读取于 {m.get('fetched_at')}", flush=True)
        po = parse_portal_official(po_path)
        po["_snapshot"] = m
    else:
        print("[2/4] 门户快照（官网）：未提供，跳过核对", flush=True)
    if pw_path:
        m = snapshot_meta(pw_path)
        print(f"[2/4] 门户快照（日报）：{m.get('path')}  version={m.get('version')}  "
              f"读取于 {m.get('fetched_at')}", flush=True)
        pw = parse_portal_wechat(pw_path)
        pw["_snapshot"] = m
    else:
        print("[2/4] 门户快照（日报）：未提供，跳过核对", flush=True)

    api_official = [r for r in prepare_official(off_rows) if r.get("url")]
    wechat = [r for r in prepare_wechat(wx_rows) if r.get("url")]
    dropped = (len(off_rows) - len(api_official), len(wx_rows) - len(wechat))

    # 官网两套数据源：门户=官网直采，接口=检索通道（百度）。它们并非同源，
    # 门户存在时以门户为主清单，接口独有的单列为「检索通道补充」。
    portal_dropped = 0
    if po is not None:
        portal_rows = normalize_portal_official(po, start, end, keywords=keywords)
        pkeys = {_title_pref(r.get("title")) for r in portal_rows}
        api_only = [r for r in api_official if _title_pref(r.get("title")) not in pkeys]
        for r in portal_rows:
            r["_src"] = "portal"
        official = portal_rows
        portal_dropped = max(0, len(normalize_portal_official(po, start, end, keep_nourl=True, keywords=keywords))
                             - len(portal_rows))
    else:
        official = api_official
        api_only = []
        for r in official:
            r["_src"] = "api"

    print("[3/4] 交叉核对", flush=True)
    check = crosscheck(api_official, wechat, po, pw, start, end,
                       portal_official_rows=(official if po is not None else None),
                       keywords=keywords)
    if po or pw:
        for k, v in check.items():
            if v:
                print(f"  {k}: api={v['api_total']} portal={v['portal_in_range']} "
                      f"diff={v['diff']:+d} -> {v['status']}", flush=True)

    if not po and not pw:
        portal_note = "未执行（未提供门户快照）"
    elif po and pw:
        portal_note = "已核对两类"
    else:
        portal_note = "已核对一类"
    stale = []
    if po_path:
        m = snapshot_meta(po_path)
        portal_note += (f"｜官网快照 v{m.get('version', '?')}"
                        f"（{m.get('fetched_at', '?')}）")
        if (m.get("fetched_at") or "")[:10] < end:
            stale.append(f"官网快照读取于 {m['fetched_at']}，早于查询截止日 {end}")
    if pw_path:
        m = snapshot_meta(pw_path)
        portal_note += (f"｜日报快照 v{m.get('version', '?')}"
                        f"（{m.get('fetched_at', '?')}）")
        if (m.get("fetched_at") or "")[:10] < end:
            stale.append(f"日报快照读取于 {m['fetched_at']}，早于查询截止日 {end}")
    if stale:
        print("  ! 快照可能已过期：" + "；".join(stale), flush=True)
        print("    → 需要最新数据时，由助手重新读取门户源码刷新 cache/", flush=True)

    ctx = {
        "meta": {
            "start": start, "end": end, "keywords": keywords,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "portal_note": portal_note,
            "portal_stale": stale,
        },
        "official": official,
        "official_api_only": api_only,
        "wechat": wechat,
        "summary": dict(summarize(official, wechat, extra_official=api_only),
                        official_api_only_n=len(api_only),
                        has_portal=po is not None),
        "check": check,
        "batches": batches,
        "stats": {"official": off_stats, "wechat": wx_stats},
        "dropped_no_url": (dropped[0] + portal_dropped, dropped[1]),
        "portal_dropped": portal_dropped,
    }
    return ctx


def main():
    ap = argparse.ArgumentParser(description="医疗资讯汇总（官网新闻 + 公众号日报）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--start", required=True, help="起始日期：20260810 或 2026-08-10")
        p.add_argument("--end", required=True, help="截止日期：20260812 或 2026-08-12")
        p.add_argument("--keyword", default="", help="关键词，多个用逗号分隔（OR）")
        p.add_argument("--portal-official", default="auto",
                       help="官网门户快照：auto（默认，找 cache/ 最新）/ none / 路径")
        p.add_argument("--portal-wechat", default="auto",
                       help="公众号日报门户快照：auto / none / 路径")

    p_run = sub.add_parser("run", help="取数并生成 md + docx")
    common(p_run)
    p_run.add_argument("--outdir", default=os.path.join(os.getcwd(), "output"))
    p_run.add_argument("--name", default="", help="输出文件名前缀（默认自动）")

    p_col = sub.add_parser("collect", help="仅取数")
    common(p_col)
    p_col.add_argument("--out", default="data_api.json")

    p_pp = sub.add_parser("parse-portal", help="解析门户页快照")
    p_pp.add_argument("--html", required=True)
    p_pp.add_argument("--type", required=True, choices=["official", "wechat"])
    p_pp.add_argument("--out", default="portal.json")

    args = ap.parse_args()

    if args.cmd == "parse-portal":
        data = (parse_portal_official if args.type == "official"
                else parse_portal_wechat)(args.html)
        json.dump(data, open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        n = len(data.get("items") or [])
        print(f"OK {args.type}: {n} 条 -> {args.out}")
        return

    if args.cmd == "collect":
        start, end = norm_date(args.start), norm_date(args.end)
        kw = split_keywords(args.keyword)
        o, _ = fetch_official(start, end, kw)
        w, _ = fetch_wechat(start, end, kw)
        json.dump({"start": start, "end": end, "keywords": kw,
                   "official": o, "wechat": w},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"OK 官网 {len(o)} 条 / 公众号 {len(w)} 条 -> {args.out}")
        return

    # run
    ctx = build_context(args)
    os.makedirs(args.outdir, exist_ok=True)
    tag = f"_{'_'.join(ctx['meta']['keywords'])}" if ctx["meta"]["keywords"] else ""
    tag = re.sub(r"[\\/:*?\"<>|\s]+", "-", tag)[:40]
    prefix = args.name or f"医疗资讯汇总_{ctx['meta']['start']}_{ctx['meta']['end']}{tag}"
    md_path = os.path.join(args.outdir, prefix + ".md")
    docx_path = os.path.join(args.outdir, prefix + ".docx")

    print("[4/4] 生成文档", flush=True)
    md = render_md(ctx)
    open(md_path, "w", encoding="utf-8").write(md)
    render_docx(ctx, docx_path)

    meta_path = os.path.join(args.outdir, prefix + ".json")
    json.dump({"meta": ctx["meta"], "summary": ctx["summary"], "check": ctx["check"],
               "batches": ctx["batches"],
               "dropped_no_url": ctx["dropped_no_url"]},
              open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(json.dumps({
        "ok": True,
        "official": len(ctx["official"]),
        "wechat": len(ctx["wechat"]),
        "dropped_no_url_official": ctx["dropped_no_url"][0],
        "dropped_no_url_wechat": ctx["dropped_no_url"][1],
        "check": {k: (v or {}).get("status") for k, v in ctx["check"].items()},
        "md": md_path,
        "docx": docx_path,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
