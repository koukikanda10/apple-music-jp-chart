#!/usr/bin/env python3
"""直近2日のスナップショットを比べ、同じ曲なのに track_id が変わっていないか検査する。

name + artist_name が同一なのに track_id が異なる組を「不安定」とみなし、
1件でも見つかったら exit 1 で終了する。日次ワークフローの取得後ステップから呼ぶ想定。

比較対象は data/parsed/*.csv に入っている chart_date のうち新しい2つ。
月をまたぐと月次ファイルが分かれるため、CSVは全月ぶんまとめて読む。
1日分しか無い場合は比較しようがないので、スキップして exit 0。
"""

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSED_DIR = os.path.join(REPO_ROOT, "data", "parsed")

EXIT_OK = 0
EXIT_UNSTABLE = 1
EXIT_ERROR = 2


def load_rows() -> list[dict]:
    """data/parsed 配下の月次CSVを全部読んで1つのリストにする。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(PARSED_DIR, "*.csv"))):
        # 書き出し側が BOM 付きUTF-8なので utf-8-sig で開く
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def pick_dates(rows: list[dict], prev_date: str | None, curr_date: str | None):
    """比較する2日を決める。指定が無ければ新しい方から2つ。足りなければ None。"""
    dates = sorted({r.get("chart_date", "") for r in rows} - {""})
    if prev_date and curr_date:
        for date in (prev_date, curr_date):
            if date not in dates:
                raise SystemExit(f"エラー: chart_date={date} のデータがありません")
        return prev_date, curr_date
    if len(dates) < 2:
        return None
    return dates[-2], dates[-1]


def index_by_song(rows: list[dict], chart_date: str) -> dict[tuple[str, str], dict]:
    """(name, artist_name) -> {track_id: 最小rank} に畳む。

    同じ曲が同日に2行出ることは通常無いが、出た場合も潰さず全IDを残す。
    """
    index: dict[tuple[str, str], dict] = defaultdict(dict)
    for row in rows:
        if row.get("chart_date") != chart_date:
            continue
        key = (row.get("name", "").strip(), row.get("artist_name", "").strip())
        track_id = row.get("track_id", "").strip()
        rank = int(row.get("rank", 0) or 0)
        ranks = index[key]
        if track_id not in ranks or rank < ranks[track_id]:
            ranks[track_id] = rank
    return index


def find_unstable(previous: dict, current: dict) -> list[dict]:
    """両日に登場する曲のうち、track_id の集合が一致しないものを拾う。"""
    findings = []
    for key in sorted(previous.keys() & current.keys()):
        prev_ids = previous[key]
        curr_ids = current[key]
        if set(prev_ids) == set(curr_ids):
            continue
        name, artist = key
        findings.append(
            {
                "name": name,
                "artist_name": artist,
                "prev_ids": sorted(prev_ids),
                "curr_ids": sorted(curr_ids),
                "prev_rank": min(prev_ids.values()),
                "curr_rank": min(curr_ids.values()),
            }
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prev-date", help="比較元の chart_date (既定: 最新から2番目)")
    parser.add_argument("--date", dest="curr_date", help="比較先の chart_date (既定: 最新)")
    args = parser.parse_args()

    rows = load_rows()
    if not rows:
        print(f"エラー: {os.path.relpath(PARSED_DIR, REPO_ROOT)} にデータがありません", file=sys.stderr)
        return EXIT_ERROR

    selected = pick_dates(rows, args.prev_date, args.curr_date)
    if selected is None:
        print("データが1日分しかないため、ID比較をスキップします。")
        return EXIT_OK

    prev_date, curr_date = selected
    previous = index_by_song(rows, prev_date)
    current = index_by_song(rows, curr_date)
    print(f"比較: {prev_date} ({len(previous)}曲) → {curr_date} ({len(current)}曲)")

    findings = find_unstable(previous, current)
    common = len(previous.keys() & current.keys())
    if not findings:
        print(f"両日に登場した {common}曲 の track_id はすべて一致しています。")
        return EXIT_OK

    print(
        f"\n異常: 同一の name + artist_name で track_id が変わった曲が {len(findings)}件 あります",
        file=sys.stderr,
    )
    for finding in findings:
        print(
            f"  - {finding['name']} / {finding['artist_name']}\n"
            f"      {prev_date} rank={finding['prev_rank']:>3} id={','.join(finding['prev_ids'])}\n"
            f"      {curr_date} rank={finding['curr_rank']:>3} id={','.join(finding['curr_ids'])}",
            file=sys.stderr,
        )
    return EXIT_UNSTABLE


if __name__ == "__main__":
    sys.exit(main())
