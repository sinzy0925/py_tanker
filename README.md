# py_tanker

MarineTraffic の地図表示に伴う **Fetch/XHR（`station:0`）** と、船舶詳細ページの **XHR JSON** を Chrome の **CDP（Chrome DevTools Protocol）** 経由で取得し、任意条件で絞り込み、**位置の前後差分**まで一気通貫で扱うための **最小構成の Python ツール群**です。

## 前提

- **Python 3.12+**（例: 3.12）
- **Google Chrome**（CDP 用にローカルで起動）
- 利用は **利用規約・権利に反しない範囲**に限定してください（手元の解析・少頻度の取得を想定）。

## セットアップ

```powershell
cd py_tanker
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

（`requirements.txt` には他用途向けのパッケージも含まれますが、本パイプラインの主役は **Playwright** です。）

## ディレクトリと成果物

| パス | 内容 |
|------|------|
| `ship_data/station0_all.json` | cdp1: `station:0` 応答の保存（`--show-all` で複数キャプチャ） |
| `ship_data/out.jsonl` | cdp2: `station0_all` からの船舶行（既定 `--mode all` + `--dedupe-by-ship-id` で件数は多め） |
| `ship_data/ship_details.json` | cdp3: `out.jsonl` の各船の詳細 XHR（全件） |
| `ship_data/ship_details_prev.json` | cdp3 が上書き前に退避した **直前** の `ship_details.json` |
| `ship_data/ship_details_jp.json` | cdp4_ship_details_filter: 航路が「日本向けっぽい」船だけを抽出 |
| `ship_data/ship_details_jp_prev.json` | 上書き前に退避した **直前** の `ship_details_jp.json` |
| `ship_moved/moved_report_*.json` | cdp5_diff: 位置差分レポート（日本時間のタイムスタンプ付きファイル名） |

`.gitignore` により、**再生成可能な成果物** はコミット対象外にできます（リポジトリはスクリプト中心）。

## パイプライン一覧

| スクリプト | 役割 |
|------------|------|
| `cdp0_run_cdp_pipeline.py` | cdp1 → cdp2 → cdp3 → cdp4_ship_details_filter → cdp5_diff を既定引数で順実行 |
| `cdp1_fetch_station0_playwright.py` | 地図を開き `station:0` の JSON を `ship_data/station0_all.json` に保存 |
| `cdp2_mt_snapshot_filter.py` | `station0_all.json` を読み、`--mode all` 等で `ship_data/out.jsonl` 出力 |
| `cdp3_fetch_ship_details.py` | `out.jsonl` の `SHIP_ID` ごとに詳細 XHR を取得し `ship_data/ship_details.json` に保存 |
| `cdp4_ship_details_filter.py` | `ship_details.json` から日本向けっぽい船だけを `ship_details_jp.json` に抽出（既存があれば `_prev` へ退避）。手動で `--also-japan-mid` を付けると `general.mmsi` の MID 431–439 も OR で含める（cdp0 では未使用） |
| `cdp5_diff_ship_positions.py` | `ship_details_jp_prev.json` と `ship_details_jp.json` を比較し移動判定を `ship_moved/` に出力 |
| `japan_wide_signals.py` | 目的地文字列の日本関連ヒント（cdp2 / cdp4_ship_details_filter で利用） |

## 一括実行

```powershell
.\.venv\Scripts\activate
python cdp0_run_cdp_pipeline.py
```

### Google Cloud Shell（tmux）

リポジトリ直下の `run_tmux_cdp0.sh` は、**スクリプトがあるディレクトリをリポジトリルートとして** `cdp0` を tmux で起動する。親ディレクトリからでも `bash py_tanker/run_tmux_cdp0.sh` のように呼べる。

```bash
chmod +x run_tmux_cdp0.sh
./run_tmux_cdp0.sh
tmux attach -t cdp0
```

スクリプト先頭の `INSTALL_CHROME` が既定 `1` のとき、**Linux かつ apt** なら **Google Chrome（`google-chrome-stable`）** を入れ、`.venv` があり **playwright が入っている**場合は **`playwright install-deps` / `playwright install chromium`** も実行する。ブラウザ処理を入れたくない場合は先頭を `INSTALL_CHROME=0` にするか `INSTALL_CHROME=0 ./run_tmux_cdp0.sh` とする。

### 初回だけ cdp5_diff が失敗することがある

`cdp4_ship_details_filter` は **2回目以降**、既存の `ship_data/ship_details_jp.json` を `ship_data/ship_details_jp_prev.json` に退避してから新規保存します。  
そのため **1回目のパイプラインでは `ship_details_jp_prev.json` が無く、`cdp5_diff_ship_positions` がエラー終了**することがあります。**もう一度 `cdp0` を実行**すれば、差分まで通ります。

## 手動実行（例）

```powershell
python cdp1_fetch_station0_playwright.py --show-all
python cdp2_mt_snapshot_filter.py --mode all --dedupe-by-ship-id --jsonl ship_data/out.jsonl
python cdp3_fetch_ship_details.py --show-all
python cdp4_ship_details_filter.py
python cdp5_diff_ship_positions.py
```

引数を省略した場合のデフォルトは、各スクリプトが `ship_data/` 配下を指すようになっています。

## 注意

- **MarineTraffic の利用規約**に従い、過度な自動アクセス・再配布・商用利用の可否は各自で確認してください。
- CDP 用に Chrome がユーザープロファイル（`.chrome-cdp-profile/`）を作ります。**Git に含めない**でください（ロックで `git add` が失敗する場合があります）。
- 表示される `(node:...) DeprecationWarning` は Playwright 内部の Node 由来のことが多く、取得結果そのものとは無関係なことが多いです。
- **Linux** で CDP 用 Chrome を自動起動するときは既定で **`--headless=new`**（`cdp1` / `cdp3` の `--chrome-headless`、Windows では `--no-chrome-headless` でウィンドウ表示）。
- `cdp4_ship_details_filter` の判定は **voyage の `reportedDestination`** を `japan_wide_signals` でヒットするかどうかのヒューリスティックであり、**実航路の保証ではありません**。

## ライセンス

リポジトリに `LICENSE` が無い場合は、利用者の責任で利用範囲を判断してください。
