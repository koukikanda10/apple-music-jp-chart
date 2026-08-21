#!/usr/bin/env python3
"""生JSONとパース済みテーブルの件数が一致するか検査する。

生JSON（data/raw/YYYY/MM/YYYY-MM-DD.json）の feed.results の要素数と、
その日の CSV / JSONL の行数を突き合わせる。1件でも食い違えば exit 1。

パースの取りこぼしや、テーブルへの書き込みが途中で切れた事故を検出するのが狙い。
生JSONは無加工で残してあるので、こちらを正とする。

  python scripts/check_counts.py
"""

import csv
import glob
import json
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
PARSED_DIR = os.path.join(REPO_ROOT, "data", "parsed")

EXIT_OK = 0
EXIT_MISMATCH = 1


def raw_counts(raw_dir: str | None = None) -> dict[str, int]:
    """chart_date -> 生JSONの件数。ファイル名から日付を取る。"""
    directory = raw_dir or RAW_DIR
    counts = {}
    for path in sorted(glob.glob(os.path.join(directory, "*", "*", "*.json"))):
        chart_date = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        counts[chart_date] = len(document.get("feed", {}).get("results", []))
    return counts


def parsed_counts(parsed_dir: str | None = None) -> dict[str, dict[str, int]]:
    """chart_date -> {"csv": 行数, "jsonl": 行数}。"""
    directory = parsed_dir or PARSED_DIR
    csv_counts: Counter = Counter()
    jsonl_counts: Counter = Counter()

    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("chart_date"):
                    csv_counts[row["chart_date"]] += 1

    for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                chart_date = json.loads(line).get("chart_date")
                if chart_date:
                    jsonl_counts[chart_date] += 1

    days = set(csv_counts) | set(jsonl_counts)
    return {day: {"csv": csv_counts[day], "jsonl": jsonl_counts[day]} for day in days}


def compare(
    raw_dir: str | None = None, parsed_dir: str | None = None
) -> list[dict]:
    """食い違いを列挙する。問題が無ければ空リスト。"""
    raw = raw_counts(raw_dir)
    parsed = parsed_counts(parsed_dir)
    findings = []

    for day in sorted(set(raw) | set(parsed)):
        if day not in parsed:
            findings.append(
                {"chart_date": day, "issue": "テーブルに行が無い", "raw": raw[day], "csv": 0, "jsonl": 0}
            )
            continue
        if day not in raw:
            findings.append(
                {
                    "chart_date": day,
                    "issue": "生JSONが無い",
                    "raw": None,
                    "csv": parsed[day]["csv"],
                    "jsonl": parsed[day]["jsonl"],
                }
            )
            continue
        if raw[day] != parsed[day]["csv"] or raw[day] != parsed[day]["jsonl"]:
            findings.append(
                {
                    "chart_date": day,
                    "issue": "件数が一致しない",
                    "raw": raw[day],
                    "csv": parsed[day]["csv"],
                    "jsonl": parsed[day]["jsonl"],
                }
            )
    return findings


def main() -> int:
    raw = raw_counts()
    parsed = parsed_counts()
    days = set(raw) | set(parsed)
    if not days:
        print("データがありません。照合をスキップします。")
        return EXIT_OK

    findings = compare()
    if not findings:
        print(f"{len(days)}日ぶんの件数が生JSONと一致しています。")
        return EXIT_OK

    print(f"\n異常: 件数が食い違う日が {len(findings)}件 あります", file=sys.stderr)
    for finding in findings:
        raw_count = "-" if finding["raw"] is None else finding["raw"]
        print(
            f"  - {finding['chart_date']}  {finding['issue']}"
            f"  生JSON={raw_count} CSV={finding['csv']} JSONL={finding['jsonl']}",
            file=sys.stderr,
        )
    return EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
