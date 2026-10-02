#!/usr/bin/env python3
"""Frosty Friday Live Challenge の放送回情報を集めて Zenn 記事を生成する。

  collect --from 0 --to 9   YouTube / スプレッドシート / Drive スライドを取得し、
                            work/volNN.json（raw + 自動抽出結果 + warnings）を作る
  render  --from 0 --to 9   work/volNN.json の "parsed" から Zenn 記事 Markdown を生成する

work/volNN.json の "parsed" は人（Claude）が直してよい。collect は既存の
"parsed" を上書きしない（--force で上書き）。
"""
import argparse
import csv
import html
import io
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PLAYLIST = "https://www.youtube.com/playlist?list=PLVj4iIZgzTAq2FzaBBgqFOtZaJTcoG3JR"
SHEET_CSV = "https://docs.google.com/spreadsheets/d/1mGTjjTJFZNHCuyIvW64MhErJ0wOW4RDfzx_Go-B5w7M/export?format=csv"
DRIVE_FOLDER = "1HNPR1w59fxWToWO5hH3g_QhxkNPCiR5d"
# このスクリプトは <repo>/.claude/skills/frosty-zenn/scripts/ にある。記事はリポジトリ直下に出力する
DEFAULT_OUT = Path(__file__).resolve().parents[4]
NAMES_FILE = Path(__file__).resolve().parent.parent / "names.json"

# 運営メンバー: 表示名 -> (別名, 運営になった Vol)
HOSTS = {
    "Gaku": (["gaku", "がく", "田代"], 0),
    "tomo": (["tomo", "若松"], 0),
    "あれ": (["あれ", "allllllllez"], 4),
}


