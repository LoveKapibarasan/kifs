# 他のアプリから KIF を読む

収集した棋譜は Silo (MinIO互換のS3) のバケット `kifs` に入っています。

```
kifs/
└── kif/<game_id>.kif        例: kif/mark38-Yukikkoo-20260624_060816.kif
```

`game_id` は `<先手ID>-<後手ID>-<YYYYMMDD_HHMMSS>` です。中身は棋神解析APIが返す KIF テキスト (UTF-8) で、`先手段級` / `後手段級` に**対局時点の**段位が入っています。

## 認証情報

Infisical の **Kifs** プロジェクト (`env.lovekapibarasan.org`, env `prod`, path `/`) に入っています。

| キー | 用途 |
| --- | --- |
| `S3_ENDPOINT` | 社内から `http://172.25.50.1:9002` |
| `S3_PUBLIC_ENDPOINT` | 外部から `https://s3.lovekapibarasan.org` |
| `S3_BUCKET` | `kifs` |
| `S3_REGION` | `us-east-1` |
| `S3_READ_ACCESS_KEY` / `S3_READ_SECRET_KEY` | **読み取り専用**。他のアプリはこちらを使う |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 書き込み用。コレクタ専用 |

読み取り専用の組はバケット `kifs` の `GetObject` と `ListBucket` のみが許可されており、書き込みは 403 になります。

公開DNS (`s3.lovekapibarasan.org`) は社内ネットワークからは引けないことがあります。その場合は `S3_ENDPOINT` を使ってください。

## boto3

```python
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url="http://172.25.50.1:9002",
    aws_access_key_id="kifs-reader",
    aws_secret_access_key="...",        # S3_READ_SECRET_KEY
    region_name="us-east-1",
)

# 1局を読む
body = s3.get_object(Bucket="kifs", Key="kif/mark38-Yukikkoo-20260624_060816.kif")
kif = body["Body"].read().decode("utf-8")

# 一覧 (10万件超あるのでページングする)
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket="kifs", Prefix="kif/"):
    for obj in page.get("Contents", []):
        game_id = obj["Key"][len("kif/"):-len(".kif")]
```

`endpoint_url` を指定すると boto3 は自動的にパススタイルになります。

## mc (MinIO client)

```bash
mc alias set kifs http://172.25.50.1:9002 kifs-reader "$S3_READ_SECRET_KEY"
mc ls kifs/kifs/kif/ | head
mc cat kifs/kifs/kif/mark38-Yukikkoo-20260624_060816.kif
mc mirror kifs/kifs/kif/ ./local-kif/       # 全件をローカルへ同期
```

## rclone

```ini
[kifs]
type = s3
provider = Minio
endpoint = http://172.25.50.1:9002
access_key_id = kifs-reader
secret_access_key = ...
region = us-east-1
```

```bash
rclone ls kifs:kifs/kif | head
rclone copy kifs:kifs/kif ./local-kif --transfers 16
```

## 更新のタイミング

コレクタが1ユーザーを巡回するたびに、その時点の未アップロード分を最大300件ずつ上げます。収集ペースが約1,500対局/時なので、**新しい対局はおおむね数分以内**にバケットへ現れます。

現在の同期状況は `kifs status` で確認できます。

```json
"games_indexed": 105058,
"uploaded_to_object_store": 105058,
"upload_pending": 0
```

## 注意

- オブジェクトは**上書きされません**。同じ `game_id` の内容は変わらないためです (段位バックフィルを実行した場合のみ再アップロードされます)。
- 削除は行いません。コレクタはバケットから消すことがないので、ローカルの `kif/` を整理してもバケット側は残ります。
- Silo の総容量は 187 GiB で、KIF は1局あたり約2KBです。
