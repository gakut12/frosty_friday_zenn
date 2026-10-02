#!/usr/bin/env python3
"""Week ごとの「お題」「解説記事」「回答リポジトリ」を集めて .work/links.json を作る。

  python3 fflinks.py [--refresh]

情報源:
  - お題: frostyfri.day の sitemap.xml
  - 回答リポジトリ: FEATURED_REPOS + GitHub 検索（"frosty friday" など）。
    リポジトリ内のパス（フォルダ / ファイル名）に含まれる Week 番号で対応づける
  - 解説記事: Zenn（churadata Publication / 検索 / 見つかった著者の全記事）、Qiita、dev.to
    タイトル中の Week 番号で対応づける。「Live Challenge Vol.N」「第N回参加レポート」は Vol 単位の記事
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[4] / ".work"  # <repo>/.work
CONF = Path(__file__).resolve().parent.parent / "links_config.json"


def http(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def cached(path, fn, refresh):
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    data = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    return data


# ------------------------------------------------------------------ お題
def official(refresh):
    def fetch():
        xml = http("https://www.frostyfri.day/sitemap.xml")
        res = {}
        for u in re.findall(r"<loc>([^<]+)</loc>", xml):
            m = re.search(r"/week-0*(\d+)(?!\d)", u)
            if m and "/en/" in u:
                res.setdefault(m.group(1), u)
        return res
    return cached(OUT / "raw/official_weeks.json", fetch, refresh)


# ------------------------------------------------------------------ リポジトリ
PATH_WEEK = re.compile(r"(?:^|[^a-z])(?:week|wk)[\s_\-.]*0*(\d{1,3})(?!\d)", re.I)


def repo_tree(full, refresh):
    f = OUT / f"raw/trees/{full.replace('/', '_')}.json"

    def fetch():
        info = gh(f"repos/{full}")
        if not info:
            return None
        t = gh(f"repos/{full}/git/trees/{info['default_branch']}?recursive=1") or {}
        return {"repo": full, "branch": info["default_branch"], "fork": info["fork"],
                "paths": [{"path": x["path"], "type": x["type"]} for x in t.get("tree", [])]}
    return cached(f, fetch, refresh)


def repo_weeks(t):
    """Week番号 -> そのWeekを表す最も浅いパスの URL"""
    types = {p["path"]: p["type"] for p in t["paths"]}
    best = {}
    for p in t["paths"]:
        segs = p["path"].split("/")
        for i, seg in enumerate(segs):
            m = PATH_WEEK.search(seg)
            if not m:
                continue
            w = int(m.group(1))
            if w > 0 and (w not in best or i < best[w][0]):
                best[w] = (i, "/".join(segs[:i + 1]))
            break
    base = f"https://github.com/{t['repo']}"
    res = {}
    for w, (_, path) in best.items():
        kind = "blob" if types.get(path) == "blob" else "tree"
        res[w] = f"{base}/{kind}/{t['branch']}/{urllib.parse.quote(path)}"
    # リポジトリ名自体が1Week（例: ugmuka/frostyfridays-week60）
    if not res:
        m = PATH_WEEK.search(t["repo"].split("/")[1]) or re.search(r"ff_0*(\d+)", t["repo"], re.I)
        if m:
            res[int(m.group(1))] = base
    return res


def repos(conf, refresh):
    def search():
        names = []
        for q in conf["github_queries"]:
            r = subprocess.run(["gh", "search", "repos", q, "--limit", "100", "--json", "fullName"],
                               capture_output=True, text=True)
            names += [x["fullName"] for x in json.loads(r.stdout or "[]")]
        return sorted(set(names))
    found = cached(OUT / "raw/gh_search_names.json", search, refresh)
    names = list(dict.fromkeys(conf["featured_repos"] + found))
    names = [n for n in names if n not in conf["exclude_repos"]]
    with ThreadPoolExecutor(8) as ex:
        trees = list(ex.map(lambda n: repo_tree(n, refresh), names))
    by_week = {}
    for t in trees:
        if not t or (t["fork"] and t["repo"] not in conf["featured_repos"]):
            continue
        for w, url in repo_weeks(t).items():
            by_week.setdefault(str(w), []).append({"owner": t["repo"].split("/")[0], "repo": t["repo"], "url": url})
    order = {n: i for i, n in enumerate(conf["featured_repos"])}
    for v in by_week.values():
        v.sort(key=lambda x: (order.get(x["repo"], 999), x["owner"].lower()))
    return by_week


# ------------------------------------------------------------------ 記事
def zenn_articles(conf, refresh):
    def fetch():
        arts, users = {}, set(conf["zenn_users"])

        def add(a):
            arts[a["path"]] = {"src": "Zenn", "title": a["title"], "url": "https://zenn.dev" + a["path"],
                               "author": a["user"]["username"], "date": a["published_at"][:10]}

        def pages(url_fmt):
            for p in range(1, 50):
                d = json.loads(http(url_fmt.format(p=p)))
                if not d.get("articles"):
                    break
                for a in d["articles"]:
                    yield a
        for pub in conf["zenn_publications"]:
            for a in pages(f"https://zenn.dev/api/articles?publication_name={pub}&order=latest&page={{p}}"):
                add(a)
        for q in conf["article_queries"]:
            qq = urllib.parse.quote(q)
            for a in pages(f"https://zenn.dev/api/search?q={qq}&order=latest&source=articles&page={{p}}"):
                if re.search(r"frosty", a["title"], re.I):
                    add(a)
                    users.add(a["user"]["username"])
        for u in sorted(users):  # 検索は件数上限があるので著者ごとに全件見る
            for a in pages(f"https://zenn.dev/api/articles?username={u}&order=latest&page={{p}}"):
                add(a)
        return list(arts.values())
    return cached(OUT / "raw/zenn_articles.json", fetch, refresh)


def other_articles(conf, refresh):
    def fetch():
        arts = {}
        for q in conf["article_queries"]:
            for it in json.loads(http("https://qiita.com/api/v2/items?per_page=100&query=" + urllib.parse.quote(q))):
                arts[it["url"]] = {"src": "Qiita", "title": it["title"], "url": it["url"],
                                   "author": it["user"]["id"], "date": it["created_at"][:10]}
        for tag in ("frostyfriday", "snowflake"):
            for it in json.loads(http(f"https://dev.to/api/articles?per_page=1000&tag={tag}")):
                arts[it["url"]] = {"src": "dev.to", "title": it["title"], "url": it["url"],
                                   "author": it["user"]["username"], "date": it["published_at"][:10]}
        return list(arts.values())
    return cached(OUT / "raw/other_articles.json", fetch, refresh)


TITLE_WEEK = re.compile(r"(?:week|FROSTY_FRIDAY\(|#)\s*[-#]?\s*0*(\d{1,3})(?!\d)", re.I)
TITLE_VOL = re.compile(r"(?:Live\s*Challenge|LC).*?(?:Vol\.?\s*(\d+)|第\s*(\d+)\s*回)", re.I)


def classify(arts, vol_dates, conf):
    by_week, by_vol, unplaced = {}, {}, []
    for a in arts:
        t = a["title"]
        if not re.search(r"frosty", t, re.I) or a["url"] in conf["exclude_articles"]:
            continue
        ov = conf.get("article_overrides", {}).get(a["url"])
        if ov:  # 手動指定 {"week": N} / {"vol": N}
            key, d = ("week", by_week) if "week" in ov else ("vol", by_vol)
            d.setdefault(str(ov[key]), []).append(a)
            continue
        m = TITLE_VOL.search(t)
        if m:
            by_vol.setdefault(str(int(m.group(1) or m.group(2))), []).append(a)
            continue
        weeks = sorted({int(x) for x in TITLE_WEEK.findall(t) if 0 < int(x) < 1000})
        if weeks:
            for w in weeks:
                by_week.setdefault(str(w), []).append(a)
        elif re.search(r"Live\s*Challenge|ライブチャレンジ", t, re.I) and re.search(r"出演|参加|レポ", t) and vol_dates:
            # Vol 表記なしの出演レポート: 公開日の直前14日以内の放送回に割り当てる
            d = date.fromisoformat(a["date"])
            near = [(v, (d - vd).days) for v, vd in vol_dates.items() if 0 <= (d - vd).days <= 14]
            if near:
                v = min(near, key=lambda x: x[1])[0]
                by_vol.setdefault(str(v), []).append(dict(a, guessed=True))
            else:
                unplaced.append(a)
        else:
            unplaced.append(a)
    for d in (by_week, by_vol):
        for v in d.values():
            v.sort(key=lambda x: x["date"])
    return by_week, by_vol, unplaced


def vol_dates():
    res = {}
    for f in OUT.glob("vol*.json"):
        p = json.loads(f.read_text())["parsed"]
        res[p["vol"]] = date.fromisoformat(p["date"].replace("/", "-"))
    pl = OUT / "raw/playlist.json"
    if pl.exists():  # まだ collect していない回も再生リストの公開日で補う
        for e in json.loads(pl.read_text()).get("entries", []):
            m = re.search(r"Vol\.?\s*(\d+)", e["title"], re.I)
            info = OUT / f"raw/yt_{e['id']}.json"
            if m and int(m.group(1)) not in res and info.exists():
                d = json.loads(info.read_text())["upload_date"]
                res[int(m.group(1))] = date(int(d[:4]), int(d[4:6]), int(d[6:]))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    conf = json.loads(CONF.read_text())
    off = official(a.refresh)
    rp = repos(conf, a.refresh)
    arts = zenn_articles(conf, a.refresh) + other_articles(conf, a.refresh)
    aw, av, unplaced = classify(arts, vol_dates(), conf)
    weeks = sorted({int(w) for w in list(off) + list(rp) + list(aw)})
    data = {"weeks": {str(w): {"official": off.get(str(w)), "articles": aw.get(str(w), []),
                               "repos": rp.get(str(w), [])} for w in weeks},
            "vol_articles": av, "unplaced_articles": unplaced}
    (OUT / "links.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"links.json: weeks={len(weeks)} repos={len({r['repo'] for v in rp.values() for r in v})} "
          f"week_articles={sum(len(v) for v in aw.values())} vol_articles={sum(len(v) for v in av.values())}")
    guessed = [(v, x["title"]) for v, xs in av.items() for x in xs if x.get("guessed")]
    for v, t in guessed:
        print(f"  ! Vol.{v} に日付で推定割当: {t}")
    for x in unplaced:
        print(f"  - 未分類（Week/Vol 不明）: {x['title']}  {x['url']}")


if __name__ == "__main__":
    main()
