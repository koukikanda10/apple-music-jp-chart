# apple-music-jp-chart

Apple Marketing Tools RSS から、日本の most-played 楽曲チャート上位100曲を毎日取得して記録する。

## データ

| パス | 内容 |
|---|---|
| `data/raw/YYYY/MM/YYYY-MM-DD.json` | APIレスポンスの生JSON全文。一切加工していない |
| `data/parsed/YYYY-MM.csv` | パース済みテーブル（月次）。Excelでそのまま開けるようBOM付きUTF-8 |
| `data/parsed/YYYY-MM.jsonl` | 同内容を1行1レコードのJSONで |

生JSONを無加工で残しているため、パース仕様を変えたくなった場合は過去分をすべて再生成できる。

### 列

`chart_date`, `rank`, `country`, `track_id`, `name`, `artist_name`, `artist_id`,
`release_date`, `kind`, `genre_ids`, `genre_names`, `url`, `artwork_url`, `fetched_at`

- `chart_date` … 取得した日（**JST**）
- `rank` … 1〜100。APIに順位フィールドは無く、`results` の配列順から付与している
- `track_id` … APIの `id`。Apple Music のカタログ内トラックID。ストアフロントごとに異なるため `country` と組で扱う
- `genre_ids` / `genre_names` … 複数あるためパイプ区切り（例 `21|34`, `ロック|ミュージック`）

## 取得元

```
https://rss.marketingtools.apple.com/api/v2/jp/music/most-played/100/songs.json
```

- 旧ドメイン `rss.applemarketingtools.com` は301リダイレクトする
- 件数の上限は **100**。101以上を指定すると 504 / 500 が返る
- レスポンス中の `feed.updated` は**チャートの更新時刻ではなくレスポンス生成時刻**（リクエストごとに変わる）。日付は取得側で付与している

## 実行

```bash
python scripts/fetch_chart.py
```

標準ライブラリのみで動作する。同じ `chart_date` の行は置き換えられるため、同日中に何度実行しても結果は変わらない。

## 自動実行

`.github/workflows/daily.yml` が毎日 **09:00 JST**（00:00 UTC）に実行し、差分をこのリポジトリへ push する。
Actions タブの「daily-chart」から手動実行も可能。

## 注意

- GitHub の cron は数分〜十数分遅れることがあり、稀にスキップされる
- リポジトリが60日間無活動になるとスケジュールが自動停止する仕様がある
- このAPIは過去日を遡って取得できない。停止していた期間のデータは復元できない
