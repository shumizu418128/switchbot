# SwitchBot Lock Lambda

SwitchBot OpenAPI v1.1 経由でスマートロックに **施錠コマンド（`lock`）** を送る **AWS Lambda** 用コードです。呼び出し側は共有秘密（`x-api-key`）で保護し、**対象デバイス ID はリクエストごとに指定**します。

## 仕様の要点（旧版からの変更）

- **デバイス ID**: 環境変数の固定 ID は使わず、イベントの `device_id` で指定する。
- **施錠フロー**: 事前にデバイス状態を取得して「施錠済みならスキップ」は行わない。SwitchBot に `lock` コマンドを送るのみ。
- **認証**: ヘッダー `x-api-key` が環境変数 `API_KEY` と一致すること。

## 動作

1. ヘッダー `x-api-key` を検証する（不一致・未設定は拒否）。
2. イベントに `device_id` があるか確認する（なければエラー）。
3. `action` が `"lock"` のときだけ、`/v1.1/devices/{device_id}/commands` に施錠コマンドを POST する。
4. 成功時は SwitchBot API の JSON 応答をそのまま返す。

ハブとロックが SwitchBot アプリ上で連携済みであり、Open Token / Secret が有効であることが前提です。

## 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `API_KEY` | はい | 呼び出し側が送る固定 API キー（十分に長いランダム文字列を推奨） |
| `SWITCHBOT_TOKEN` | はい | SwitchBot アプリの Open Token |
| `SWITCHBOT_SECRET` | はい | SwitchBot アプリの Secret |
| `SWITCHBOT_API_BASE_URL` | いいえ | 省略時 `https://api.switch-bot.com` |

## イベント（入力）形式

ハンドラーは **イベントオブジェクトのトップレベル**から次を読みます。

| フィールド | 必須 | 説明 |
|------------|------|------|
| `headers` | 実質必須 | `x-api-key` を含む（大文字小文字の扱いは Lambda のイベント実装に依存） |
| `device_id` | はい | 対象スマートロックのデバイス ID |
| `action` | はい | `"lock"` のときのみ施錠を実行 |

`action` が `"lock"` 以外、または `device_id` が無い場合は HTTP 相当のエラー用ボディ（例: `400` と JSON）を返します。

**Lambda Function URL / API Gateway** で JSON ボディに `device_id` や `action` を載せる場合は、統合側でイベントのトップレベルにマッピングするか、ハンドラー側で `body` を JSON パースする処理を追加する必要があります（標準の HTTP プロキシイベントではこれらはボディ文字列内にあります）。

## Lambda の設定

- **ハンドラー**: `handler.lambda_handler`（デプロイ zip のルートに `handler.py` と `switchbot_client.py` を置く場合）
  **`src` をルートに含める構成**の場合は `src.handler.lambda_handler` に合わせ、ランタイムの作業ディレクトリ／モジュールパスが `switchbot_client` を解決できるようにしてください。
- **ランタイム**: Python 3.11 以上（`pyproject.toml` の `requires-python` に準拠）
- **依存**: 標準ライブラリのみのため、追加パッケージの同梱は不要です。

デプロイ zip の例（`src` 構成のとき）:

```
deployment.zip
  src/
    __init__.py
    handler.py
    switchbot_client.py
```

## 開発（テスト）

プロジェクトルートは `lambda` ディレクトリです。開発用依存のインストール（[uv](https://github.com/astral-sh/uv) の例）:

```bash
cd lambda
uv pip install --system ".[dev]"
python -m pytest tests/ -v
```

## 参考

- [SwitchBot API v1.1（公式 README）](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/master/README.md)