# ---------------------------------------------------------------- fetch utils
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def cached(path, fn, refresh=False):
    if path.exists() and not refresh:
        return path.read_text()
    text = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def yt_json(args):
    out = subprocess.run(["yt-dlp", "-J", "--skip-download", *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


def drive_list(folder_id):
    s = http_get(f"https://drive.google.com/embeddedfolderview?id={folder_id}")
    items = []
    for m in re.finditer(r'<a href="([^"]+)"[^>]*>.*?flip-entry-title">([^<]+)<', s, re.S):
        items.append((html.unescape(m.group(2)).strip(), html.unescape(m.group(1))))
    return items


def vol_of(title):
    m = re.search(r"Vol\.?\s*(\d+)", title, re.I)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- parsing
def load_names():
    return json.loads(NAMES_FILE.read_text()) if NAMES_FILE.exists() else {}


def clean_person(name):
    name = re.sub(r"[（(]\s*(確定|了承済み?|仮)\s*[）)]", "", name)
    name = re.sub(r"\((.*?)\)", r"（\1）", name).strip(" 　")
    return load_names().get(name, name)


def link_alive(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=20)
        return True
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except Exception:
        return True  # ネットワーク不調は判定しない


def host_of(name):
    low = name.lower()
    for disp, (aliases, _) in HOSTS.items():
        if any(a.lower() in low for a in aliases):
            return disp
    return None


def is_guest(name, vol):
    h = host_of(name)
    return h is None or vol < HOSTS[h][1]


def parse_sheet(text):
    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        vol = vol_of(r.get("YouTube回", "") or "")
        if vol is None:
            continue
        week = level = topic = None
        m = re.match(r"\s*\w+\s+(\d+)\s*[–-]\s*(\w+)", r.get("Week", ""))  # "Week 4 – Hard" / "Semaine 64 – Hard"
        if m:
            week, level, topic = int(m.group(1)), m.group(2), (r.get("Topic") or "").strip()
        else:
            m = re.match(r"\s*Week\s+(\d+)\s*-\s*(\w+)\s+(.*)", r.get("新サイトTopics名", ""))
            if m:
                week, level, topic = int(m.group(1)), m.group(2).capitalize(), m.group(3).strip()
        if week is None:
            continue
        rows.setdefault(vol, []).append({
            "week": week, "level": level, "topic": topic,
            "presenter": clean_person(r.get("担当（敬称略）", "") or ""),
        })
    for v in rows.values():
        v.sort(key=lambda x: x["week"])
    return rows


CHAPTER = re.compile(r"^\s*\d{1,2}(?::\d{2}){1,2}\s+(.*)$")


def norm_item(s):
    s = s.strip(" ・\u3000")
    return re.sub(r"\s*[—–-]\s*(Preview|プレビュー|GA|一般提供|PuPr)$", r" — \1", s)


def split_items(s):
    """「a / b」「a,b」を分割（「、」は文中にも出るので分割しない）（「PostgreSQL/MySQL」のような空白なし / は分割しない）"""
    parts = re.split(r"\s+/\s+|\s*／\s*|,(?=\S)(?![^()（）]*[)）])", s)
    return [norm_item(p) for p in parts if norm_item(p)]


def parse_description(desc):
    lines = []
    for ln in desc.splitlines():
        lines += re.split(r"\s+(?=\d{1,2}:\d{2}(?::\d{2})?\s)", ln)
    whatsnew, topics = [], []
    # 1) チャプター形式: "03:22 What's New : xxx" / "10:25 Topics: xxx"
    for ln in lines:
        m = CHAPTER.match(ln)
        if not m:
            continue
        body = m.group(1)
        mm = re.match(r"What[’']?s\s*New\s*[:：]?\s*(.*)", body, re.I)
        if mm and mm.group(1):
            whatsnew += split_items(mm.group(1))
            continue
        mm = re.match(r"Topics?\s*[:：]?\s*(.*)", body, re.I)
        if mm and mm.group(1):
            topics.append(mm.group(1).strip())
    # 2) ブロック形式: "What's New" 見出しの下に "・xxx"
    if not whatsnew or not topics:
        cur = None
        for ln in lines:
            s = ln.strip()
            m = CHAPTER.match(ln)
            if m and re.fullmatch(r"(What[’']?s\s*New.*|Topics?)", m.group(1).strip(), re.I):
                s = m.group(1).strip()  # "3:36  What's New" のような見出しチャプター
            if re.fullmatch(r"(What[’']?s\s*New)(\s*&\s*Topics)?", s, re.I):
                cur = "wn"
                continue
            if re.fullmatch(r"Topics?", s, re.I):
                cur = "tp"
                continue
            if cur and s.startswith("・"):
                (whatsnew if cur == "wn" else topics).append(norm_item(s))
            elif cur and s and not CHAPTER.match(ln):
                cur = None
            elif cur and CHAPTER.match(ln):
                cur = None
    # 本文のメンバー紹介より前だけを GitHub 対象にする
    body_end = len(lines)
    for i, ln in enumerate(lines):
        if re.match(r"^\s*[—-]{3,}", ln) or "Frosty Fridayという取り組み" in ln:
            if i > 0 and any("http" in l for l in lines[:i]):
                body_end = i
                break
    links, label = [], None
    for ln in lines[:body_end]:
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"(Git(Hub)?|Github)\s*[:：]?", s, re.I):
            label = None
            continue
        if s.startswith("http"):
            links.append({"label": label, "url": s.split()[0]})
        elif not CHAPTER.match(ln) and not s.startswith("・") and len(s) < 40:
            label = s
    return whatsnew, topics, links


def week_in_url(url):
    m = re.search(r"week[-_ ]?0*(\d+)", url, re.I)
    return int(m.group(1)) if m else None


def assign_links(weeks, links, warnings):
    by_week = {w["week"]: w for w in weeks}
    rest = []
    for ln in links:
        url = ln["url"]
        if re.search(r"github\.com/?(x{3,}/?)?$", url, re.I):
            warnings.append(f"プレースホルダURL: {url}（{ln['label']}）")
            continue
        w = week_in_url(url)
        if w in by_week:
            by_week[w]["links"].append(url)
        else:
            rest.append(ln)
    for ln in rest:
        target = None
        lab = (ln["label"] or "").lower()
        # 同じラベルの直前リンク（例: 説明スライド、同一人物の2本目）
        for w in weeks:
            p = w["presenter"].lower()
            if lab and (lab in p or p in lab or (host_of(lab) and host_of(lab) == host_of(p))):
                target = w
                break
        if target is None:
            empty = [w for w in weeks if not w["links"]]
            if len(empty) == 1:
                target = empty[0]
        if target:
            target["links"].append(ln["url"])
            warnings.append(f"Week 推定で割当: {ln['url']} → Week{target['week']}（label={ln['label']}）")
        else:
            warnings.append(f"割当できないリンク: {ln['url']}（label={ln['label']}）")
    for w in weeks:
        if not w["links"]:
            warnings.append(f"Week{w['week']} の GitHub URL が見つからない")
        for i, u in enumerate(w["links"]):
            if link_alive(u):
                continue
            # リポジトリ側でフォルダ名がゼロ埋め（week2_ → week002_）に変わったケース
            fixed = re.sub(r"/week(\d{1,2})_", lambda m: f"/week{int(m.group(1)):03d}_", u, count=1)
            if fixed != u and link_alive(fixed):
                w["links"][i] = fixed
                warnings.append(f"リンク修正: {u} → {fixed}")
            else:
                warnings.append(f"リンク切れ(404): {u}")
        if "、" in w["presenter"]:
            warnings.append(f"Week{w['week']} の担当が複数名（{w['presenter']}）→ GitHub ラベルで振り分ける")


def title_week_topics(desc):
    """概要欄「今回は」直下の Week 行（シートとの食い違い確認用）"""
    return [s.strip() for s in desc.splitlines() if re.match(r"\s*Week\s*\d+", s)][:4]


# ---------------------------------------------------------------- commands
def collect(a):
    out = Path(a.out)
    raw = out / ".work/raw"
    work = out / ".work"
    pl = json.loads(cached(raw / "playlist.json", lambda: yt_json(["--flat-playlist", PLAYLIST]), a.refresh))
    videos = {}
    for e in pl["entries"]:
        v = vol_of(e["title"])
        if v is not None and v not in videos:
            videos[v] = e
    sheet = parse_sheet(cached(raw / "sheet.csv", lambda: http_get(SHEET_CSV), a.refresh))
    drive = json.loads(cached(raw / "drive.json", lambda: json.dumps(drive_list(DRIVE_FOLDER), ensure_ascii=False), a.refresh))
    vol_folders = {vol_of(t): u for t, u in drive if vol_of(t) is not None and "/folders/" in u}

    for vol in range(a.vol_from, a.vol_to + 1):
        dst = work / f"vol{vol:02d}.json"
        if vol not in videos:
            print(f"Vol.{vol}: 再生リストに見つからない", file=sys.stderr)
            continue
        e = videos[vol]
        info = json.loads(cached(raw / f"yt_{e['id']}.json",
                                 lambda: yt_json([f"https://www.youtube.com/watch?v={e['id']}"]), a.refresh))
        desc = info.get("description", "")
        slides = ""
        if vol in vol_folders:
            fid = vol_folders[vol].rstrip("/").split("/")[-1]

            def fetch_slides():
                txt = []
                for t, u in drive_list(fid):
                    m = re.search(r"/presentation/d/([\w-]+)", u)
                    if m:
                        txt.append(f"# {t}\n" + http_get(f"https://docs.google.com/presentation/d/{m.group(1)}/export/txt"))
                return "\n".join(txt)
            slides = cached(raw / f"slides_vol{vol:02d}.txt", fetch_slides, a.refresh)

        warnings = []
        whatsnew, topics, links = parse_description(desc)
        weeks = [dict(w, links=[]) for w in sheet.get(vol, [])]
        if not weeks:
            warnings.append("スプレッドシートに Week 行がない")
        assign_links(weeks, links, warnings)
        if not whatsnew:
            warnings.append("What's New が抽出できない")
        if not topics:
            warnings.append("Topics が概要欄にない" + ("（スライドから補完を検討）" if slides else ""))
        d = info["upload_date"]
        parsed = {
            "vol": vol,
            "url": f"https://www.youtube.com/watch?v={e['id']}",
            "date": f"{d[:4]}/{d[4:6]}/{d[6:]}",
            "guests": list(dict.fromkeys(w["presenter"] for w in weeks if is_guest(w["presenter"], vol))),
            "whatsnew": whatsnew,
            "topics": topics,
            "weeks": [{"week": w["week"], "level": w["level"], "topic": w["topic"],
                       "presenter": host_of(w["presenter"]) if not is_guest(w["presenter"], vol) else w["presenter"],
                       "links": w["links"]} for w in weeks],
        }
        desc_weeks = title_week_topics(desc)
        for w in weeks:
            hit = [d for d in desc_weeks if re.search(rf"Week\s*{w['week']}\b", d)]
            norm = lambda x: re.sub(r"[^a-z0-9]|s\b", "", x.lower())
            if hit and w["topic"] and norm(w["topic"]) not in norm(hit[0]):
                warnings.append(f"Week{w['week']} のテーマがシート「{w['topic']}」と概要欄「{hit[0]}」で異なる")
        record = {"raw": {"title": info["title"], "description_weeks": desc_weeks,
                          "description": desc, "slides": slides},
                  "warnings": warnings, "parsed": parsed}
        if dst.exists() and not a.force:
            old = json.loads(dst.read_text())
            record["parsed"] = old["parsed"]
            record["reviewed"] = old.get("reviewed", False)
        dst.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        flag = "" if record.get("reviewed") else " ".join(f"\n    ! {w}" for w in warnings)
        print(f"Vol.{vol}: {dst.name}{flag}")


def article_link(a):
    return f"[{a['title']}]({a['url']})（{a['author']} / {a['src']}）"


def render_week(w, links, featured):
    out = ["", f"### Week{w['week']} – {w['level']} {w['topic']}（{w['presenter']}）", ""]
    out += [f"- 番組での解説：{u}" for u in w["links"]]
    lk = links.get("weeks", {}).get(str(w["week"]), {})
    if lk.get("official"):
        out.append(f"- お題：{lk['official']}")
    if lk.get("articles"):
        out.append("- 解説記事")
        out += [f"  - {article_link(a)}" for a in lk["articles"]]
    repos = lk.get("repos", [])
    main = [r for r in repos if r["repo"] in featured]
    rest = [r for r in repos if r["repo"] not in featured]
    if main:
        out.append("- 回答リポジトリ：" + " / ".join(f"[{r['owner']}]({r['url']})" for r in main))
    if rest:
        out += ["", f":::details その他の回答リポジトリ（{len(rest)}件）",
                " / ".join(f"[{r['owner']}]({r['url']})" for r in rest), ":::"]
    return out


def render_vol(p, links, featured):
    out = [f"## Vol.{p['vol']}（{p['date']}）", "", p["url"], ""]
    challengers = list(dict.fromkeys(w["presenter"] for w in p["weeks"]))
    hosts = [h for h, (_, since) in HOSTS.items() if p["vol"] >= since]
    cast = list(dict.fromkeys(hosts + challengers))
    out.append(f"- 出演者：{'、'.join(cast)}、チャレンジャー：{'、'.join(challengers) or 'なし'}")
    for key, label in (("whatsnew", "What's New"), ("topics", "Topics")):
        if p.get(key):
            out.append(f"- {label}")
            out += [f"  - {x}" for x in p[key]]
    reports = links.get("vol_articles", {}).get(str(p["vol"]), [])
    if reports:
        out.append("- 番組レポート")
        out += [f"  - {article_link(a)}" for a in reports]
    for w in p["weeks"]:
        out += render_week(w, links, featured)
    return "\n".join(out)


def render(a):
    out = Path(a.out)
    vols = []
    for vol in range(a.vol_from, a.vol_to + 1):
        f = out / f".work/vol{vol:02d}.json"
        if f.exists():
            vols.append(json.loads(f.read_text())["parsed"])
    if not vols:
        sys.exit("対象の work/volNN.json がありません。先に collect を実行してください")
    lo, hi = vols[0]["vol"], vols[-1]["vol"]
    front = "\n".join([
        "---",
        f'title: "Frosty Friday Live Challenge まとめ Vol.{lo}〜Vol.{hi}"',
        'emoji: "❄️"',
        'type: "idea"',
        'topics: ["snowflake", "frostyfriday", "snowvillage"]',
        "published: false",
        "---",
        "",
        "Snowflake のスキルアップチャレンジ Frosty Friday を解説する YouTube 番組",
        "「Frosty Friday Live Challenge」の放送回をまとめました。",
        "",
        PLAYLIST,
        "",
    ])
    lf = out / ".work/links.json"
    links = json.loads(lf.read_text()) if lf.exists() else {}
    if not links:
        print("links.json が無いので お題・解説記事・回答リポジトリ は出力しません（fflinks.py を実行）", file=sys.stderr)
    conf = NAMES_FILE.parent / "links_config.json"
    featured = json.loads(conf.read_text())["featured_repos"] if conf.exists() else []
    body = "\n\n".join(render_vol(p, links, featured) for p in vols)
    dst = out / f"frosty-friday-live-vol{a.vol_from:02d}-{a.vol_to:02d}.md"
    dst.write_text(front + "\n" + body + "\n")
    print(dst)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "render"):
        s = sub.add_parser(name)
        s.add_argument("--from", dest="vol_from", type=int, required=True)
        s.add_argument("--to", dest="vol_to", type=int, required=True)
        s.add_argument("--out", default=str(DEFAULT_OUT))
        if name == "collect":
            s.add_argument("--refresh", action="store_true", help="キャッシュを無視して再取得")
            s.add_argument("--force", action="store_true", help="既存の parsed を上書き")
    a = ap.parse_args()
    {"collect": collect, "render": render}[a.cmd](a)


if __name__ == "__main__":
    main()
