# kifs — Shogi Wars KIF collection pipeline

将棋ウォーズの対局を**もれなく・重複なく**集め続けるためのパイプラインです。ユーザーを辿って対局IDを見つけ、棋神解析APIからKIFを取得し、SQLite に索引化します。作業台上では systemd サービスとして常駐します。

```
ランキング/対局履歴 ──> フロンティア(ユーザー) ──> crawl_records ──> KIF取得 ──> 索引化
   shogiwars.heroz.jp                                    kishin-analytics.heroz.jp
```

## 構成

```
src/kifs/
  config.py            設定・全パス・認証情報の解決を一元化
  clients/             shogiwars.py (Rails HTML) / kishin.py (KIF API)
  kif/parser.py        KIFパース (I/OもDBも持たない)
  storage/             sqlite.py (スキーマ) / database.py / frontier.py / export.py / migrate.py / lock.py
  pipeline/            discovery → downloader → indexer、service.py が常駐ループ
  ranks/               mypage からの段位取得・付与・バックフィル
  query/               検索と統計
  cli.py               唯一のエントリポイント (kifs ...)
deploy/                systemd unit とインストーラ
scripts/               v1レイアウトからの移行スクリプト
docs/                  設計と運用手順
data/                  生成データ (gitignore)
```

依存は一方向です: `cli → pipeline → {clients, storage, kif}`。KIFを索引化する処理は `pipeline/indexer.py` の1箇所だけにあります (v1では3ファイルに重複していました)。

## データ配置

すべて `KIFS_DATA_DIR` (既定 `./data`) の下にあります。

```
data/
├── kif/                    ダウンロード済み .kif
├── kifs.sqlite3            対局 / crawl_records / users
├── state/
│   ├── user_ranks.json     段位キャッシュ
│   ├── alerts.json         アラートのクールダウン
│   └── report_state.json   日次レポートの差分基準
├── logs/
└── derived/                KIF由来の学習データ (csa / hcpe)
```

対局・crawl_records・ユーザー巡回状態はすべて `kifs.sqlite3` の1ファイルに入ります (SQLite は書き込み1プロセスなので、接続とトランザクションを共有します)。

既存ツールが `kifu_db.json` を読む場合は、いつでも書き出せます。

```bash
kifs export                       # data/kifu_db.json を TinyDB 形式で出力
kifs export -o /path/to/out.json --games-only
```

## セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

シークレット (`WEB_SESSION` / `ANALYTICS_SESSION`) は Infisical の **Kifs** プロジェクト (`env.lovekapibarasan.org`, env `prod`) から取得します。認証は `~/.env.global` の `INFISICAL_KIFS_CLIENT_ID` / `_CLIENT_SECRET` / `_ENDPOINT`。

```bash
kifs secrets check     # どの経路で解決されたか確認
kifs secrets push      # 環境変数の値を Infisical へ登録し直す (Cookie失効時)
```

解決順序は **Infisical → 環境変数 → repo直下の `.env`** です。`.env` はフォールバックとして機能します (`.gitignore` 済み、`.env.example` 参照)。Infisical を使わない場合は `--no-infisical` を付けると `.env` / 環境変数だけを見ます。

## 使い方

```bash
kifs serve                       # 常駐収集 (systemd が起動するもの)
kifs serve --max-cycles 3        # 3ユーザー分だけ回して終了 (動作確認用)
kifs crawl --users 10            # 単発クロール
kifs download <game_id> ...      # 対局IDを指定して取得
kifs download --limit 200        # 再試行待ちのバックログを処理
kifs reconcile                   # ディスク上の .kif とDBを突き合わせ
kifs status                      # 収集状況をJSONで出力
kifs stats                       # データセットの統計
kifs search --player takachang2 --min-moves 100
kifs sync                        # 収集済みKIFをオブジェクトストレージへ同期
kifs sync --dry-run              # 何件上がるかだけ確認
kifs sync --verify               # バケットを実際に見て同期状態を作り直す
kifs report                      # 日次レポートをプレビュー
kifs report --send               # メール送信 (systemd timer が実行するもの)
kifs ranks fetch|annotate|backfill
```

## オブジェクトストレージ (Silo)

収集した `.kif` は Silo (MinIO互換, S3 API) に上げます。他のアプリはコレクタのディスクではなく**ここから読みます**。

```
bucket kifs/
└── kif/<game_id>.kif
```

アップロードは**コレクタのサイクル内**で行います (ユーザーを1人巡回するごとに最大300件)。専用タイマーにしなかったのは、**SQLiteの書き込みが1プロセスに限られる**ためです。別プロセスの同期はコレクタの書き込みロック待ちに終始します。

どのファイルが未アップロードかは `crawl_records.uploaded_at` で追跡するため、1回の同期は索引を引く1クエリで済み、バケット全体の列挙は行いません。

大量のバックログを一気に上げる場合は、コレクタを止めてから実行します。

```bash
systemctl --user stop kifs-collector
kifs sync
systemctl --user start kifs-collector
```

- アップロードに失敗したファイルは `uploaded_at` を NULL のままにするので、次回に必ず再送されます。
- ローカルの状態とバケットがずれた場合 (バケットを手で空にした、PUT とフラグ書き込みの間で落ちた) は `kifs sync --verify` がバケットを列挙して状態を作り直します。列挙は重いので、通常の同期では行いません。
- 進捗は `kifs status` の `uploaded_to_object_store` / `upload_pending` で見られます。

### 他のアプリからの読み取り

