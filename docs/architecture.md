# 設計

## レイヤ

依存は上から下への一方向のみ。

```
cli.py
  └── pipeline/          service → discovery / downloader / indexer
        ├── clients/     HTTPだけを知っている
        ├── storage/     永続化だけを知っている
        └── kif/         パースだけを知っている
              config.py  全レイヤが読む設定 (誰にも依存しない)
```

v1 では `index_to_nosql.py` が「ストレージ定義 + パーサ + 索引 + 検索 + 統計 + CLI」を兼ね、`run_pipeline.py` / `kif_downloader.py` / `index_to_nosql.py` の3箇所に同じ索引処理が書かれていました。現在は `pipeline/indexer.py::index_kif_file` の1箇所だけです。

## 収集サイクル

`CollectorService._cycle()` が1周で行うこと:

1. **再試行の消化** — `due_records()` が返す、KIFが未公開だった対局のうち再試行時刻を過ぎたもの。
2. **ユーザーを1人クロール** — 未巡回のユーザーを優先し、いなければ再訪期限を最も過ぎているユーザー。対局履歴を「新規IDが出なくなるまで」辿る。
3. **その対局を収集** — 未取得のものをダウンロードして索引化。両対局者をフロンティアに追加 (グラフ探索)。

フロンティアに処理対象がない場合、**ループを抜けずに**ランキングから再シードします。v1 はここで静かに終了していました。

## 状態機械 (crawl_records)

```
             discover
                │
                ▼
         kif_missing ──────(404)──────┐
                │                     │ attempts++
                │ 取得成功            │ next_retry_at = now + 30min * 2^(n-1)
                ▼                     │ (上限 24h)
        kif_downloaded ◄──────────────┘
                │                     │
                │ パース成功          │ attempts >= 12
                ▼                     ▼
            indexed             kif_unavailable
```

`indexed` と `kif_unavailable` だけが `is_due()` で False を返します。`kif_unavailable` は捨てずに残し、`kifs status` の `records_given_up` に出ます。

## ストレージ

`data/kifs.sqlite3` に `games` / `crawl_records` / `users` / `meta` の4テーブルを持ちます (WAL、`synchronous=NORMAL`)。

