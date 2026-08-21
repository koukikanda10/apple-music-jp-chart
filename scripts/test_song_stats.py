#!/usr/bin/env python3
"""song_stats.py のテスト。

実データは一切読まない。テストごとに架空のスナップショットをメモリ上の
データベースへ流し込み、そこから導出される指標を検証する。
実データに依存すると、チャートの中身が変わるたびに結果が変わってしまうため。

  python scripts/test_song_stats.py

失敗があれば終了コード 1 で終わる。
"""

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_chart
import song_stats

# 30日ぶんの日付。テストは「何日目か」で書き、実際の日付はここから引く
CALENDAR = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(30)]


def days(*indexes: int) -> list[str]:
    """0始まりの日番号を日付文字列にする。days(0, 2) -> ['2026-01-01', '2026-01-03']"""
    return [CALENDAR[i] for i in indexes]


def make_conn(charted: dict[str, list], observed: list[str] | None = None):
    """架空のスナップショットを載せた接続を返す。

    charted  {track_id: [日付, ...]} または {track_id: [(日付, 順位), ...]}
    observed 観測された日の一覧。省略すると CALENDAR の全日が観測済みになる。

    在籍区間の空白は「観測日の数」で測るため、どの日が観測済みかがテスト結果を左右する。
    そこで観測日ごとに必ず在籍する埋め草の曲を入れ、その日を観測済みにしている。
    """
    if observed is None:
        observed = CALENDAR
    connection = song_stats.open_snapshots([])
    rows = [(day, 100, "FILLER", "埋め草", "埋め草グループ") for day in observed]
    for track_id, entries in charted.items():
        for entry in entries:
            day, rank = entry if isinstance(entry, tuple) else (entry, 10)
            rows.append((day, rank, track_id, f"曲{track_id}", f"歌手{track_id}"))
    connection.executemany(
        "INSERT INTO snapshots (chart_date, rank, track_id, name, artist_name)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    return connection


def run(start: int, end: int, length: int) -> dict:
    """期待する区間を日番号で書くための補助。"""
    return {"start_date": CALENDAR[start], "end_date": CALENDAR[end], "days": length}


class TestChartRunsBoundary(unittest.TestCase):
    """在籍区間を分ける閾値のちょうど境目を確かめる。"""

    def test_gap_of_six_days_stays_one_run(self):
        # 0,1,2日目に在籍 → 3〜8日目の6日間は不在 → 9日目に復帰
        connection = make_conn({"A": days(0, 1, 2, 9)})
        self.assertEqual(song_stats.chart_runs(connection, "A"), [run(0, 9, 4)])

    def test_gap_of_seven_days_splits(self):
        # 0,1,2日目に在籍 → 3〜9日目の7日間は不在 → 10日目に復帰
        connection = make_conn({"A": days(0, 1, 2, 10)})
        self.assertEqual(
            song_stats.chart_runs(connection, "A"),
            [run(0, 2, 3), run(10, 10, 1)],
        )

    def test_threshold_is_configurable(self):
        """閾値は定数を外から差し替えられる。6日空白も gap_days=6 なら割れる。"""
        connection = make_conn({"A": days(0, 1, 2, 9)})
        self.assertEqual(len(song_stats.chart_runs(connection, "A", gap_days=6)), 2)
        self.assertEqual(len(song_stats.chart_runs(connection, "A", gap_days=7)), 1)

    def test_default_threshold_is_seven(self):
        self.assertEqual(song_stats.SESSION_GAP_DAYS, 7)


class TestChartRunsShapes(unittest.TestCase):
    """区間の分かれ方そのもの。"""

    def test_continuous_only(self):
        """一度も途切れず在籍し続けた場合は1区間。"""
        connection = make_conn({"A": days(0, 1, 2, 3, 4, 5, 6)})
        self.assertEqual(song_stats.chart_runs(connection, "A"), [run(0, 6, 7)])
        self.assertEqual(song_stats.total_days(connection, "A"), 7)

    def test_single_day_only(self):
        """1日だけランクインした場合も、長さ1の区間が1つできる。"""
        connection = make_conn({"A": days(4)})
        self.assertEqual(song_stats.chart_runs(connection, "A"), [run(4, 4, 1)])

    def test_three_runs(self):
        # 0-2日目 → 7日空き → 10,11日目 → 7日空き → 19日目
        connection = make_conn({"A": days(0, 1, 2, 10, 11, 19)})
        self.assertEqual(
            song_stats.chart_runs(connection, "A"),
            [run(0, 2, 3), run(10, 11, 2), run(19, 19, 1)],
        )

    def test_four_runs_with_single_day_islands(self):
        """1日だけの区間が並ぶ場合も、区間として数える。"""
        connection = make_conn({"A": days(0, 8, 16, 24)})
        self.assertEqual(
            song_stats.chart_runs(connection, "A"),
            [run(0, 0, 1), run(8, 8, 1), run(16, 16, 1), run(24, 24, 1)],
        )

    def test_total_days_counts_across_runs(self):
        """通算在籍日数は区間が割れても合計される。"""
        connection = make_conn({"A": days(0, 1, 2, 10, 11, 19)})
        self.assertEqual(song_stats.total_days(connection, "A"), 6)

    def test_same_day_twice_counts_once(self):
        """同じ日に複数行あっても1日と数える。"""
        connection = make_conn({"A": [(CALENDAR[0], 10), (CALENDAR[0], 11)]})
        self.assertEqual(song_stats.total_days(connection, "A"), 1)
        self.assertEqual(song_stats.chart_runs(connection, "A"), [run(0, 0, 1)])

    def test_collection_outage_does_not_split(self):
        """収集側が止まった期間は不在に数えない。暦では8日空くが区間は割れない。"""
        observed = days(0, 1, 2) + days(10, 11)
        connection = make_conn({"A": observed}, observed=observed)
        self.assertEqual(song_stats.chart_runs(connection, "A"), [run(0, 11, 5)])


class TestPeakPosition(unittest.TestCase):
    """最高順位と、それを記録した日付。"""

    def test_tie_returns_first_date(self):
        """同じ最高順位が複数日続いたら、最初にその順位になった日を返す。"""
        connection = make_conn(
            {"A": [(CALENDAR[0], 5), (CALENDAR[1], 2), (CALENDAR[2], 2), (CALENDAR[3], 3)]}
        )
        self.assertEqual(
            song_stats.peak_position(connection, "A"),
            {"rank": 2, "date": CALENDAR[1], "days_at_peak": 2},
        )

    def test_tie_across_separate_runs(self):
        """区間をまたいで同順位が並んでも、最初の日と通算日数を返す。"""
        connection = make_conn(
            {"A": [(CALENDAR[0], 3), (CALENDAR[10], 3), (CALENDAR[20], 9)]}
        )
        self.assertEqual(
            song_stats.peak_position(connection, "A"),
            {"rank": 3, "date": CALENDAR[0], "days_at_peak": 2},
        )

    def test_single_best_day(self):
        """最高順位が1日だけなら days_at_peak は1。"""
        connection = make_conn(
            {"A": [(CALENDAR[0], 8), (CALENDAR[1], 1), (CALENDAR[2], 4)]}
        )
        self.assertEqual(
            song_stats.peak_position(connection, "A"),
            {"rank": 1, "date": CALENDAR[1], "days_at_peak": 1},
        )

    def test_peak_on_last_day(self):
        """最高順位が最終日でも正しく拾う。"""
        connection = make_conn(
            {"A": [(CALENDAR[0], 50), (CALENDAR[1], 30), (CALENDAR[2], 7)]}
        )
        peak = song_stats.peak_position(connection, "A")
        self.assertEqual((peak["rank"], peak["date"]), (7, CALENDAR[2]))

    def test_smaller_number_is_better(self):
        """順位は数が小さいほど上位。100位より1位が最高になる。"""
        connection = make_conn({"A": [(CALENDAR[0], 100), (CALENDAR[1], 1)]})
        self.assertEqual(song_stats.peak_position(connection, "A")["rank"], 1)


class TestFirstCharted(unittest.TestCase):
    def test_returns_earliest_day(self):
        connection = make_conn({"A": days(5, 6, 20)})
        self.assertEqual(song_stats.first_charted(connection, "A"), CALENDAR[5])

    def test_unaffected_by_insertion_order(self):
        """挿入順ではなく日付で決まる。"""
        connection = make_conn({"A": days(20, 5, 6)})
        self.assertEqual(song_stats.first_charted(connection, "A"), CALENDAR[5])

    def test_first_run_start_matches(self):
        """初回ランクイン日は最初の区間の開始日と一致する。"""
        connection = make_conn({"A": days(5, 6, 20)})
        first_run = song_stats.chart_runs(connection, "A")[0]
        self.assertEqual(song_stats.first_charted(connection, "A"), first_run["start_date"])


class TestEmptyResults(unittest.TestCase):
    """結果が空でも例外にならないこと。"""

    def test_no_data_at_all(self):
        connection = song_stats.open_snapshots([])
        self.assertIsNone(song_stats.first_charted(connection, "A"))
        self.assertIsNone(song_stats.peak_position(connection, "A"))
        self.assertEqual(song_stats.total_days(connection, "A"), 0)
        self.assertEqual(song_stats.chart_runs(connection, "A"), [])
        self.assertEqual(song_stats.find_tracks(connection, "何か"), [])

    def test_unknown_track_id(self):
        connection = make_conn({"A": days(0, 1)})
        self.assertIsNone(song_stats.first_charted(connection, "MISSING"))
        self.assertIsNone(song_stats.peak_position(connection, "MISSING"))
        self.assertEqual(song_stats.total_days(connection, "MISSING"), 0)
        self.assertEqual(song_stats.chart_runs(connection, "MISSING"), [])

    def test_summary_of_unknown_track_id(self):
        connection = make_conn({"A": days(0, 1)})
        result = song_stats.summary(connection, "MISSING")
        self.assertEqual(result["track_id"], "MISSING")
        self.assertIsNone(result["name"])
        self.assertIsNone(result["peak"])
        self.assertEqual(result["total_days"], 0)
        self.assertEqual(result["runs"], [])

    def test_single_day_of_data(self):
        """データが1日分しかなくても成立する。"""
        connection = make_conn({"A": [(CALENDAR[0], 3)]}, observed=days(0))
        result = song_stats.summary(connection, "A")
        self.assertEqual(result["first_charted"], CALENDAR[0])
        self.assertEqual(result["peak"]["rank"], 3)
        self.assertEqual(result["total_days"], 1)
        self.assertEqual(result["runs"], [run(0, 0, 1)])


class TestSummary(unittest.TestCase):
    def test_uses_latest_label(self):
        """曲名は最新の行から取る。改名されても最後の表記が出る。"""
        connection = song_stats.open_snapshots([])
        connection.executemany(
            "INSERT INTO snapshots (chart_date, rank, track_id, name, artist_name)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (CALENDAR[0], 4, "A", "旧タイトル", "歌手"),
                (CALENDAR[1], 4, "A", "新タイトル", "歌手"),
            ],
        )
        self.assertEqual(song_stats.summary(connection, "A")["name"], "新タイトル")

    def test_respects_gap_days_argument(self):
        connection = make_conn({"A": days(0, 1, 2, 9)})
        self.assertEqual(len(song_stats.summary(connection, "A", gap_days=7)["runs"]), 1)
        self.assertEqual(len(song_stats.summary(connection, "A", gap_days=6)["runs"]), 2)


class TestSchema(unittest.TestCase):
    def test_columns_match_the_writer(self):
        """取得側の列定義とずれていないこと。列が増えたらここで気づける。"""
        self.assertEqual([name for name, _ in song_stats.COLUMNS], fetch_chart.FIELDS)

    def test_loads_from_csv_files(self):
        """CSV を経由する読み込み経路も通しておく。"""
        import csv
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "2026-01.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=fetch_chart.FIELDS, lineterminator="\n"
                )
                writer.writeheader()
                writer.writerow(
                    {"chart_date": CALENDAR[0], "rank": 1, "track_id": "A", "name": "曲"}
                )
            connection = song_stats.open_snapshots([path])
        self.assertEqual(song_stats.total_days(connection, "A"), 1)
        self.assertEqual(song_stats.peak_position(connection, "A")["rank"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
