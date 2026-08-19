#!/usr/bin/env python3
"""Apple Marketing Tools RSS から日本の most-played 楽曲チャートを日次取得する。

保存物は2種類:
  data/raw/YYYY/MM/YYYY-MM-DD.json  APIレスポンスの生JSON全文（無加工）
  data/parsed/YYYY-MM.csv / .jsonl  パース済みテーブル（月次ファイルに追記）

同じ chart_date の行は書き換えられるので、同日中に何度実行しても結果は同じになる。
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

COUNTRY = "jp"
LIMIT = 100  # このAPIの上限。101以上は 504/500 を返す
FEED_URL = (
    f"https://rss.marketingtools.apple.com"
    f"/api/v2/{COUNTRY}/music/most-played/{LIMIT}/songs.json"
)

JST = timezone(timedelta(hours=9), "JST")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 取得失敗は一時的なことが多いので、間隔を空けて数回試す
RETRIES = 3
BACKOFF_SECONDS = [5, 15]
TIMEOUT_SECONDS = 30

FIELDS = [
    "chart_date",
    "rank",
    "country",
    "track_id",
    "name",
    "artist_name",
    "artist_id",
    "release_date",
    "kind",
    "genre_ids",
    "genre_names",
    "url",
    "artwork_url",
    "fetched_at",
]


def fetch() -> str:
    """フィードを取得して本文を文字列で返す。全試行が失敗したら例外を投げる。"""
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(
                FEED_URL,
                headers={"User-Agent": "apple-music-jp-chart/1.0 (+github actions)"},
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                body = response.read().decode("utf-8")
            print(f"取得成功 ({len(body)} bytes) attempt={attempt}")
            return body
        except (urllib.error.URLError, OSError, RuntimeError) as error:
            last_error = error
            print(f"取得失敗 attempt={attempt}/{RETRIES}: {error}", file=sys.stderr)
            if attempt < RETRIES:
                wait = BACKOFF_SECONDS[attempt - 1]
                print(f"{wait}秒待って再試行します", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"フィードを取得できませんでした: {last_error}")


def parse(body: str, chart_date: str, fetched_at: str) -> list[dict]:
    """生JSONを1曲1行のテーブルに変換する。順位はAPIに無いので配列順から付ける。"""
    document = json.loads(body)
    results = document["feed"]["results"]
    if not results:
        raise RuntimeError("results が空です")
    if len(results) != LIMIT:
        # 上位が欠けている可能性があるので気づけるようにする（失敗にはしない）
        print(f"警告: {LIMIT}件を期待しましたが {len(results)}件でした", file=sys.stderr)

    rows = []
    for rank, item in enumerate(results, start=1):
        genres = item.get("genres") or []
        rows.append(
            {
                "chart_date": chart_date,
                "rank": rank,
                "country": COUNTRY,
                "track_id": item.get("id", ""),
                "name": item.get("name", ""),
                "artist_name": item.get("artistName", ""),
                "artist_id": item.get("artistId", ""),
                "release_date": item.get("releaseDate", ""),
                "kind": item.get("kind", ""),
                "genre_ids": "|".join(g.get("genreId", "") for g in genres),
                "genre_names": "|".join(g.get("name", "") for g in genres),
                "url": item.get("url", ""),
                "artwork_url": item.get("artworkUrl100", ""),
                "fetched_at": fetched_at,
            }
        )
    return rows


def write_raw(body: str, chart_date: str) -> str:
    """生JSONを一切加工せずそのまま保存する。"""
    year, month, _ = chart_date.split("-")
    directory = os.path.join(REPO_ROOT, "data", "raw", year, month)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{chart_date}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return path


def load_existing_rows(csv_path: str) -> list[dict]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def upsert_table(rows: list[dict], chart_date: str) -> tuple[str, str]:
    """月次のCSVとJSONLを書き出す。同じ chart_date の既存行は新しい行に置き換える。"""
    month_key = chart_date[:7]
    directory = os.path.join(REPO_ROOT, "data", "parsed")
    os.makedirs(directory, exist_ok=True)
    csv_path = os.path.join(directory, f"{month_key}.csv")
    jsonl_path = os.path.join(directory, f"{month_key}.jsonl")

    kept = [r for r in load_existing_rows(csv_path) if r.get("chart_date") != chart_date]
    if len(kept) != len(load_existing_rows(csv_path)):
        print(f"{chart_date} の既存行を置き換えます")

    merged = kept + [{k: str(v) for k, v in row.items()} for row in rows]
    merged.sort(key=lambda r: (r.get("chart_date", ""), int(r.get("rank", 0))))

    # CSVはExcelで文字化けしないよう BOM 付きUTF-8で書く。
    # 改行は LF に固定する（csv の既定は CRLF で、Windows と Linux で差分が出るため）
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in merged:
            writer.writerow({field: row.get(field, "") for field in FIELDS})

    with open(jsonl_path, "w", encoding="utf-8", newline="\n") as handle:
        for row in merged:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    return csv_path, jsonl_path


def main() -> int:
    now_jst = datetime.now(JST)
    chart_date = now_jst.strftime("%Y-%m-%d")
    fetched_at = now_jst.isoformat(timespec="seconds")
    print(f"chart_date={chart_date} (JST) url={FEED_URL}")

    body = fetch()
    rows = parse(body, chart_date, fetched_at)

    raw_path = write_raw(body, chart_date)
    csv_path, jsonl_path = upsert_table(rows, chart_date)

    print(f"生JSON  : {os.path.relpath(raw_path, REPO_ROOT)}")
    print(f"CSV     : {os.path.relpath(csv_path, REPO_ROOT)}")
    print(f"JSONL   : {os.path.relpath(jsonl_path, REPO_ROOT)}")
    print(f"{len(rows)}件を保存しました。1位: {rows[0]['name']} / {rows[0]['artist_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
