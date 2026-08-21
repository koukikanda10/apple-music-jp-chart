#!/usr/bin/env python3
"""スナップショットから曲ごとの指標を導出する。

data/parsed/*.csv をメモリ上の SQLite に読み込み、指標はすべて SQL で計算する。
指標をカラムとして保存することはしない。集計方法を変えたくなったらクエリを直せばよく、
過去データの作り直しは要らない。

  first_charted()  初回ランクイン日
  peak_position()  最高順位と、それを記録した日付
  total_days()     通算在籍日数
  chart_runs()     在籍区間の配列（一定日数以上の空白で分割）
  summary()        上記をまとめて1件返す

データが無い曲・1日しか無い場合も例外にはせず、None / 0 / [] を返す。
"""

import argparse
import csv
import glob
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSED_DIR = os.path.join(REPO_ROOT, "data", "parsed")

# 在籍区間を分ける空白の長さ。「その曲がチャート外だった観測日がこの日数以上続いたら別区間」
# と読む。7 なら 6日空いて復帰は同一区間、7日空けば別区間。
SESSION_GAP_DAYS = 7

# CSV の列。空のデータベースでもクエリが通るよう、ヘッダからではなくここから作る
COLUMNS = [
    ("chart_date", "TEXT"),
    ("rank", "INTEGER"),
    ("country", "TEXT"),
    ("track_id", "TEXT"),
    ("name", "TEXT"),
    ("artist_name", "TEXT"),
    ("artist_id", "TEXT"),
    ("release_date", "TEXT"),
    ("kind", "TEXT"),
    ("genre_ids", "TEXT"),
    ("genre_names", "TEXT"),
    ("url", "TEXT"),
    ("artwork_url", "TEXT"),
    ("fetched_at", "TEXT"),
]

# 観測日（スナップショットが1件でもある日）に通し番号を振る。
# 区間の空白を「暦の日数」ではなく「観測日の数」で測るために使う。
# 取得が飛んだ日は分母から外れるので、収集側の穴で区間が誤って分割されない。
OBSERVED_DAYS = """
    WITH observed AS (SELECT DISTINCT chart_date FROM snapshots)
    SELECT chart_date, ROW_NUMBER() OVER (ORDER BY chart_date) AS day_index
    FROM observed
"""


def open_snapshots(csv_paths: list[str] | None = None) -> sqlite3.Connection:
    """CSV を読み込んだメモリ上のデータベースを返す。CSV が1つも無くても空の表を作る。"""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    definition = ", ".join(f"{name} {sql_type}" for name, sql_type in COLUMNS)
    connection.execute(f"CREATE TABLE snapshots ({definition})")

    if csv_paths is None:
        csv_paths = sorted(glob.glob(os.path.join(PARSED_DIR, "*.csv")))

    names = [name for name, _ in COLUMNS]
    placeholders = ", ".join("?" for _ in names)
    for path in csv_paths:
        # 書き出し側が BOM 付きUTF-8なので utf-8-sig で開く
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = [
                tuple(row.get(name) or None for name in names) for row in csv.DictReader(handle)
            ]
        connection.executemany(
            f"INSERT INTO snapshots ({', '.join(names)}) VALUES ({placeholders})", rows
        )
    connection.commit()
    return connection


