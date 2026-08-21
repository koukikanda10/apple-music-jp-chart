#!/usr/bin/env python3
"""取得の成否を1行1試行で記録する。

data/fetch_log.csv に追記していく。狙いは「その日データが無い理由」を後から言い当てられること。
チャートに載らなかった日と、そもそも取得できていない日を、データの有無だけから区別するのは
不可能なので、試行の側を記録しておく。

  ok       取得してテーブルに保存した
  skipped  その日は既に記録済みだったので取得しなかった（予備実行）
  failed   取得または保存に失敗した

日ごとの状態は day_statuses() で導出する。ログに1行も無い日は、ワークフローが
そもそも起動しなかった（GitHub にスケジュールを飛ばされた）ことを示す。

  python scripts/fetch_log.py        日ごとの状態を一覧する
"""

import csv
import glob
import os
import sys
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(REPO_ROOT, "data", "fetch_log.csv")
PARSED_DIR = os.path.join(REPO_ROOT, "data", "parsed")

FIELDS = ["chart_date", "attempted_at", "status", "item_count", "detail"]

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# day_statuses() が返す日ごとの状態
DAY_OK = "ok"  # データがある
DAY_FAILED = "failed"  # 試行はしたが取れなかった
DAY_MISSING = "missing"  # 試行の記録すら無い（ワークフローが動いていない）


def record(
    chart_date: str,
    status: str,
    item_count: int = 0,
    detail: str = "",
    log_path: str | None = None,
) -> None:
    """1試行ぶんを追記する。ファイルが無ければヘッダから作る。"""
    path = log_path or LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    # 失敗の詳細に改行が混ざると1行1レコードが崩れるので潰す
    flattened = " ".join(str(detail).split())
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if is_new:
            # 他のCSVに合わせ、Excelで文字化けしないよう先頭にBOMを置く。
            # 追記のたびに BOM が入らないよう、utf-8-sig ではなく新規作成時だけ書く
            handle.write("﻿")
            writer.writeheader()
        writer.writerow(
            {
                "chart_date": chart_date,
                "attempted_at": datetime.now(JST).isoformat(timespec="seconds"),
                "status": status,
                "item_count": item_count,
                "detail": flattened,
            }
        )


def load(log_path: str | None = None) -> list[dict]:
    path = log_path or LOG_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def recorded_dates(parsed_dir: str | None = None) -> set[str]:
    """パース済みテーブルに実際にデータがある日。"""
    directory = parsed_dir or PARSED_DIR
    dates = set()
    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("chart_date"):
                    dates.add(row["chart_date"])
    return dates


def day_statuses(
    log_path: str | None = None, parsed_dir: str | None = None
) -> dict[str, str]:
    """記録の最初の日から最後の日まで、1日ごとの状態を返す。

    データがある日は ok。無い日は、失敗の記録があれば failed、
    記録が1行も無ければ missing（ワークフローが起動していない）とする。

    データの有無を先に見るのは、fetch_log を導入する前に取得した日を
    正しく ok と判定するため。
    """
    entries = load(log_path)
    with_data = recorded_dates(parsed_dir)
    attempted: dict[str, set[str]] = {}
    for entry in entries:
        attempted.setdefault(entry["chart_date"], set()).add(entry["status"])

    known = sorted(with_data | set(attempted))
    if not known:
        return {}

    first = date.fromisoformat(known[0])
    last = date.fromisoformat(known[-1])
    statuses = {}
    for offset in range((last - first).days + 1):
        day = (first + timedelta(days=offset)).isoformat()
        if day in with_data:
            statuses[day] = DAY_OK
        elif day in attempted:
            statuses[day] = DAY_FAILED
        else:
            statuses[day] = DAY_MISSING
    return statuses


def main() -> int:
    statuses = day_statuses()
    if not statuses:
        print("記録がまだありません。")
        return 0

    entries = load()
    detail_by_day: dict[str, list[str]] = {}
    for entry in entries:
        if entry["status"] == STATUS_FAILED and entry["detail"]:
            detail_by_day.setdefault(entry["chart_date"], []).append(entry["detail"])

    label = {DAY_OK: "ok     ", DAY_FAILED: "failed ", DAY_MISSING: "missing"}
    for day, status in statuses.items():
        note = ""
        if status == DAY_FAILED:
            note = "  " + " / ".join(detail_by_day.get(day, []))
        elif status == DAY_MISSING:
            note = "  ワークフローが起動していない"
        print(f"{day}  {label[status]}{note}")

    counts = {state: list(statuses.values()).count(state) for state in label}
    print(
        f"\n{len(statuses)}日中 ok={counts[DAY_OK]} "
        f"failed={counts[DAY_FAILED]} missing={counts[DAY_MISSING]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
