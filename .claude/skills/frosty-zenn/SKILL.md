---
name: frosty-zenn
description: Frosty Friday Live Challenge（YouTube 再生リスト）の放送回から、URL・公開日・出演者・チャレンジャー・What's New・Topics・解説 Week・GitHub URL を抽出し、Vol を10回ずつ区切った Zenn 記事 Markdown を生成・更新する。「Frosty Friday のまとめ記事」「Vol.30〜39 を作って」などで使う。
---

# Frosty Friday Live Challenge → Zenn まとめ記事

## 出力先

- 記事: リポジトリ `frosty_friday_zenn`（`~/git/frosty_friday_zenn`）直下の `frosty-friday-live-volAA-BB.md`
- 作業データ: `<repo>/.work/`（`raw/` は取得キャッシュ、`volNN.json` は回ごとの抽出結果）

## 情報源と優先順位

| 項目 | 情報源 |
|---|---|
| URL・公開日 | YouTube（`yt-dlp`）。公開日は upload_date（シートの「収録日」は使わない） |
| Week 番号・レベル・テーマ・担当者 | スプレッドシート「Frosty Friday Live Challenge List」 |
| What's New・Topics・GitHub URL | YouTube 概要欄（チャプター行 / 「今回は」「GitHub:」ブロック） |
| Topics（概要欄に無い初期回） | Drive のスライド（`.work/raw/slides_volNN.txt`）。スライドが無い回は抜き出さなくてよい |
| お題（公式） | frostyfri.day の sitemap.xml |
| 回答リポジトリ | `links_config.json` の `featured_repos`（常に表示）+ GitHub 検索で見つかったリポジトリ（`:::details` に折りたたみ）。パス中の Week 番号で対応づけ |
| 解説記事 | Zenn（churadata Publication・検索・見つかった著者の全記事）/ Qiita / dev.to。タイトルの Week 番号で対応づけ。「Live Challenge Vol.N」「第N回参加レポート」は Vol の「番組レポート」 |

## 手順

1. 収集（10回分ずつ。キャッシュがあれば再取得しない。最新化は `--refresh`）

   ```bash
   python3 ~/git/frosty_friday_zenn/.claude/skills/frosty-zenn/scripts/ffzenn.py collect --from 30 --to 39
   ```

   既に `reviewed: true` の回は parsed を保持する。作り直す場合のみ `--force`。

2. **レビュー**: 出力された `!` 警告ごとに `.work/volNN.json` の `parsed` を直し、`"reviewed": true` にする。
   `raw.description` / `raw.slides` を必ず見て判断すること。
   - **What's New / Topics**: 1項目1行に。` / ` 区切りや文中の「、」での切れ方を確認し、チャプター行がくっついた残骸は除く。
     見出し付きの補足（サイズ表など）は1項目に（ ）でまとめる。原文の言い回しは変えない（明らかな誤字のみ修正）。
     初期回で概要欄に Topics が無ければスライドの「Snowflake関連トピック」から取る。
   - **GitHub URL**: `Week 推定で割当` は label と担当者が合っているか確認。プレースホルダ（`github.com/XXXX`）は、
     本人の既知リポジトリ（例: あれ → `allllllllez/frosty_friday/sql/week-NN.sql`）に該当ファイルがあれば `gh api` で存在確認して補う。
     見つからなければリンク無しのままにし、最終報告に載せる。URL を推測で作らない。
   - **リンク切れ(404)**: `リンク修正`（ゼロ埋めフォルダ名）は自動対応済み。それ以外の 404 は最終報告に載せる。
   - **担当が複数名**（例「亀井、向井」）: 概要欄 GitHub の label と URL で Week ごとに振り分け、`guests` も直す。
   - **テーマ不一致**（シートと概要欄）: シートを正とするが、最終報告でユーザーに確認する。
3. リンク収集（collect の後に実行。Vol の公開日を使って出演レポートを割り当てるため）

   ```bash
   python3 ~/git/frosty_friday_zenn/.claude/skills/frosty-zenn/scripts/fflinks.py            # キャッシュ利用
   python3 ~/git/frosty_friday_zenn/.claude/skills/frosty-zenn/scripts/fflinks.py --refresh  # 新しい記事・リポジトリを取り込む
   ```

   - `! Vol.N に日付で推定割当` は、タイトルに Vol 番号がない出演・参加レポートを公開日の直前14日以内の回に割り当てたもの。内容が違えば `links_config.json` の `article_overrides` で `{"<url>": {"vol": N}}` か `{"week": N}` を指定する
   - `未分類` は Week も Vol もわからない記事。特定の Week の記事なら `article_overrides`、関係ない記事なら `exclude_articles` に入れる
   - Week 番号の誤検出をしたリポジトリは `exclude_repos` に入れる

4. 生成

   ```bash
   python3 ~/git/frosty_friday_zenn/.claude/skills/frosty-zenn/scripts/ffzenn.py render --from 30 --to 39
   ```

5. 報告: 生成ファイルのパス、手で直した箇所、未解決（リンク無し・404・不一致）を一覧で伝える。
   プレビューは `! glow -p <file>` をユーザーに案内する。

## 出演者・チャレンジャーのルール

記事では `- 出演者：〇〇、チャレンジャー：〇〇` の1行で出す（render が Week の担当者から自動で組み立てる）。

- 運営（ホスト）: Gaku・tomo（Vol.0〜）、あれ（Vol.4〜。それ以前はゲスト扱い）。`scripts/ffzenn.py` の `HOSTS` で管理。
- チャレンジャー = その回の Week 解説担当者全員（運営も含む）。Week 順・重複なし。
- 出演者 = その時点の運営メンバー全員 + チャレンジャー（重複なし）。
- `parsed.guests`（運営以外の解説者）はレビュー用に残しているが、記事には出さない。

## 名前の表記ゆれ

シートの担当者名は `names.json`（シート表記 → 記事表記）で統一する。
レビューで新しい表記ゆれを見つけたら `names.json` に追記してから `collect --force`（未レビュー回のみ対象にするなら該当範囲だけ）を実行する。
例: `"Snowflake Miyagawa": "Daishi Miyagawa（Snowflake）"`, `"sakatoku": "酒徳"`。

## 記事フォーマット（render が出力）

```markdown
## Vol.3（2024/05/30）

https://www.youtube.com/watch?v=...

- 出演者：Gaku、tomo、酒徳、チャレンジャー：Gaku、酒徳
- What's New
  - ...
- Topics
  - ...
- 番組レポート
  - [タイトル](url)（著者 / Zenn）

### Week7 – Intermediate Tags, Account Usage（Gaku）

- 番組での解説：https://github.com/...
- お題：https://www.frostyfri.day/...
- 解説記事
  - [タイトル](url)（著者 / Zenn）
- 回答リポジトリ：[Chris-Hastie](...) / [gakut12](...)

:::details その他の回答リポジトリ（7件）
[apd-jlaird](...) / ...
:::
```
