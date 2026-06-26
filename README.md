# Shogi Wars KIF Data Pipeline

将棋ウォーズの対局 ID を収集し、棋神解析ページから KIF をダウンロードして、検索しやすい JSON データベースに索引化するための Python プロジェクトです。

## できること

- 将棋ウォーズのイベントランキングからユーザー ID を収集する
- 各ユーザーの対局履歴から `wars_game_id` を抽出する
- 棋神解析ページから KIF テキストを取得し、`kif_data/` に保存する
- KIF の先手、後手、開始日時、終局理由、指し手などをパースする
- TinyDB + orjson で `kifu_db.json` に索引化する
- 対局 ID、プレイヤー名、手数、結果などで検索する
- `run_pipeline.py` でクロール、ダウンロード、索引化を継続実行する

## 構成

```text
.
├── swars_crawler.py      # ランキングと対局履歴から game_id を収集
├── kif_downloader.py     # game_id から KIF をダウンロードして保存、同時に索引化
├── index_to_nosql.py     # KIF の一括索引化、検索、統計表示
├── run_pipeline.py       # ユーザー探索から KIF 保存、索引化までを継続実行
├── games_sb.jsonl        # 収集済み game_id の JSON Lines
├── kif_data/             # ダウンロード済み .kif ファイル
├── kifu_db.json          # TinyDB 形式の検索用 DB
├── crawler_state.json    # 継続クロール用の状態ファイル
├── pipeline.log          # パイプライン実行ログ
└── requirements.txt
```

`games_*.jsonl`、`kif_data/`、`kifu_db.json`、`crawler_state.json`、`pipeline.log` は実行によって生成または更新されるデータです。

## セットアップ

Python 3 の仮想環境を作成し、依存パッケージをインストールします。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

プロジェクトルートに `.env` を作成し、将棋ウォーズのセッション Cookie を設定します。

```text
WEB_SESSION=your_session_cookie_here
```

`swars_crawler.py` は `WEB_SESSION` が未設定だと終了します。`kif_downloader.py` は Cookie なしでも初期化できますが、実運用では設定しておく前提です。

## 基本的な使い方

### 1. 対局 ID を収集する

ランキングの `start` オフセット範囲を 25 件刻みで巡回し、見つかったユーザーの対局履歴から game_id を保存します。

```bash
python3 swars_crawler.py 1 100 sb --pages 2
```

引数:

- `start`: ランキング取得開始オフセット
- `end`: ランキング取得終了オフセット
- `game_type`: 対局種別。省略時は `sb`
- `--pages`: ユーザーごとに読む履歴ページ数。省略時は `1`

出力先は `games_<game_type>.jsonl` です。例では `games_sb.jsonl` が作成または追記されます。終了時に同一 game_id は重複排除されます。

### 2. KIF をダウンロードする

収集済み JSONL を入力にして、棋神解析ページから KIF を取得します。

```bash
python3 kif_downloader.py games_sb.jsonl
```

KIF は `kif_data/<game_id>.kif` に保存されます。保存できた KIF はその場でパースされ、`kifu_db.json` に upsert されます。既に同名 KIF ファイルがある場合はスキップします。

### 3. KIF を一括で索引化する

既存の `kif_data/*.kif` をまとめて読み直し、まだ DB にない対局を `kifu_db.json` に追加します。

```bash
python3 index_to_nosql.py --index
```

入力ディレクトリや DB パスを変える場合:

```bash
python3 index_to_nosql.py --index --kif-dir kif_data --jsonl-dir . --db kifu_db.json
```

### 4. 検索する

対局 ID で検索:

```bash
python3 index_to_nosql.py --search --game-id "playerA-playerB-20260617_120000"
```

プレイヤー名で検索:

```bash
python3 index_to_nosql.py --search --player "playerName" --limit 20
```

先手、後手、終局理由、最小手数で絞り込み:

```bash
python3 index_to_nosql.py --search --sente "playerA" --gote "playerB" --result "投了" --min-moves 80
```

検索結果を JSON にエクスポート:

```bash
python3 index_to_nosql.py --search --player "playerName" --export results.json
```

### 5. DB 統計を表示する

```bash
python3 index_to_nosql.py --stats
```

総対局数、ユニークプレイヤー数、平均手数、終局理由の分布、対局数上位プレイヤーを表示します。

## 継続パイプライン

`run_pipeline.py` は、ランキングから初期ユーザーを取得し、対局履歴をたどりながら新しい対局とプレイヤーを探索します。見つけた KIF は保存し、TinyDB に索引化します。

```bash
python3 run_pipeline.py
```

動作概要:

- `crawler_state.json` からクロール済みユーザーと待ち行列を復元する
- 待ち行列が空ならランキングから初期ユーザーを取得する
- 各ユーザーの `sb` 対局履歴を最大 3 ページ取得する
- game_id から対局者名を抽出し、未処理ユーザーをキューに追加する
- 未索引の対局だけ KIF を取得して DB に追加する
- 5 ユーザーごとに状態を保存する
- 一時的なエラーでは 60 秒待って再試行する

停止時は `SIGINT` / `SIGTERM` を受けて状態保存を試みます。

## データ形式

### `games_*.jsonl`

1 行 1 対局の JSON Lines です。

```json
{"game_id":"playerA-playerB-20260617_120000","type":"sb","user":"playerA","ts":"2026-06-17T06:00:00.000000"}
```

### `kifu_db.json`

TinyDB の JSON ファイルです。各ドキュメントには主に次のフィールドが入ります。

- `game_id`
- `sente`
- `gote`
- `start_time`
- `end_time`
- `location`
- `handicap`
- `result`
- `moves`
- `total_moves`
- `raw_headers`
- `crawler_user`
- `crawler_type`
- `crawler_ts`

## 注意

- 外部サイトへアクセスするため、短時間に大量リクエストを送らないようにしてください。各スクリプトには待機処理がありますが、範囲やページ数を大きくする場合は負荷に注意してください。
- `.env` の `WEB_SESSION` は認証情報です。公開リポジトリやログに含めないでください。
- `pipeline.log` や `kifu_db.json` は大きくなりやすいファイルです。運用時は保存先とバックアップ方針を決めてください。
- game_id の形式は `先手-後手-YYYYMMDD_HHMMSS` を前提にしている箇所があります。プレイヤー名に特殊な文字が含まれる場合は抽出結果を確認してください。
