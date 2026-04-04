# SwitchBot Lock Lambda

SwitchBot ハブ経由でスマートロックを施錠する **AWS Lambda** 用コードです。
**Lambda Function URL** を前面に置き、`POST` リクエストと `x-api-key` で保護します。

## 動作

1. `POST`（パスは `/lock` または `/`）を受け取る。
2. ヘッダー `x-api-key` が環境変数 `API_KEY` と一致するか検証する。
3. SwitchBot OpenAPI でデバイス状態を取得し、`lockState` が施錠済みなら **何も送らず** `{"ok": true, "result": "already_locked"}` を返す。
4. 未施錠なら `lock` コマンドを送信し `{"ok": true, "result": "locked"}` を返す。
5. `lockState` が `jammed` のときは施錠せず HTTP `502`（`device_state`）を返す。

ハブ（Hub 2 等）とロックがアプリ上で連携済みであること、および OpenAPI のトークンが有効であることが前提です。

## 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `API_KEY` | はい | 呼び出し側が送る固定 API キー（十分に長いランダム文字列を推奨） |
| `SWITCHBOT_TOKEN` | はい | SwitchBot アプリの Open Token |
| `SWITCHBOT_SECRET` | はい | SwitchBot アプリの Secret |
| `SWITCHBOT_DEVICE_ID` | はい | 対象スマートロックのデバイス ID |
| `SWITCHBOT_API_BASE_URL` | いいえ | 省略時 `https://api.switch-bot.com` |

## Lambda の設定

- **ハンドラー**: `src.handler.lambda_handler`
- **ランタイム**: Python 3.11 以上を推奨（ローカル検証は 3.13 でも可）
- **デプロイパッケージ**: 次のように `src` パッケージごとルートに含める。

```
deployment.zip
  src/
    __init__.py
    handler.py
    models.py
    switchbot_client.py
```

標準ライブラリのみ使用しているため、依存パッケージの同梱は不要です。

### Function URL

- **認証タイプ**: `NONE`（アプリ側で `x-api-key` を検証）
- **許可メソッド**: `POST` のみ推奨
- 呼び出し例:

```http
POST https://xxxxxxxx.lambda-url.ap-northeast-1.on.aws/lock
x-api-key: <API_KEYと同じ値>
```

## 開発（テスト）

開発用オプション依存のインストール（[uv](https://github.com/astral-sh/uv) を使用する場合の例）:

```bash
cd lambda/switchbot_lock_api
uv pip install --system "pytest>=8"
python -m pytest tests/ -v
```

## レスポンス一覧（概要）

| HTTP | 用途 |
|------|------|
| 200 | 施錠済みスキップまたは施錠成功 |
| 401 | API キー不正 |
| 404 | パス不正 |
| 405 | POST 以外 |
| 502 | SwitchBot API エラー、または jammed |
| 500 | 設定不備・内部エラー |

## 参考

- [SwitchBot API v1.1（公式 README）](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/master/README.md)