読み取り専用の認証情報が Infisical の Kifs プロジェクトに入っています (`S3_READ_ACCESS_KEY` / `S3_READ_SECRET_KEY`)。バケット `kifs` の GET と ListBucket のみが許可されており、書き込みは 403 になります。

| 用途 | エンドポイント |
| --- | --- |
| 社内ネットワークから | `http://172.25.50.1:9002` (`S3_ENDPOINT`) |
| 外部 / DNSが引ける環境から | `https://s3.lovekapibarasan.org` (`S3_PUBLIC_ENDPOINT`) |

boto3 / mc / rclone の具体的な接続例は **[docs/object-storage.md](docs/object-storage.md)** にあります。

## 通知

日次レポートを毎日 **08:00 JST** (23:00 UTC) にメール送信します。加えて、以下を検知したときは即時メールを送ります。

| 条件 | 内容 |
| --- | --- |
| `cookie_expired` | ランキング/履歴が401・403を返した (Cookie失効) |
| `collection_stalled` | サービスは稼働中なのに `KIFS_STALL_MINUTES` (既定90分) 索引化が0件 |
| `cycle_failing` | 収集サイクルが5回連続で失敗 |

同じ条件は `KIFS_ALERT_COOLDOWN_HOURS` (既定6時間) 以内に再送しません。状態は `data/state/alerts.json` に持つため、再起動ループで大量送信することはありません。条件が解消すると (対局が索引化されると) キーがクリアされ、次回の発生で即座に通知します。メール送信の失敗が収集を止めることはありません。

SMTP設定 (`SMTP_SERVER` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `REPORT_TO`) はセッションCookieと同じく Infisical の Kifs プロジェクトから取得します。

## 「もれなく」の担保

| 取りこぼし経路 | 対策 |
| --- | --- |
| 巡回済みユーザーの新規対局 | フロンティアの全ユーザーが `next_crawl_at` を持ち、`KIFS_USER_RECRAWL_HOURS` (既定24h) 後に再訪対象へ戻る |
| 対局直後でKIFが未公開 (404) | `attempts` / `next_retry_at` で指数バックオフ再試行。上限到達分も `kif_unavailable` として `kifs status` に残る |
| 履歴4ページ目以降 | 新規IDが出なくなるまでページを辿る (上限 `KIFS_MAX_HISTORY_PAGES`) |
| キューあふれ | 切り捨てを廃止。全ユーザーを保持 |
| プロセス異常終了で未フラッシュ分が消える | .kif は取得直後にディスクへ書かれる。起動時 `reconcile` がディスクを正としてDBを復元 |
| ファイルだけ消えた対局 | `reconcile` が再取得対象へ戻す |
| Cookie失効 | 401/403 を検出してログに明示し、リトライ間隔を延ばして待機 |

## 「重複なく」の担保

- `game_id → doc_id` の索引を起動時に構築し、存在判定・更新はすべて **O(1)**。
- crawl record の作成は `KifuDatabase.add_record()` の1経路のみ。既知IDは `False` を返して何も書かない。
- 対局ドキュメントの書き込みは `upsert_game()` の1経路のみ。既存フィールド (段位など) は保持してマージ。
- `data/state/kifs.lock` の `flock` により、同一データディレクトリに対する書き込みプロセスは常に1つ。

## ストレージ性能

単一JSONだった頃は、データが増えるほど全体のロードと書き直しが重くなりました。

| | v1 (TinyDB 全書き直し) | v2 (JSONバッファ) | v3 (SQLite) |
| --- | --- | --- | --- |
| 1対局あたりの書き込み | 約600MB | 0 (バッファ) | 0 (バッチcommit) |
| 起動時ロード | 全件 | 全件 (75k時 **34.5秒**) | **なし** |
| 存在判定 | O(n) 走査 | O(1) メモリ索引 | O(1) 索引 |
| 再試行対象の抽出 | 全件走査+ソート | 全件走査+ソート | 索引レンジスキャン |
| メモリ | 全件 | 全件 (約380MB→) | 定数 |

38,194対局での実測: `kifs status` が 34.5秒 → **2.6秒**。ロードが消えたため、データが増えても起動コストは増えません。書き込みは `KIFS_FLUSH_EVERY_WRITES` (既定200件) / `KIFS_FLUSH_EVERY_SECONDS` (既定60秒) 、および SIGTERM 受信時に commit します。収集レートは約1,500対局/時で、`KIFS_REQUEST_DELAY=1.5` のポライトディレイ律速です (ストレージ律速ではありません)。

## 作業台へのデプロイ

`docs/operations.md` を参照。要約:

```bash
ssh 172.25.20.20
git clone git@github.com:LoveKapibarasan/kifs.git ~/kifs
cd ~/kifs && ./deploy/install.sh
journalctl --user -u kifs-collector -f
```

## 移行

**v2 (JSON) から v3 (SQLite) へ:**

```bash
kifs migrate-sqlite --dry-run
kifs migrate-sqlite
```

`data/kifu_db.json` と `data/state/frontier.json` を読んで `data/kifs.sqlite3` に取り込みます。専用カラムを持たないフィールド (`rank_backfilled_at` など) は `games.extra` に入るため欠落しません。**元のJSONは削除しません**。再実行しても重複しません (すべてid keyのupsert)。実測: 38,194対局 + 61,122ユーザーで26秒。

**v1 (ルート直下配置) から:**

```bash
python scripts/migrate_v1_layout.py --dry-run
python scripts/migrate_v1_layout.py --move
kifs migrate-sqlite
```

## テスト

```bash
.venv/bin/python -m pytest -q
```
