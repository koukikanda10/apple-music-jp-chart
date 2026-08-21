#!/usr/bin/env python3
"""fetch_log.py と check_counts.py のテスト。

実データは読まず、テストごとに一時ディレクトリへ架空のファイルを作って検証する。

  python scripts/test_checks.py
"""

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_counts
import fetch_log

PARSED_FIELDS = ["chart_date", "rank", "track_id", "name"]


class TempData(unittest.TestCase):
    """一時ディレクトリに raw / parsed / fetch_log を組み立てる土台。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        self.raw_dir = os.path.join(self.root, "raw")
        self.parsed_dir = os.path.join(self.root, "parsed")
        self.log_path = os.path.join(self.root, "fetch_log.csv")
        os.makedirs(self.raw_dir)
        os.makedirs(self.parsed_dir)

    def write_raw(self, chart_date: str, count: int):
        year, month, _ = chart_date.split("-")
        directory = os.path.join(self.raw_dir, year, month)
        os.makedirs(directory, exist_ok=True)
        document = {"feed": {"results": [{"id": str(i)} for i in range(count)]}}
        with open(os.path.join(directory, f"{chart_date}.json"), "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def write_parsed(self, counts: dict[str, int], jsonl_counts: dict[str, int] | None = None):
        """counts は chart_date -> 行数。jsonl_counts を省くと CSV と同数にする。"""
        jsonl_counts = counts if jsonl_counts is None else jsonl_counts
        csv_path = os.path.join(self.parsed_dir, "2026-01.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PARSED_FIELDS, lineterminator="\n")
            writer.writeheader()
            for chart_date, count in sorted(counts.items()):
                for rank in range(1, count + 1):
                    writer.writerow(
                        {"chart_date": chart_date, "rank": rank, "track_id": f"T{rank}", "name": "曲"}
                    )
        jsonl_path = os.path.join(self.parsed_dir, "2026-01.jsonl")
        with open(jsonl_path, "w", encoding="utf-8", newline="\n") as handle:
            for chart_date, count in sorted(jsonl_counts.items()):
                for rank in range(1, count + 1):
                    handle.write(json.dumps({"chart_date": chart_date, "rank": rank}) + "\n")


class TestCheckCounts(TempData):
    def test_matching_counts_report_nothing(self):
        self.write_raw("2026-01-01", 100)
        self.write_parsed({"2026-01-01": 100})
        self.assertEqual(check_counts.compare(self.raw_dir, self.parsed_dir), [])

    def test_short_table_is_caught(self):
        """パースで取りこぼした場合。生JSON 100件に対しテーブルが99行。"""
        self.write_raw("2026-01-01", 100)
        self.write_parsed({"2026-01-01": 99})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["issue"], "件数が一致しない")
        self.assertEqual((findings[0]["raw"], findings[0]["csv"]), (100, 99))

    def test_extra_rows_are_caught(self):
        """二重書き込みで行が増えた場合も検出する。"""
        self.write_raw("2026-01-01", 100)
        self.write_parsed({"2026-01-01": 200})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual(findings[0]["issue"], "件数が一致しない")

    def test_jsonl_alone_can_mismatch(self):
        """CSV は合っていて JSONL だけずれている場合も見逃さない。"""
        self.write_raw("2026-01-01", 100)
        self.write_parsed({"2026-01-01": 100}, jsonl_counts={"2026-01-01": 98})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]["csv"], findings[0]["jsonl"]), (100, 98))

    def test_missing_raw_file(self):
        """生JSONだけ失われた場合。再パースができなくなるので異常として扱う。"""
        self.write_parsed({"2026-01-01": 100})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual(findings[0]["issue"], "生JSONが無い")

    def test_missing_parsed_rows(self):
        """生JSONはあるのにテーブルに入っていない場合。"""
        self.write_raw("2026-01-01", 100)
        self.write_parsed({})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual(findings[0]["issue"], "テーブルに行が無い")

    def test_only_the_broken_day_is_reported(self):
        for day, count in [("2026-01-01", 100), ("2026-01-02", 100), ("2026-01-03", 100)]:
            self.write_raw(day, count)
        self.write_parsed({"2026-01-01": 100, "2026-01-02": 42, "2026-01-03": 100})
        findings = check_counts.compare(self.raw_dir, self.parsed_dir)
        self.assertEqual([f["chart_date"] for f in findings], ["2026-01-02"])

    def test_no_data_at_all(self):
        self.assertEqual(check_counts.compare(self.raw_dir, self.parsed_dir), [])

    def test_fewer_than_100_is_fine_if_consistent(self):
        """APIが100件返さない日もある。生JSONと一致していれば正常。"""
        self.write_raw("2026-01-01", 87)
        self.write_parsed({"2026-01-01": 87})
        self.assertEqual(check_counts.compare(self.raw_dir, self.parsed_dir), [])


class TestFetchLogRecording(TempData):
    def test_creates_header_then_appends(self):
        fetch_log.record("2026-01-01", fetch_log.STATUS_OK, 100, log_path=self.log_path)
        fetch_log.record("2026-01-02", fetch_log.STATUS_FAILED, 0, "HTTP 504", log_path=self.log_path)
        entries = fetch_log.load(self.log_path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["status"], "ok")
        self.assertEqual(entries[0]["item_count"], "100")
        self.assertEqual(entries[1]["detail"], "HTTP 504")

    def test_multiline_detail_stays_on_one_line(self):
        """失敗メッセージに改行が混ざっても1行1レコードを崩さない。"""
        fetch_log.record(
            "2026-01-01", fetch_log.STATUS_FAILED, 0, "line1\nline2\n  line3", log_path=self.log_path
        )
        self.assertEqual(fetch_log.load(self.log_path)[0]["detail"], "line1 line2 line3")
        with open(self.log_path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.readlines()), 2)  # ヘッダ + 1行

    def test_load_without_file(self):
        self.assertEqual(fetch_log.load(self.log_path), [])


class TestDayStatuses(TempData):
    """狙いの中心。cron が失敗した日と、単にランク外だった日を区別できること。"""

    def test_day_with_data_is_ok(self):
        self.write_parsed({"2026-01-01": 100})
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(statuses, {"2026-01-01": fetch_log.DAY_OK})

    def test_day_with_data_but_no_log_is_still_ok(self):
        """fetch_log を導入する前に取得した日を failed と誤判定しない。"""
        self.write_parsed({"2026-01-01": 100, "2026-01-02": 100})
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(set(statuses.values()), {fetch_log.DAY_OK})

    def test_failed_day_is_distinguished_from_missing(self):
        """取りに行って失敗した日は failed、起動すらしなかった日は missing。"""
        self.write_parsed({"2026-01-01": 100, "2026-01-04": 100})
        fetch_log.record("2026-01-01", fetch_log.STATUS_OK, 100, log_path=self.log_path)
        fetch_log.record("2026-01-03", fetch_log.STATUS_FAILED, 0, "HTTP 504", log_path=self.log_path)
        fetch_log.record("2026-01-04", fetch_log.STATUS_OK, 100, log_path=self.log_path)
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(
            statuses,
            {
                "2026-01-01": fetch_log.DAY_OK,
                "2026-01-02": fetch_log.DAY_MISSING,  # ログにも無い = 起動していない
                "2026-01-03": fetch_log.DAY_FAILED,  # 試行はした
                "2026-01-04": fetch_log.DAY_OK,
            },
        )

    def test_skipped_run_does_not_make_a_day_ok(self):
        """予備実行の skipped だけでは ok にしない。データの有無で判定する。"""
        fetch_log.record("2026-01-01", fetch_log.STATUS_SKIPPED, 0, log_path=self.log_path)
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(statuses["2026-01-01"], fetch_log.DAY_FAILED)

    def test_range_covers_gaps_between_first_and_last(self):
        self.write_parsed({"2026-01-01": 1, "2026-01-10": 1})
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(len(statuses), 10)
        self.assertEqual(list(statuses.values()).count(fetch_log.DAY_MISSING), 8)

    def test_no_records_at_all(self):
        self.assertEqual(fetch_log.day_statuses(self.log_path, self.parsed_dir), {})

    def test_out_of_chart_is_not_a_missing_day(self):
        """曲がランク外でも、その日にデータがあれば ok。欠測とは別物であること。"""
        self.write_parsed({"2026-01-01": 100, "2026-01-02": 100})
        statuses = fetch_log.day_statuses(self.log_path, self.parsed_dir)
        self.assertEqual(statuses["2026-01-02"], fetch_log.DAY_OK)
        # 1/2 のデータには存在しない曲を引いても、その日が欠測になるわけではない
        self.assertNotIn(fetch_log.DAY_MISSING, statuses.values())


if __name__ == "__main__":
    unittest.main(verbosity=2)
