#!/usr/bin/env python3
"""check_id_stability.py の終了コードのテスト。

日次ワークフローは --warn-only で呼ぶので、不一致があっても 0 を返すことを守る。
実データは読まず、一時ディレクトリに架空のCSVを作って検証する。

  python scripts/test_id_stability.py
"""

import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_id_stability

FIELDS = ["chart_date", "rank", "track_id", "name", "artist_name"]


class TestExitCode(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parsed_dir = directory.name
        patcher = mock.patch.object(check_id_stability, "PARSED_DIR", self.parsed_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rows: list[tuple[str, str]]):
        """rows は (chart_date, track_id)。曲名とアーティストは全行同じにする。"""
        path = os.path.join(self.parsed_dir, "2026-10.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for chart_date, track_id in rows:
                writer.writerow(
                    {"chart_date": chart_date, "rank": 1, "track_id": track_id,
                     "name": "群青", "artist_name": "YOASOBI"}
                )

    def run_main(self, *argv: str) -> int:
        with mock.patch.object(sys, "argv", ["check_id_stability.py", *argv]):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return check_id_stability.main()

    def test_changed_id_fails_by_default(self):
        self.write([("2026-10-14", "1528119630"), ("2026-10-15", "1542182767")])
        self.assertEqual(self.run_main(), check_id_stability.EXIT_UNSTABLE)

    def test_changed_id_only_warns_with_warn_only(self):
        self.write([("2026-10-14", "1528119630"), ("2026-10-15", "1542182767")])
        self.assertEqual(self.run_main("--warn-only"), check_id_stability.EXIT_OK)

    def test_stable_id_passes(self):
        self.write([("2026-10-14", "1542182767"), ("2026-10-15", "1542182767")])
        self.assertEqual(self.run_main(), check_id_stability.EXIT_OK)

    def test_unreadable_data_still_errors_with_warn_only(self):
        # データが読めないのは付け替えとは別の事態なので、--warn-only でも隠さない
        self.assertEqual(self.run_main("--warn-only"), check_id_stability.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
