# SwitchBot Lambda

SwitchBot OpenAPI v1.1 経由の CO2 センサー監視と、API Gateway 経由の WiFi 在宅判定を行う **AWS Lambda** 用コードです。

## 動作

- **スケジュール（5分ごと）**: CO2・湿度をチェックし、閾値超過時に Slack へ通知（SSM で通知状態を管理）
- **POST `/wifi`**: クライアント Webhook（`wifi_connected` / `wifi_disconnected`）に応じて在宅状態を更新し、変化時のみフック処理後に SSM へ `at_home` を保存（API Gateway の API Key 必須）

## 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `TOKEN` | はい | SwitchBot Open Token |
| `CLIENT_SECRET` | はい | SwitchBot Secret |
| `SLACK_WEBHOOK_URL` | はい | Slack Incoming Webhook URL |
| `HOME_WIFI_SSID` | はい | 家の WiFi SSID（在宅判定用） |
| `SWITCHBOT_API_BASE_URL` | いいえ | 省略時 `https://api.switch-bot.com` |
| `ALERT_STATE_PARAM` | デプロイ時設定 | CO2 通知状態の SSM パラメータ名 |
| `WIFI_STATE_PARAM` | デプロイ時設定 | 在宅状態の SSM パラメータ名 |

## SSM（在宅状態）

`WIFI_STATE_PARAM` に保存する JSON 例:

```json
{"at_home": false, "updated_at": 1710000000}
```

## API（`/wifi`）

| フィールド | 必須 | 説明 |
|------------|------|------|
| `event` | はい | `wifi_connected` または `wifi_disconnected` |
| `ssid` | 接続時のみ | 接続中の WiFi SSID（`wifi_connected` のみ） |
| `timestamp` | いいえ | クライアント送信時刻（ISO-8601、サーバーでは未使用） |

接続時リクエスト例:

```json
{
  "event": "wifi_connected",
  "ssid": "MyHomeWiFi",
  "timestamp": "2026-05-20T12:00:00+09:00"
}
```

切断時リクエスト例:

```json
{
  "event": "wifi_disconnected",
  "timestamp": "2026-05-20T18:00:00+09:00"
}
```

`wifi_connected` では `ssid` を `HOME_WIFI_SSID` と比較します。`wifi_disconnected` では在宅だった場合に外出扱いとします。

成功時レスポンス例:

```json
{"ok": true, "at_home": true}
```

認証は API Gateway の `x-api-key` ヘッダーです。

在宅状態が変化したとき、`switchbot_service.py` の `on_arrived_home` / `on_left_home` が呼ばれます（実装は各自で追加）。

## コード構成

| モジュール | 役割 |
|------------|------|
| `router.py` | Lambda エントリ・イベント種別の判定（スケジュール / API Gateway） |
| `routes/schedule.py` | 定期実行タスク（`SCHEDULE_HANDLERS` に action を登録） |
| `routes/api.py` | HTTP API（`API_ROUTES` にパスを登録） |
| `api_http.py` | API Gateway 用の body パース・レスポンス組み立て |

新機能追加時は、スケジュールなら `routes/schedule.py`、HTTP API なら `routes/api.py` と `template.yaml` に追記する。

## Lambda の設定

- **ハンドラー**: `router.dispatch`
- **ランタイム**: Python 3.13（`pyproject.toml` の `requires-python` に準拠）

## 開発（テスト）

プロジェクトルートは `lambda` ディレクトリです。開発用依存のインストール（[uv](https://github.com/astral-sh/uv) の例）:

```bash
cd lambda
uv pip install --system ".[dev]"
python -m pytest tests/ -v
```

## 参考

- [SwitchBot API v1.1（公式 README）](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/master/README.md)
