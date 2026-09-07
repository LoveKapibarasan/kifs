# 運用

## 作業台 (172.25.20.20) へのデプロイ

作業台には `~/.env.global` が必要です (Infisical のマシンID)。最低限これだけあれば動きます:

```
INFISICAL_KIFS_CLIENT_ID=...
INFISICAL_KIFS_CLIENT_SECRET=...
INFISICAL_KIFS_ENDPOINT=env.lovekapibarasan.org
```

```bash
ssh 172.25.20.20
git clone git@github.com:LoveKapibarasan/kifs.git ~/kifs
cd ~/kifs
./deploy/install.sh
```

`install.sh` は venv 作成、パッケージ導入、`~/kifs-data/` 作成、認証情報の確認、**user** systemd unit の設置・有効化・起動、`loginctl enable-linger` までを行います。root は不要です。

既存データを持ち込む場合は、install の前に転送します:

```bash
rsync -a --info=progress2 ~/kifs/data/kif/     172.25.20.20:~/kifs-data/kif/
rsync -a                  ~/kifs/data/kifu_db.json 172.25.20.20:~/kifs-data/
rsync -a                  ~/kifs/data/state/   172.25.20.20:~/kifs-data/state/
```

## 日常操作

```bash
systemctl --user status kifs-collector      # 稼働確認
journalctl --user -u kifs-collector -f      # ログ追尾
systemctl --user restart kifs-collector
systemctl --user stop kifs-collector        # SIGTERM → 状態を確定保存して停止
KIFS_DATA_DIR=~/kifs-data ~/kifs/.venv/bin/kifs status
```

`kifs status` はサービス稼働中でも実行できます (ロックを取りません)。

## 見るべき指標

```json
{
  "games_indexed": 38194,          // 収集済み対局
  "records_by_status": {
    "indexed": 38194,
    "kif_missing": 8               // 再試行待ち。増え続けるなら要調査
  },
  "records_due_now": 8,            // 今すぐ再試行できる件数
  "records_given_up": 0,           // 上限まで再試行しても取れなかった件数
  "users_never_crawled": 20034,    // 未巡回。0に近づいたら再訪モードに入る
  "users_due": 20150               // 巡回対象の総数
}
```

- `records_given_up` が増える → KIFが恒久的に非公開の対局か、`ANALYTICS_SESSION` の失効。
- `users_never_crawled` が0で `users_due` も小さい → ランキング再シードが効いているか確認。
- `db_size_mb` の伸びが止まった → フラッシュされているかログで確認。

## Cookie が失効したとき

ログに以下が出ます:

```
Session cookie rejected (ranking returned 403). Credentials came from 'infisical'.
Refresh WEB_SESSION in Infisical, then restart.
```

サービスは終了せず、間隔を空けて再試行し続けます。更新手順:

```bash
# ブラウザから新しい Cookie を取得し、環境変数に入れてから
export WEB_SESSION='...'
export ANALYTICS_SESSION='...'
kifs secrets push
systemctl --user restart kifs-collector
```

`kifs secrets check` で反映を確認できます (`source:` がどの経路で解決したかを示します)。

Infisical に到達できないときは環境変数、次に repo 直下の `.env` にフォールバックします。`.env` は削除せず、Infisical が使えない環境での退避経路として残してあります。

## 収集ペースの調整

既定は1リクエストあたり1.5秒のポライトディレイで、約1,500対局/時です。

```bash
systemctl --user edit kifs-collector
# [Service]
# Environment=KIFS_REQUEST_DELAY=1.0
# Environment=KIFS_USER_RECRAWL_HOURS=12
# Environment=KIFS_GAME_TYPES=sb,s1
```

`KIFS_GAME_TYPES` を増やすと収集対象の持ち時間が増えますが、1ユーザーあたりのリクエスト数もその分増えます。

## 日次レポートと通知

```bash
systemctl --user list-timers kifs-report.timer     # 次回送信時刻
kifs report                                        # 送信せずにプレビュー
kifs report --send                                 # 今すぐ送る
kifs report --send --to someone@example.org        # 宛先を上書きして送る
kifs report --json                                 # 生の数値
journalctl --user -u kifs-report.service -n 30     # 送信ログ
```

タイマーは `OnCalendar=*-*-* 23:00:00 UTC` (= 08:00 JST) で、`Persistent=true` のためホストが停止していた場合は復帰後に送ります。

レポートは「前回送信時からの差分」を出します。基準は `data/state/report_state.json` に保存され、**`--send` なしのプレビューでは基準を動かしません** (プレビューが差分を食い潰さないため)。

送信時刻を変えるとき:

```bash
systemctl --user edit kifs-report.timer
# [Timer]
# OnCalendar=
# OnCalendar=*-*-* 09:00:00 UTC
```

宛先を変えるときは Infisical の `REPORT_TO` を更新します (`kifs secrets push` は Cookie 用なので、REPORT_TO はダッシュボードか API で更新)。

### 即時アラート

`docs/architecture.md` の「通知」を参照。`KIFS_ALERTS_ENABLED=false` で無効化できます。

## オブジェクトストレージへの同期

```bash
systemctl --user list-timers kifs-sync.timer      # 次回同期
kifs sync --dry-run                               # 未アップロード件数
kifs sync                                         # 今すぐ同期
kifs sync --limit 1000                            # 件数を絞って同期
kifs sync --verify                                # バケットを列挙して状態を作り直す
journalctl --user -u kifs-sync.service -n 30      # 同期ログ
```

1回の同期件数は `KIFS_S3_BATCH` (既定5000) で上限を設けています。バックログが大きいときは複数回に分かれますが、毎時実行なので自然に消化されます。

`kifs status` の以下を見ます。

```json
"uploaded_to_object_store": 76134,
"upload_pending": 0
```

`upload_pending` が増え続ける場合は Silo への到達性か認証情報を疑います。

### バケットの中身を直接見る

office-router 上で:

```bash
RP=$(docker inspect silo --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MINIO_ROOT_PASSWORD=' | cut -d= -f2-)
docker exec -e MC_HOST_local="http://minioadmin:${RP}@127.0.0.1:9000" silo mc ls local/kifs/kif/ | head
docker exec -e MC_HOST_local="http://minioadmin:${RP}@127.0.0.1:9000" silo mc du local/kifs
```

## バックアップ

失って困るのは `~/kifs-data/` だけです。`kif/` があれば `kifs reconcile` でDBは再構築できるため、優先度は `kif/` > `kifs.sqlite3` > `state/` の順です。

SQLite のバックアップはコピーではなく、稼働中でも安全な以下を使います。

```bash
sqlite3 ~/kifs-data/kifs.sqlite3 ".backup ~/kifs-backup.sqlite3"
```

## SQLite への移行 (v2 → v3)

```bash
systemctl --user stop kifs-collector
kifs migrate-sqlite --dry-run     # 件数の確認
kifs migrate-sqlite               # 取り込み (38k対局で約26秒)
kifs status                       # 件数が一致するか確認
systemctl --user start kifs-collector
```

元の `kifu_db.json` と `state/frontier.json` は削除されません。切り戻す場合は旧コードに戻すだけで、そのまま読めます。
