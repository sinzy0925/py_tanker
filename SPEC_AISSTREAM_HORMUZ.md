# 仕様書：IMO／MMSI 起点マスタと AISstream による「ホルムズ滞在タンカー」検出

| 項目 | 内容 |
|------|------|
| 文書 ID | SPEC-AISSTREAM-HORMUZ-001 |
| ステータス | ドラフト（v0.4） |
| 想定データソース | [AISstream](https://aisstream.io/) WebSocket API のみ（HTML スクレイピングは行わない） |

---

## 1. 目的

1. 事前に把握した **IMO（および可能なら MMSI）** を起点とする**マスタデータ**を整備する。
2. AISstream を用いて、**ホルムズ海峡周辺の関心海域（AOI: Area of Interest）** 内に存在する船舶の AIS を受信する。
3. そのうえで、**一定時間以上 AOI 内にとどまっているタンカー**を識別し、一覧化・状態管理する。

本仕様は**受信・判定ロジックの要件**を定義する。マスタの IMO→MMSI 解決に用いる外部 DB（Equasis 等）は**別モジュール／別仕様**とし、ここではインタフェースのみ触れる。

---

## 2. 用語定義

| 用語 | 定義 |
|------|------|
| **マスタ** | IMO を主キーとし、MMSI・船名スナップショット・更新日時等を保持する静的／準静的データ集合。 |
| **AOI** | 緯度・経度で定義する**矩形（Bounding Box）**。ホルムズ周辺の監視範囲。複数矩形を許容する。 |
| **滞在（dwell）** | 同一船舶（MMSI）について、AOI 内にいる状態が、後述の**閾値**を満たす**継続時間**以上続いていること。 |
| **タンカー** | AIS **静的情報**における **Ship and Cargo Type**（船種コード）が、本仕様で定める**タンカー系コード範囲**に含まれる船舶。 |
| **ウォッチリスト** | 監視対象とする IMO／MMSI の集合。AISstream の `FiltersShipMMSI` 利用時は **最大 50 MMSI** の制約がある。 |

---

## 3. 外部参照（AISstream）

| 参照先 | URL / 備考 |
|--------|------------|
| API リファレンス | https://aisstream.io/documentation |
| メッセージ型定義（OpenAPI） | https://github.com/aisstream/ais-message-models/blob/master/type-definition.yaml |
| サンプル実装 | https://github.com/aisstream/example |
| 問い合わせ | https://github.com/aisstream/issues |

---

## 4. 前提・制約（AISstream）

以下はドキュメントに基づく**技術制約**。変更される可能性があるため、実装前に最新ドキュメントを確認すること。

### 4.1 接続

| 項目 | 内容 |
|------|------|
| 接続 URL | `wss://stream.aisstream.io/v0/stream` |
| プロトコル | **WSS のみ**（平文 WS 不可の趣旨） |
| 認証 | 購読 JSON 内の **API Key**（ユーザー登録後に発行） |

### 4.2 購読タイムアウト

- WebSocket 確立後、**購読メッセージを 3 秒以内**に送信すること。
- 超過した場合、**接続はクローズ**される。

### 4.3 MMSI フィルタ上限

- `FiltersShipMMSI` に指定できる MMSI は **最大 50 件**（文字列形式のリスト）。
- 51 隻以上を同時に MMSI フィルタで絞り込むことは**不可**。
  - **対策**: 複数接続に分割（キー・レート制限に注意）、または **MMSI フィルタを使わず AOI のみ**で受信し、クライアント側でマスタと照合する。

### 4.4 サービス品質

- サービスは **BETA**。
- **SLA なし**、**稼働保証なし**。
- API・オブジェクトモデルは**非安定**（破壊的変更があり得る）。

### 4.5 スロットリング・切断

- **API キー単位・ユーザー単位**でスロットリングがある。
- 下流の読み取りが追いつかず**送信キューが肥大**すると、接続がクローズされ得る。
- ドキュメントでは、世界全域購読時に**平均 300 メッセージ／秒**を処理できるリソースが目安とされる。

### 4.6 セキュリティ・配置

- **ブラウザからの直接接続（CORS）は想定されていない**。API キーを公開サイトに埋め込まない。
- **推奨アーキテクチャ**: バックエンドが WebSocket で AISstream に接続し、必要なら自前の API でクライアントに配信する。

### 4.7 購読 JSON のキー表記

- OpenAPI スキーマ（`SubscriptionMessage`）では必須キーは **`APIKey`**（先頭大文字の A/P/I/K）である。
- 公式ドキュメントの一部コード例では **`Apikey`** と小文字の `i` になっている箇所がある。**実装は `APIKey` を第一候補**とし、接続失敗時のみ例示に合わせて試す。

### 4.8 購読メッセージの構造（OpenAPI 準拠）

`type-definition.yaml` の `SubscriptionMessage` に基づく。

| キー | 必須 | 制約 |
|------|------|------|
| `APIKey` | ○ | 文字列 |
| `BoundingBoxes` | ○ | **矩形の配列**。各矩形は **ちょうど 2 頂点**の配列。各頂点は **`[latitude, longitude]`**（いずれも `double`）。 |
| `FiltersShipMMSI` | 任意 | 文字列の配列。**各要素は長さ 9**（`minLength`/`maxLength` 9）。数値型で送らない。 |
| `FilterMessageTypes` | 任意 | 列挙型名の配列。重複不可。 |

**BoundingBoxes の入れ子（1 矩形の例）**

- 論理: **矩形の配列**。各矩形は **2 頂点** `[lat, lon]` の配列（頂点順不同）。
- JSON 例（1 矩形）— **角括弧は 3 重**（公式 Python 例 `[[[-90,-180],[90,180]]]` と同じ段数）:

```json
"BoundingBoxes": [[[27.40, 55.30], [24.60, 59.20]]]
```

複数矩形のときは **4 重にならない**。例: 2 矩形なら  
`[[[lat1,lon1],[lat2,lon2]], [[lat3,lon3],[lat4,lon4]]]`。

---

## 5. 全体アーキテクチャ（論理）

```
┌─────────────────────┐
│ マスタ整備           │  IMO リスト（＋任意 MMSI）
│ （別プロセス可）     │  MMSI 未登録 → 外部で IMO↔MMSI 解決（任意）
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ AISstream クライアント │  WSS 接続 → 購読（AOI ± FiltersShipMMSI）
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ メッセージ処理        │  PositionReport / ShipStaticData 等をパース
│                      │  MMSI 単位で状態を更新
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ タンカー判定          │  静的情報の船種コード（80–89 等）
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 滞在判定              │  AOI 内継続時間・SOG・ナビステータス
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 出力                  │  JSONL / DB / ログ 等（実装任せ）
└─────────────────────┘
```

---

## 6. マスタデータ仕様

### 6.1 エンティティ：船舶マスタ行

| フィールド | 必須 | 型（論理） | 説明 |
|------------|------|------------|------|
| `imo` | ○ | 文字列 | **7 桁**の IMO 番号。先頭ゼロの有無を**システム内で統一**する（推奨: 常に 7 桁ゼロ埋め）。 |
| `mmsi` | △ | 文字列 | **9 桁** MMSI。`FiltersShipMMSI` を使うモードでは必須。 |
| `name_snapshot` | 任意 | 文字列 | 照合用の船名（任意時点のスナップショット）。 |
| `master_source` | 任意 | 文字列 | マスタの出典（例: `fleet_csv`, `equasis`）。 |
| `master_updated_at` | 推奨 | UTC 日時 | マスタ行の最終更新時刻。 |

### 6.2 MMSI 未登録の IMO

- AISstream は **IMO でのフィルタ購読を提供しない**（MMSI 最大 50 のみ）。
- 対応方針（いずれか、または併用）:
  1. **外部ソース**で IMO→MMSI を解決してからマスタに格納する。
  2. **探索モード**（後述）で AOI のみ購読し、**静的情報に含まれる IMO** とマスタ IMO を突き合わせる。

### 6.3 マスタの保存形式

- 実装自由（CSV / JSON / SQLite 等）。
- 本仕様で必須とするのは**上記フィールドの意味と必須性**のみ。

### 6.4 IMO／MMSI の正規化・検証

| 項目 | ルール |
|------|--------|
| **IMO** | 表示・突合は **7 桁の数字文字列**に正規化する（例: 整数 `9123456` → `"9123456"`）。IMO はチェックディジット付き（最終桁が検証用）だが、**本仕様ではチェックディジット検証を必須とはしない**（マスタ品質が低いデータでも動かすため）。必要ならオプション検証とする。 |
| **MMSI** | **9 桁の数字文字列**。AIS の `UserID` は JSON では **整数**で届くことがあるため、**先頭ゼロを落とさないよう** `UserID` → 文字列化時に **`%09d` 相当のゼロ埋め**を必須とする（例: `431234567` はそのまま、`12345` は `000012345`）。 |
| **MMSI 妥当性（任意）** | MID（先頭 3 桁）が ITU 割当と一致するか等は**オプション警告**とする。 |

### 6.5 外部 IMO→MMSI 解決（インタフェース）

本仕様のスコープ外モジュールだが、接続は次のインタフェースで定義する。

| 項目 | 内容 |
|------|------|
| 入力 | `imo`（7 桁文字列）、必要なら `name_snapshot` |
| 出力 | `mmsi`（9 桁文字列）または `not_found`、任意で `confidence`、`source`、`resolved_at_utc` |
| 副作用 | レート制限遵守、キャッシュ（同一 IMO の再問い合わせ抑制） |
| 失敗時 | マスタの `mmsi` を空のままにし、**探索モード**または **静的情報待ち**でカバー |

---

## 7. AOI（ホルムズ周辺）定義

### 7.1 AISstream における Bounding Box

- 形式: `BoundingBoxes` は **矩形のリスト**。各矩形は **2 頂点** `[[lat1, lon1], [lat2, lon2]]`。
- **頂点の順序は不問**（ドキュメント記載）。
- **複数矩形**を指定可能。重なりによる**メッセージの重複は発生しない**（ドキュメント記載）。

緯度は **-90.0〜90.0**、経度は **-180.0〜180.0** の実数（度）。

### 7.2 設定パラメータ

| パラメータ名 | 説明 |
|--------------|------|
| `aoi_boxes` | 上記形式の矩形配列。設定ファイルまたは環境変数で与える。 |

### 7.3 デフォルト例（初期値・運用で差し替え）

**狭域（海峡付近のみの例）**

- 頂点1: 緯度 `26.85`, 経度 `56.15`
- 頂点2: 緯度 `25.85`, 経度 `56.85`

```json
"BoundingBoxes": [[[26.85, 56.15], [25.85, 56.85]]]
```

**広域（オマーン湾〜ペルシャ湾口付近を含む例）**

- 頂点1: 緯度 `27.40`, 経度 `55.30`
- 頂点2: 緯度 `24.60`, 経度 `59.20`

```json
"BoundingBoxes": [[[27.40, 55.30], [24.60, 59.20]]]
```

※ 実際の「待機海域」をカバーするには、運用知見に基づき矩形を追加・調整する。

### 7.4 点の包含判定

- 入力: 緯度 `lat`、経度 `lon`、矩形 `[[lat_a, lon_a], [lat_b, lon_b]]`。
- 判定:  
  `min(lat_a, lat_b) ≤ lat ≤ max(lat_a, lat_b)` かつ  
  `min(lon_a, lon_b) ≤ lon ≤ max(lon_a, lon_b)`  
  （経度が日付変更線をまたぐ矩形は本仕様のデフォルト例では想定しない。必要なら別アルゴリズムとする。）

- **複数矩形**のとき: いずれかの矩形に含まれれば **AOI 内**。

---

## 8. AISstream 購読メッセージ仕様

### 8.1 購読 JSON スキーマ（論理）

**詳細・OpenAPI との対応は 4.8 を参照。** ここでは概要のみ。

| キー | 必須 | 型 | 説明 |
|------|------|-----|------|
| `APIKey` | ○ | 文字列 | API キー。 |
| `BoundingBoxes` | ○ | 配列 | 4.8 の入れ子構造。 |
| `FiltersShipMMSI` | 任意 | 文字列配列（各 **9 文字**） | 最大 **50** 個。 |
| `FilterMessageTypes` | 任意 | 文字列配列 | **同一型を二重指定するとエラー**。 |

### 8.2 購読例（論理）

MMSI フィルタあり（ウォッチリストモード）、メッセージ型を限定:

```json
{
  "APIKey": "<SECRET>",
  "BoundingBoxes": [[[27.40, 55.30], [24.60, 59.20]]],
  "FiltersShipMMSI": ["431000000", "431000001"],
  "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
}
```

MMSI フィルタなし（探索モード）、広域 AOI のみ:

```json
{
  "APIKey": "<SECRET>",
  "BoundingBoxes": [[[27.40, 55.30], [24.60, 59.20]]],
  "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
}
```

### 8.3 推奨 `FilterMessageTypes`

| 型名 | 用途 |
|------|------|
| `PositionReport` | 位置、SOG、COG、Navigation Status 等。 |
| `ShipStaticData` | 船名、**IMO**、**Ship and Cargo Type**（タンカー判定）、寸法等。 |

**任意追加**（対象とする船舶が Class B のみで送る場合など）:

- `StandardClassBPositionReport`
- `ExtendedClassBPositionReport`

ドキュメントに列挙される**全型**から選択可能。省略した場合、**全型が流れる**可能性があり負荷が高いため、**明示指定を推奨**する。

### 8.4 購読の更新

- 同一 WebSocket 上で購読 JSON を**再送**すると、**新しい購読で完全に置換**される（旧条件とのマージはされない）。

---

## 9. 受信メッセージ形式（AISstream）

### 9.1 ラッパー構造（OpenAPI: `AisStreamMessage`）

受信各メッセージは、少なくとも次の 3 トップレベルキーを持つ（必須: `MessageType`, `Message`, `MetaData`）。

```json
{
  "MessageType": "PositionReport",
  "MetaData": { },
  "Message": {
    "PositionReport": { }
  }
}
```

| キー | 説明 |
|------|------|
| `MessageType` | 文字列。`FilterMessageTypes` で列挙される型名と一致。 |
| `Message` | オブジェクト。**型ごとに 1 キーのみ**（例: `PositionReport`）。値が AIS デコード済みボディ。 |
| `MetaData` | オブジェクト。スキーマ上は自由形式。**実装者は生ストリームでキー綴り（`MetaData` / `Metadata`）と中身を確認**すること（ドキュメント HTML と OpenAPI で表記が揺れる可能性）。補助的な受信時刻・位置が含まれる場合は**位置・時刻のフォールバック**に使ってよい。 |

**パース手順（擬似）**

1. `MessageType` を読む。  
2. `Message` 内の**同名キー**（または `MessageType` に対応するキー）から本体オブジェクトを取得。  
3. 本体から `UserID` を取り、**6.4 のルールで 9 桁 MMSI 文字列**に正規化し、状態テーブルのキーとする。

### 9.1.1 `MetaData` の形（実測メモ）

リポジトリ内 **`sample_aisstream.jsonl`**（1 行目、`received_at_utc` ≈ `2026-03-29T08:25:13Z`）の **`ShipStaticData`** において、トップレベルキーは **`MetaData`**（**`Metadata` ではない**）であった。

| フィールド | 実測の型・例 | 備考 |
|------------|----------------|------|
| `MMSI` | integer（例: `566711000`） | 本体 `UserID` と一致することがある。 |
| `MMSI_String` | **integer**（例: `566711000`） | 名前は `_String` だが、当該サンプルでは **JSON 上は数値**。実装では **`str` 化して 9 桁ゼロ埋め**を推奨。 |
| `ShipName` | string（例: `"VALLIANZ STEADFAST  "`） | 末尾スペース等あり得る。trim 任意。 |
| `latitude` | number（度、例: `25.55122`） | **小文字** `latitude`。静的情報メッセージでも**最終既知位置**として付与される例あり。 |
| `longitude` | number（度、例: `55.38136`） | **小文字** `longitude`。 |
| `time_utc` | string | 例: `"2026-03-29 08:25:00.995828264 +0000 UTC"`。**ナノ秒級＋固定文言 `UTC` の非 ISO8601**。パースする場合は言語標準の strptime では不足しがちなため、**正規表現で先頭〜秒または小数まで抜き出す**、または **受信時刻（9.5 の優先度 1）を正**とし `time_utc` は検証用に留める、が安全。 |

**実測 `MetaData` の抜粋（整形）**

```json
"MetaData": {
  "MMSI": 566711000,
  "MMSI_String": 566711000,
  "ShipName": "VALLIANZ STEADFAST  ",
  "latitude": 25.55122,
  "longitude": 55.38136,
  "time_utc": "2026-03-29 08:25:00.995828264 +0000 UTC"
}
```

**実装指針**

- **位置**: 本体に緯度経度があるメッセージ型では**本体を優先**。`MetaData.latitude` / `longitude` はフォールバックまたはクロスチェック用。  
- **時刻**: 9.5 のとおり**受信時刻を主軸**とし、`time_utc` は利用する場合のみ**専用パーサ**で扱う。  
- **メッセージ型差**: `PositionReport` 等で `MetaData` のキー集合が異なる可能性があるため、**存在チェック後に参照**すること。

### 9.2 `PositionReport`（Message types 1/2/3 系）— フィールド対応

`type-definition.yaml` の `PositionReport` に基づく。本仕様で用いる主な対応は次のとおり。

| AISstream フィールド | 型（OpenAPI） | 本仕様での用途 |
|----------------------|---------------|----------------|
| `UserID` | integer | MMSI（6.4 で 9 桁文字列化） |
| `Latitude` | double | 度。AOI 判定。`Valid == false` のときは**位置を使わない**（下記）。 |
| `Longitude` | double | 度。AOI 判定。 |
| `Sog` | double | 速力（ノット）。**AIS 上 102.3 は「データなし」**の慣例があるため、実装では **≥ 102.3 を欠損**として扱ってよい。 |
| `Cog` | double | 針路（度）。任意記録。 |
| `NavigationalStatus` | integer | 0–15（11.5 参照）。滞在判定 C4。 |
| `TrueHeading` | integer | 511 = 利用不可の慣例。 |
| `Valid` | boolean | **false の場合、位置報告が無効**とみなし、**AOI・滞在更新に使わない**（スキップ）。 |
| `Timestamp` | integer | **AIS の「分内秒」0–59**。完全な UTC 日時**ではない**（9.5 参照）。 |

### 9.3 `ShipStaticData`（Message type 5）— フィールド対応

| AISstream フィールド | 型 | 本仕様での用途 |
|----------------------|-----|----------------|
| `UserID` | integer | MMSI |
| `ImoNumber` | integer | IMO。**0 の場合は「IMO なし」**とみなし、マスタ突合・出力の IMO は空またはマスタのみ。正規化時は 7 桁文字列（先頭ゼロ埋め）。 |
| `Type` | integer | **Ship and Cargo Type**。タンカー判定（10 章）。**0 は未設定**。 |
| `Name` | string | 船名（20 文字までの慣例。`@` パディング除去等は実装任せ）。 |
| `Destination` | string | AIS 上の目的地テキスト（任意・信頼性は低い）。本仕様の滞在判定の**必須入力ではない**。 |
| `Valid` | boolean | false なら当メッセージを静的情報更新に**使わない**。 |

### 9.4 `ExtendedClassBPositionReport`（任意購読時）

Class B 向け。`UserID`, `Latitude`, `Longitude`, `Sog` に加え **`Type`（船種）**・`Name` 等を含む。`ShipStaticData` 未到着でも **船種のみ早期判定**したい場合に利用可能。`Valid` および位置の扱いは 9.2 と同様。

### 9.5 時刻の扱い（滞在時間の基準）— 必須方針

AIS 位置レポートの `Timestamp` は **「現在の分における秒」0–59** であり、**日付・時・分を単独では表さない**。

| 優先度 | 時刻ソース | 用途 |
|--------|------------|------|
| **1（推奨）** | **WebSocket メッセージを受信した時刻**（クライアントの **UTC** クロック） | `last_report_utc`、滞在時間 `T_dwell` / `T_gap` / `T_stale` の計算の**主時刻軸**。 |
| 2 | `MetaData.time_utc` 等（**9.1.1**。形式は非標準のため専用パースが必要） | 受信時刻の代替・検証用。 |
| 3 | `Timestamp`（0–59） | **単体では絶対時刻にしない**。受信時刻と組み合わせて「同一分内の順序」推定に使う程度に留める。 |

**要件**: 同一 MMSI に対し、**すべての滞在計算で同じ時刻ソース優先ルール**を適用すること。

### 9.6 マスタ IMO との突合

| 条件 | 動作 |
|------|------|
| `ShipStaticData.ImoNumber` > 0 | 7 桁文字列化し `imo_resolved` に保存。マスタの `imo` と**文字列一致**でウォッチリスト「該当」とみなす。 |
| `ImoNumber == 0` | マスタ IMO 突合は**行わない**（MMSI のみで追跡）。 |
| マスタに同一 MMSI・別 IMO があった場合 | **静的情報の ImoNumber を優先**し、マスタを上書きするか警告ログを出すかは実装方針（設定化推奨）。 |

---

## 10. タンカー判定仕様

### 10.1 判定の正とするデータ

- **AIS 静的情報**の **Ship and Cargo Type**（船種コード）を正とする。
- 静的情報を**一度も受信していない MMSI** は、タンカー未確定とする。

### 10.2 タンカー系コード範囲（デフォルト）

国際的な AIS の慣例として、**80〜89** をタンカー系とする（例: 80 Tanker all ships of this type、81–89 は危険物区分等）。

| パラメータ | デフォルト | 説明 |
|------------|------------|------|
| `tanker_type_min` | `80` | タンカー判定の下限（含む） |
| `tanker_type_max` | `89` | タンカー判定の上限（含む） |

- コードが **0（未設定）** や欠損の場合は **タンカー不明** とし、**「滞在タンカー」確定リストには含めない**（誤検知抑制）。

### 10.2.1 `ExtendedClassBPositionReport.Type` の扱い

`FilterMessageTypes` に `ExtendedClassBPositionReport` を含める場合、`Message` 内の `Type` を **静的情報に先立つ暫定船種**として扱ってよい。`ShipStaticData` 到着後は **`ShipStaticData.Type` で上書き**する（静的情報を正とする）。

### 10.2.2 代表的な AIS 船種コード（参考）

| 範囲 | 概要（参考） |
|------|----------------|
| 80–89 | タンカー系（本仕様のデフォルト対象） |
| 70–79 | 貨物船系 |
| 30–39 | 漁船 |
| 50–59 | 旅客船 |

詳細は ITU-R M.1371 付属・各種 AIS ガイドを参照。境界付近のコードは運用で `tanker_type_min` / `max` を調整する。

### 10.3 緩和オプション（任意）

- `include_position_only_non_tanker_confirmed`: `false`（デフォルト）  
  - `true` の場合、タンカー未確定でも位置のみで別レポートを出す等の**デバッグ用出力**を許容する。本番の「滞在タンカー」定義とは分離すること。

---

## 11. 「ホルムズにとどまっている」判定仕様

### 11.1 入力

- **時刻**: **9.5 に従い、原則 WebSocket 受信時刻（UTC）**をイベント時刻とする。AIS フィールド `Timestamp`（0–59）単体では絶対時刻にしない。
- **緯度・経度**: `PositionReport`（または購読に含めた Class B レポート）。`Valid == false` は除外。
- **SOG**: `Sog`。欠損慣例 **≥ 102.3** は 14.1 の `sog_not_available_min` に従う。
- **Navigation Status**: `NavigationalStatus`（11.5）。Class B で欠損する場合は SOG のみで C4。

### 11.2 状態変数（MMSI ごと）— 論理名

11.6 のアルゴリズムと対応させる。

| 変数 | 説明 |
|------|------|
| `last_inside_aoi` | 直近の**有効**位置サンプルが AOI 内だったか（デバッグ・UI 用）。 |
| `segment_start_utc` | 現在の「AOI 内滞在区間」の開始時刻（`T_gap` で途切れを接続した後の実効開始）。旧称 `dwell_candidate_start_utc` と同義。 |
| `last_report_utc` | 最終**有効**位置レポートの受信時刻（= 11.6 の `last_any_utc`）。 |
| `tanker_confirmed` | 静的情報（または暫定 `ExtendedClassBPositionReport.Type`）でタンカーと確定したか。 |
| `imo_resolved` | 静的情報から取得した IMO（7 桁文字列）。 |
| `dwell_met_since_utc` | 初回に C1–C4 をすべて満たした時刻（任意・履歴用）。 |

### 11.3 閾値パラメータ

| パラメータ | 例 | 説明 |
|------------|-----|------|
| `T_dwell` | `24h` | AOI 内に**連続**して滞在しているとみなす最短時間。 |
| `Sog_max_still` | `3.0` kn | 「ほぼ停止」扱いの SOG 上限。 |
| `T_gap` | `15min` | AOI 外に出た／欠測がこの時間以内なら、**同一滞在のギャップ**として許容する（任意）。 |
| `T_stale` | `6h` | 最終レポートからこの時間超で**監視不能（stale）**とする。 |

上記は**初期例**。運用で変更する。

### 11.4 滞在条件（すべて満たすこと）

対象 MMSI について、次を**すべて**満たすとき **「滞在タンカー（dwell_satisfied）」** とする。

| ID | 条件 |
|----|------|
| C1 | **タンカー確定**: 10 章に従い `tanker_confirmed == true`。 |
| C2 | **位置**: 最新（または評価時点）の位置が **AOI 内**。 |
| C3 | **継続時間**: `segment_start_utc` から見て **AOI 内で実効的に連続した区間**（11.6 の `T_gap` ルール込み）が **`T_dwell` 以上**。評価式: `(t_now - segment_start_utc) ≥ T_dwell`。 |
| C4 | **低速または停泊**: 次のいずれか  
  - 当該区間における **最大 SOG ≤ `Sog_max_still`**、または  
  - **Navigation Status** が「停泊・係留」等に該当（実装でコード表を定数化）。 |

### 11.5 Navigation Status（`NavigationalStatus` 整数コード）

`PositionReport.NavigationalStatus` は **ITU-R M.1371** で定義される 0–15（拡張実装では追加値の可能性あり）。本仕様では次を**デフォルト**とする。

| コード | 意味（英） | C4「停泊・係留」扱い（デフォルト） |
|--------|------------|-------------------------------------|
| 0 | Under way using engine | ×（航行中） |
| 1 | At anchor | ○ |
| 2 | Not under command | △（設定 `nav_status_treat_not_under_command_as_still` で選択） |
| 3 | Restricted manoeuverability | △ |
| 4 | Constrained by her draught | △ |
| 5 | Moored | ○ |
| 6 | Aground | ○ |
| 7 | Engaged in Fishing | ×（本仕様では滞留扱いにしない） |
| 8 | Under way sailing | × |
| 9–14 | Reserved / 将来用 | ×（未知は × 推奨） |
| 15 | Not defined (= default) | × |

- **C4 判定**: `NavigationalStatus ∈ nav_still_codes`（デフォルト: `{1, 5, 6}` に **2,3,4 を加えるか**は設定 `nav_still_codes` で上書き）**または** SOG 条件を満たすこと。  
- Class B 位置レポートに `NavigationalStatus` が無い場合は **SOG のみ**で C4 を評価する。

### 11.6 ギャップ許容・状態更新（アルゴリズム詳細）

**入力イベント**: 各 `PositionReport`（または同等）処理時に、**受信時刻 `t_now`（UTC）**、位置、`inside_aoi`（bool）、`sog_valid`、`nav_status` を確定する。

**変数（MMSI ごと）**

| 変数 | 初期値 | 説明 |
|------|--------|------|
| `state` | `NO_DATA` | `NO_DATA` / `OUTSIDE` / `INSIDE_ACCUMULATING` / `DWELL_MET` |
| `segment_start_utc` | null | 現在の「AOI 内連続区間」の開始時刻（ギャップ許容で繋いだ結果）。 |
| `last_inside_utc` | null | 直近で AOI 内だった受信時刻。 |
| `last_any_utc` | null | 直近の有効位置レポート受信時刻。 |
| `last_outside_utc` | null | 直近で AOI 外だった受信時刻（ギャップ用）。 |

**更新手順（各メッセージ）**

1. `Valid == false` または位置欠損 → **何も更新せず**終了（`last_any_utc` も更新しない）。  
2. `t_now` で `last_any_utc = t_now`。`T_stale` は `now - last_any_utc` で評価（バッチ評価時は `now` を明示）。  
3. `inside_aoi` を計算。  
4. **AOI 内**の場合:  
   - `last_inside_utc = t_now`。  
   - `state == OUTSIDE` または `NO_DATA` から入った場合: `segment_start_utc = t_now`（新規区間）。  
   - 直前が AOI 外で `t_now - last_outside_utc ≤ T_gap` の場合: **区間継続**（`segment_start_utc` は維持）。  
   - `tanker_confirmed` かつ `(t_now - segment_start_utc) ≥ T_dwell` かつ C4 を満たすなら `state = DWELL_MET`。  
5. **AOI 外**の場合:  
   - `last_outside_utc = t_now`。  
   - `t_now - last_inside_utc > T_gap`（直近 AOI 内からの経過で判断する版）または **即時リセット**を選ぶか:  
     - **推奨**: AOI 外になった時点で `pending_reset_after_gap` を立て、`T_gap` 経過後も AOI 外なら `segment_start_utc = null`, `state = OUTSIDE`。  
   - 簡易版: AOI 外で `T_gap` 超過で `segment_start_utc = null`。  
6. **`T_stale`**: `now - last_any_utc > T_stale` のとき `dwell_status = stale`（位置は最後の既知を保持可）。

**簡易版疑似コード（ギャップ即時リセット）**

```
on valid position (t_now, inside, sog, nav):
  last_any = t_now
  if inside:
    if was_outside and (t_now - last_outside) <= T_gap:
      # 継続: segment_start 不変
      pass
    elif was_outside and (t_now - last_outside) > T_gap:
      segment_start = t_now
    elif first_inside:
      segment_start = t_now
    last_inside = t_now
  else:
    last_outside = t_now
    if (t_now - last_inside) > T_gap:
      segment_start = null
```

実装は上記いずれかに統一し、**テストで `T_gap` 跨ぎの挙動を固定**すること。

### 11.6.1 欠測（メッセージが来ない）の扱い

- 欠測は **AIS では常態**。`last_any_utc` が `T_stale` を超えたら **stale**。  
- stale 中は **dwell を新たに認定しない**が、過去に `DWELL_MET` だったレコードを**どれだけ保持するか**は `T_output_retention`（任意パラメータ）で定義する。

### 11.7 ステータス列挙（出力用）

| 値 | 意味 |
|----|------|
| `outside_aoi` | 現在 AOI 外（または未受信） |
| `inside_aoi_short` | AOI 内だが `T_dwell` 未満 |
| `dwell_satisfied` | 11.4 を満たす |
| `stale` | `T_stale` 超で有効レポートなし |
| `tanker_unknown` | 静的情報不足でタンカー未確定 |

---

## 12. 動作モード

### 12.1 探索モード（discovery）

| 項目 | 内容 |
|------|------|
| `FiltersShipMMSI` | **指定しない** |
| `BoundingBoxes` | 広域 AOI 推奨 |
| 処理 | 受信 MMSI のうち、静的情報で **タンカー** かつ **滞在条件** を満たすものを列挙。マスタ IMO との突合は **IMO が取れた場合のみ**。 |

### 12.2 ウォッチリストモード（watchlist）

| 項目 | 内容 |
|------|------|
| `FiltersShipMMSI` | マスタから最大 **50** MMSI を選択して指定 |
| 処理 | 指定 MMSI のみ受信が来るため負荷低。51 隻超は**複数接続に分割**または探索モードへフォールバック。 |

### 12.2.1 ウォッチリスト >50 隻時の分割戦略（必須設計）

AISstream は **1 接続あたり `FiltersShipMMSI` 最大 50**。次のいずれかを仕様として採用する。

| 方式 | 内容 | 注意 |
|------|------|------|
| **A. 複数 WebSocket** | MMSI リストを **50 件ずつのチャンク**に分割し、**チャンクごとに別接続**を張る。 | **同一 API キーで同時接続数が増える** → スロットリング・切断リスク。接続数上限は運用で決め、**最大 N 接続**を設定化する。 |
| **B. 単接続ローテーション** | 50 件ずつ `FiltersShipMMSI` を付け替え（8.4 の購読更新）。各バッチを `T_rotate` 秒監視後、次の 50 件へ。 | バッチ外の船は**その間スナップショットされない**。`T_rotate` は `T_stale`・`T_dwell` より十分短いか、要件を下げる。 |
| **C. 探索モードへフォールバック** | MMSI フィルタなし＋クライアント側でマスタ MMSI 集合にフィルタ。 | メッセージ量は増えるが実装が単純。 |

**推奨**: 本番は **A または C**。B はデモ・低頻度監視向け。

### 12.3 モード選択の推奨

- マスタが **50 MMSI 以下**で揃っている → ウォッチリストモード。
- **IMO のみ**が大量にある → 先に MMSI 解決バッチを回すか、探索モード＋IMO 突合。

---

## 13. 出力仕様

### 13.1 レコード単位（推奨フィールド）

| フィールド | 型 | 説明 |
|------------|-----|------|
| `mmsi` | 文字列 | 9 桁 |
| `imo` | 文字列 | マスタまたは静的情報 |
| `is_tanker` | 真偽 | 10 章の判定結果 |
| `dwell_status` | 文字列 | 11.7 のいずれか |
| `dwell_started_at_utc` | 日時 | 現在の滞在区間の開始（推定） |
| `last_position_utc` | 日時 | 最終位置報告時刻 |
| `lat` | 実数 | 度 |
| `lon` | 実数 | 度 |
| `sog` | 実数 | kn |
| `nav_status` | 整数または文字列 | 実装に合わせる |

### 13.2 シリアライズ形式

- JSON Lines、JSON 配列、RDB 行など**実装任せ**。
- ログに IMO／MMSI を出す場合、**個人情報・運航秘匿**に配慮する運用は利用者責任とする。

---

## 14. 非機能要件

| 項目 | 要件 |
|------|------|
| 機密情報 | API Key は環境変数またはシークレット管理。リポジトリに含めない。 |
| 再接続 | AISstream 切断時は**指数バックオフ**で再接続。推奨デフォルト: **初期待機 `5s`**、**倍率 `2`**、**最大待機 `300s`**、**無限リトライ**または **`max_retries` 設定**（例: 空なら無制限）。切断理由が認証失敗の場合は**バックオフせず終了**しログに出す。 |
| 受信ループ | メインスレッドをブロックしない。**非同期 I/O** または専用スレッドで、**受信と同じスレッドで重い DB 書き込みをしない**（キューで切り離し推奨）。 |
| 時刻 | 内部は **UTC** で統一。 |
| 観測欠損 | AIS は**常に欠損しうる**。「いない」ことと「見えていない」ことを混同しない（stale 扱い）。 |
| 利用条件 | AISstream の利用規約・β条件に従う。データの**再配布可否**は別途プロバイダ確認。 |
| スプーフィング | MMSI／位置の偽装検知は**本仕様のスコープ外**。 |
| ログ | 本番では **API Key 全文をログに出さない**（マスク）。 |

### 14.1 設定ファイル例（YAML 論理・必須ではない）

実装の参考として、次のキーを推奨する。

```yaml
aisstream:
  api_key_env: "AISSTREAM_API_KEY"
  bounding_boxes: [[[27.40, 55.30], [24.60, 59.20]]]
  filter_message_types: ["PositionReport", "ShipStaticData"]
  # watchlist_mmsi: []  # 最大50、省略で探索モード

dwell:
  T_dwell: "24h"
  T_gap: "15m"
  T_stale: "6h"
  Sog_max_still: 3.0
  sog_not_available_min: 102.3   # AIS SOG 欠損慣例
  nav_still_codes: [1, 5, 6]

tanker:
  type_min: 80
  type_max: 89

reconnect:
  initial_seconds: 5
  multiplier: 2
  max_seconds: 300
```

---

## 15. テスト観点（受け入れの目安）

1. 購読 JSON 送信が **3 秒以内**であること。
2. 既知 MMSI のモック位置で、AOI 内／外の判定が矩形定義と一致すること。
3. 船種コード **80** の静的情報後に、`is_tanker == true` となること。
4. 船種未受信時は `tanker_unknown` であり、`dwell_satisfied` にならないこと。
5. AOI 内を `T_dwell` 以上継続した場合のみ `dwell_satisfied` となること（モック時刻で可）。
6. `FiltersShipMMSI` に **51 件**渡したとき、クライアント側で拒否または分割すること（AISstream 仕様違反を防ぐ）。
7. `Valid: false` の位置レポートで AOI 状態が更新されないこと。
8. `Sog: 102.3`（または ≥ 102.3）を欠損扱いにした場合、C4 が NAV のみで評価されること。
9. `UserID` 整数 `1234567` が MMSI 文字列 `"001234567"` に正規化されること（先頭ゼロ保持）。
10. `ShipStaticData.ImoNumber: 0` の船が `imo_resolved` を持たないこと。
11. 再接続後、**同一購読 JSON を 3 秒以内**に再送すること。

---

## 16. 改訂履歴

| 版 | 日付 | 内容 |
|----|------|------|
| 0.1 | 2026-03-29 | 初版ドラフト作成 |
| 0.2 | 2026-03-29 | OpenAPI 準拠の購読／受信フィールド、時刻方針、Nav Status 表、ギャップアルゴリズム、>50 MMSI 戦略、IMO/MMSI 正規化、非機能・テスト拡充 |
| 0.3 | 2026-03-29 | `BoundingBoxes` の JSON 入れ子を訂正（1 矩形は **3 重** `[[[lat,lon],[lat,lon]]]`。4 重はサーバが malformed と返す） |
| 0.4 | 2026-03-29 | §9.1.1 `MetaData` 実測メモ（`sample_aisstream.jsonl` / `ShipStaticData`）。§9.5 時刻優先度 2 の参照先を明確化 |

---

## 付録 A: AISstream ドキュメント記載の `FilterMessageTypes` 一覧（参考）

ドキュメントに列挙される型名（抜粋・改行は読みやすさのため）:

`PositionReport`, `UnknownMessage`, `AddressedSafetyMessage`, `AddressedBinaryMessage`, `AidsToNavigationReport`, `AssignedModeCommand`, `BaseStationReport`, `BinaryAcknowledge`, `BinaryBroadcastMessage`, `ChannelManagement`, `CoordinatedUTCInquiry`, `DataLinkManagementMessage`, `DataLinkManagementMessageData`, `ExtendedClassBPositionReport`, `GroupAssignmentCommand`, `GnssBroadcastBinaryMessage`, `Interrogation`, `LongRangeAisBroadcastMessage`, `MultiSlotBinaryMessage`, `SafetyBroadcastMessage`, `ShipStaticData`, `SingleSlotBinaryMessage`, `StandardClassBPositionReport`, `StandardSearchAndRescueAircraftReport`, `StaticDataReport`

※ 最新は公式ドキュメントおよび `type-definition.yaml` を優先すること。

---

*本ドキュメントは AISstream の公開情報に基づく。プロダクト変更時は仕様を追随すること。*