単一JSONをやめた理由は、コストがフォーマットではなく**構造**にあったためです。1つのJSONオブジェクトに全対局を入れる限り、読むには全部パースし、書くには全部シリアライズする必要があります。収集が回り始めると1日で 38k件/157MB/1.0秒 が 75k件/306MB/**34.5秒** になりました。

| 操作 | JSON | SQLite |
| --- | --- | --- |
| 起動 | 全件パース | なし |
| `has_game` | メモリ索引 (要全件ロード) | 主キー参照 |
| `due_records` | 全件走査 + Pythonソート | `idx_records_due` のレンジスキャン |
| `next_user` | 全件走査してdue queue再構築 | `idx_users_next` の LIMIT 1 |
| メモリ | データ量に比例 | 定数 |

### 単一ライタと共有トランザクション

SQLite の書き込みは1接続だけです。`KifuDatabase` が接続と `Transaction` を所有し、`Frontier` はそれを**共有**します。別々に接続すると互いの `BEGIN` でデッドロックするためです。commit は N件 / T秒 / `close()` / SIGTERM で行います。

`Transaction.commit()` は自前のフラグではなく `connection.in_transaction` を真とします。DDL やエラーロールバックがトランザクションを裏で終わらせることがあり、フラグだけを見ると `cannot commit - no transaction is active` で落ちるためです。

### ユーザーのリース

`next_user()` が pop からクエリになったため、選択しただけでは同じユーザーが返り続けます。クロールが例外で落ちると同一ユーザーを永久に回すことになるので、`claim_user()` は選択と同時に短いリース (既定15分) を張ります。失敗・中断したクロールはリース満了後に戻ってくるだけで、失われません。`mark_crawled()` がリースを本来の再訪間隔に置き換えます。

### クラッシュ時に何が起きるか

未コミットのトランザクション分は失われます。ただし `.kif` ファイルは取得直後にディスクへ書かれているため、次回起動時の `reconcile()` がディスクを正としてDBを再構築します。**もれは発生しません。**

### 既存ツールとの互換

`kifs export` が `{"_default": {...}, "crawl_records": {...}}` 形式の `kifu_db.json` を書き出します。`tests/test_storage.py::test_export_is_readable_by_tinydb` が実際に TinyDB で開いて検証しています。

## フロンティア

`users` テーブル (`user_id`, `first_seen_at`, `last_crawled_at`, `next_crawl_at`, `games_found`, `crawls`)。

```sql
SELECT user_id FROM users
WHERE next_crawl_at IS NULL OR next_crawl_at <= :now
ORDER BY next_crawl_at ASC, first_seen_at ASC LIMIT 1;
```

SQLite は `ORDER BY` で NULL を先頭に置くため、この1本のクエリで「未巡回ユーザー優先、次に再訪期限を最も過ぎたユーザー」という優先順位がそのまま得られます (`idx_users_next` が効きます)。

ユーザーは削除されません。v1 の 50,000 件切り捨ては廃止しています。

## 並行実行

書き込みプロセスは常に1つです (`data/state/kifs.lock` の `flock`)。2つ目の `kifs serve` は起動を拒否して終了コード3を返します。読み取り専用コマンド (`status` / `stats` / `search`) はロックを取らないので、サービス稼働中でも実行できます。

## 通知

`kifs.notify` は収集パイプラインから片方向に呼ばれるだけで、逆向きの依存はありません。

```
pipeline/service.py ──> notify/alerts.py ──> notify/mailer.py
cli.py (report)     ──> notify/report.py ──┘
```

設計上の約束が3つあります。

1. **メールの失敗は収集を止めない。** `Alerter.fire()` は `MailError` を捕まえてログに落とし、`False` を返します。
2. **同じ問題で何通も送らない。** アラートはキーごとにクールダウンを持ち、状態は `data/state/alerts.json` にあります。再起動してもクールダウンは維持されるため、クラッシュループが大量送信になりません。
3. **解消したら次は即座に鳴る。** 対局が索引化されるたびに `cookie_expired` と `collection_stalled` のキーをクリアします。

日次レポートの差分基準は `data/state/report_state.json` です。`build_report(persist=False)` (プレビュー) は基準を更新しないため、`kifs report` を何度実行しても翌日の差分は正しいままです。

停止検知 (`collection_stalled`) が最も重要です。Cookieが失効したり巡回対象が枯渇したりしても `systemctl status` は `active (running)` のままで、外形からは健全なループと区別がつかないためです。

## オブジェクトストレージへの同期

```
pipeline/service.py ─┬─> pipeline/upload.py ──> clients/s3.py ──> Silo (MinIO互換)
cli.py (sync)  ──────┘          └──> crawl_records.uploaded_at
```

アップロードは**コレクタのサイクル内**で動きます。専用のタイマーで別プロセスにしないのは、**SQLiteの書き込みが1プロセスに限られる**ためです。コレクタはコミット間隔の間ずっと書き込みトランザクションを保持するので、別プロセスの同期は `database is locked` を待ち続けることになります (実際に踏みました)。1サイクルあたりの件数を上限付きにして、クロールを止めないようにしています。

同じ理由で、`connect()` が毎回 `meta` にスキーマバージョンを書き込んでいたのも問題でした。**DBを開くだけで書き込みロックを取る**ため、稼働中のコレクタと衝突します。現在は値が変わったときだけ書きます。

オブジェクトストレージが落ちていても収集は止めません (`_drain_uploads` は例外をログに落として続行)。

`clients/s3.py` は SigV4 署名を自前で持ちます (PUT / HEAD / GET list のみ)。boto3 を入れると依存が一気に増えるため、コレクタの依存は httpx + orjson + python-dotenv のままにしてあります。アドレッシングはパススタイル (`<endpoint>/<bucket>/<key>`) で、バケットごとのDNSを必要としません。

**署名対象と送信パスが一致していること**が重要です。キーの各セグメントを `quote(safe="")` でエスケープし、`/` 区切りだけを残しています。httpx はそのURLを再エンコードしないため二重エンコードは起きません (`tests/test_upload.py::test_keys_with_special_characters_are_escaped` が実際の wire path で検証)。

未アップロードの判定は `crawl_records.uploaded_at IS NULL` で、`idx_records_upload` が効きます。毎回バケットを列挙する設計にしなかったのは、再試行スケジューラと同じ理由です。列挙が必要になるのは状態がずれたときだけなので、`--verify` として明示的に呼ぶ形にしています。

アップロード失敗時は `uploaded_at` を NULL のままにします。「成功したものだけ記録する」ことで、取りこぼしが起きないようにしています。
