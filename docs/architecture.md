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

`kifu_db.json` は `{テーブル名: {doc_id: ドキュメント}}` という TinyDB の形式のまま保存します。ただし読み書きは `BufferedJSONStore` が担当し、

- 起動時に一度だけメモリへロード、
- 変更はメモリ上、
- N件 / T秒 / `close()` / SIGTERM でアトミック書き込み (tmp → fsync → rename)

とします。TinyDB の `Table._update_table` は1操作ごとにテーブル全体の dict を2回作り直すため、38k件規模では書き込み経路として使っていません。形式互換は `tests/test_storage.py::test_file_is_readable_by_tinydb` が実際に TinyDB で開いて検証します。

### クラッシュ時に何が起きるか

未フラッシュの索引エントリは失われます。ただし `.kif` ファイルは取得直後にディスクへ書かれているため、次回起動時の `reconcile()` がディスクを正としてDBを再構築します。**もれは発生しません。**

## フロンティア

```json
{
  "users": {
    "takachang2": {
      "first_seen_at": "...", "last_crawled_at": "...",
      "next_crawl_at": "...", "games_found": 33, "crawls": 2
    }
  },
  "pending": ["未巡回ユーザーを発見順に"],
  "seed_cursor": 0
}
```

- 未巡回ユーザーは `pending` の deque から発見順に処理。
- 空になったら `next_crawl_at <= now` のユーザーを一度だけソートして `_due_queue` を作り直す。ユーザーごとに全走査しないための遅延再構築です。
- ユーザーは削除されません。v1 の 50,000 件切り捨ては廃止しました。

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