def first_charted(connection: sqlite3.Connection, track_id: str) -> str | None:
    """初回ランクイン日。一度も入っていなければ None。"""
    row = connection.execute(
        "SELECT MIN(chart_date) AS first_date FROM snapshots WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    return row["first_date"] if row else None


def peak_position(connection: sqlite3.Connection, track_id: str) -> dict | None:
    """最高順位と、それを記録した日付。同じ順位が複数日あれば最初の日を date に置く。"""
    row = connection.execute(
        """
        SELECT rank AS peak_rank,
               MIN(chart_date) AS date,
               COUNT(*) AS days_at_peak
        FROM snapshots
        WHERE track_id = ?
          AND rank = (SELECT MIN(rank) FROM snapshots WHERE track_id = ?)
        """,
        (track_id, track_id),
    ).fetchone()
    # 該当なしでも集約関数は1行返すので、中身が NULL かどうかで判定する
    if row is None or row["peak_rank"] is None:
        return None
    return {
        "rank": row["peak_rank"],
        "date": row["date"],
        "days_at_peak": row["days_at_peak"],
    }


def total_days(connection: sqlite3.Connection, track_id: str) -> int:
    """通算在籍日数。同じ日に複数行あっても1日と数える。"""
    row = connection.execute(
        "SELECT COUNT(DISTINCT chart_date) AS days FROM snapshots WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    return row["days"] if row else 0


def chart_runs(
    connection: sqlite3.Connection, track_id: str, gap_days: int = SESSION_GAP_DAYS
) -> list[dict]:
    """在籍区間の配列。空白が gap_days 以上の観測日ぶん続いたところで区間を分ける。

    前の在籍日との観測日番号の差から1を引くと「間に挟まった不在の観測日数」になる。
    それが gap_days 以上なら新しい区間の始まりとして印を付け、印の累計を区間IDにする。
    """
    rows = connection.execute(
        f"""
        WITH observed_days AS ({OBSERVED_DAYS}),
        charted AS (
            SELECT DISTINCT o.chart_date, o.day_index
            FROM snapshots AS s
            JOIN observed_days AS o ON o.chart_date = s.chart_date
            WHERE s.track_id = ?
        ),
        marked AS (
            SELECT chart_date,
                   CASE
                       WHEN LAG(day_index) OVER (ORDER BY day_index) IS NULL THEN 1
                       WHEN day_index - LAG(day_index) OVER (ORDER BY day_index) - 1 >= ? THEN 1
                       ELSE 0
                   END AS starts_run
            FROM charted
        ),
        numbered AS (
            SELECT chart_date,
                   SUM(starts_run) OVER (ORDER BY chart_date ROWS UNBOUNDED PRECEDING) AS run_id
            FROM marked
        )
        SELECT MIN(chart_date) AS start_date,
               MAX(chart_date) AS end_date,
               COUNT(*) AS days
        FROM numbered
        GROUP BY run_id
        ORDER BY start_date
        """,
        (track_id, gap_days),
    ).fetchall()
    return [dict(row) for row in rows]


def summary(
    connection: sqlite3.Connection, track_id: str, gap_days: int = SESSION_GAP_DAYS
) -> dict:
    """1曲ぶんの指標をまとめて返す。データが無い曲でも例外にはしない。"""
    label = connection.execute(
        """
        SELECT name, artist_name FROM snapshots
        WHERE track_id = ? ORDER BY chart_date DESC LIMIT 1
        """,
        (track_id,),
    ).fetchone()
    return {
        "track_id": track_id,
        "name": label["name"] if label else None,
        "artist_name": label["artist_name"] if label else None,
        "first_charted": first_charted(connection, track_id),
        "peak": peak_position(connection, track_id),
        "total_days": total_days(connection, track_id),
        "runs": chart_runs(connection, track_id, gap_days),
    }


def find_tracks(connection: sqlite3.Connection, keyword: str) -> list[dict]:
    """曲名かアーティスト名の部分一致で track_id を引く。track_id は目で覚えられないため。"""
    pattern = f"%{keyword}%"
    rows = connection.execute(
        """
        SELECT track_id, name, artist_name, COUNT(DISTINCT chart_date) AS days
        FROM snapshots
        WHERE name LIKE ? OR artist_name LIKE ?
        GROUP BY track_id, name, artist_name
        ORDER BY days DESC, name
        """,
        (pattern, pattern),
    ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="track_id、または曲名/アーティスト名の一部")
    parser.add_argument(
        "--gap-days",
        type=int,
        default=SESSION_GAP_DAYS,
        help=f"在籍区間を分ける空白の日数 (既定: {SESSION_GAP_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args()

    connection = open_snapshots()
    observed = connection.execute(
        "SELECT COUNT(DISTINCT chart_date) AS days FROM snapshots"
    ).fetchone()["days"]

    if not args.query:
        print(f"観測日数: {observed}日")
        print("使い方: python scripts/song_stats.py <track_id | 曲名の一部>")
        return 0

    matches = find_tracks(connection, args.query)
    if not matches:
        # キーワードで引けなければ track_id そのものとみなす
        matches = [{"track_id": args.query}]

    results = [summary(connection, m["track_id"], args.gap_days) for m in matches]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print(f"観測日数: {observed}日 / 一致: {len(results)}件")
    for result in results:
        title = result["name"] or "(データなし)"
        print(f"\n{title} / {result['artist_name'] or '-'}  [{result['track_id']}]")
        print(f"  初回ランクイン : {result['first_charted'] or '-'}")
        peak = result["peak"]
        print(
            f"  最高順位       : {peak['rank']}位 ({peak['date']}, 計{peak['days_at_peak']}日)"
            if peak
            else "  最高順位       : -"
        )
        print(f"  通算在籍日数   : {result['total_days']}日")
        if not result["runs"]:
            print("  在籍区間       : -")
        for run in result["runs"]:
            print(f"  在籍区間       : {run['start_date']} 〜 {run['end_date']} ({run['days']}日)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
