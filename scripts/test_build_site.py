#!/usr/bin/env python3
"""build_site.py のテスト。

実データは読まず、架空のスナップショットからサイトを組み立てて検証する。
見た目そのものではなく、並べ替えの順序・絞り込み・変動表示・エスケープなど、
壊れると内容が誤って伝わる部分を対象にする。

  python scripts/test_build_site.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_site
import song_stats

CALENDAR = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(30)]


def days(*indexes: int) -> list[str]:
    return [CALENDAR[i] for i in indexes]


def make_conn(entries: dict[str, list], observed: list[str] | None = None):
    """entries は {track_id: [(日付, 順位), ...]}。観測日は埋め草の曲で成立させる。"""
    if observed is None:
        observed = sorted({day for rows in entries.values() for day, _ in rows})
    connection = song_stats.open_snapshots([])
    rows = [(day, 100, "FILLER", "埋め草", "埋め草歌手") for day in observed]
    for track_id, appearances in entries.items():
        for day, rank in appearances:
            rows.append((day, rank, track_id, f"曲{track_id}", f"歌手{track_id}"))
    connection.executemany(
        "INSERT INTO snapshots (chart_date, rank, track_id, name, artist_name)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    return connection


def by_id(data: dict) -> dict[str, dict]:
    return {track["track_id"]: track for track in data["tracks"]}


class WellFormed(HTMLParser):
    """開始タグと終了タグの対応だけを見る簡易検査。"""

    VOID = {"meta", "link", "br", "hr", "img", "input", "source", "col"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"閉じ過ぎ: </{tag}>")
        elif self.stack[-1] != tag:
            self.errors.append(f"対応しない終了タグ: </{tag}> (開いているのは {self.stack[-1]})")
        else:
            self.stack.pop()


class TestCollect(unittest.TestCase):
    def test_rank_and_delta_for_a_riser(self):
        connection = make_conn({"A": [(CALENDAR[0], 10), (CALENDAR[1], 4)]})
        track = by_id(build_site.collect(connection))["A"]
        self.assertEqual(track["rank"], 4)
        self.assertEqual(track["delta"], "+6")

    def test_delta_for_a_faller_is_negative(self):
        connection = make_conn({"A": [(CALENDAR[0], 4), (CALENDAR[1], 10)]})
        self.assertEqual(by_id(build_site.collect(connection))["A"]["delta"], "-6")

    def test_no_change_is_marked_explicitly(self):
        """空欄にすると、変わらなかったのかデータが無いのか区別できない。"""
        connection = make_conn({"A": [(CALENDAR[0], 7), (CALENDAR[1], 7)]})
        self.assertEqual(by_id(build_site.collect(connection))["A"]["delta"], "=")

    def test_debut_and_reentry_are_distinguished(self):
        """どちらも前日との差が計算できない行だが、意味が違うので同じ表示にしない。"""
        connection = make_conn(
            {
                "BACK": [(CALENDAR[0], 5), (CALENDAR[2], 5)],  # 1日空けて復帰
                "DEBUT": [(CALENDAR[2], 8)],  # 最新日が初登場
                "GONE": [(CALENDAR[0], 9)],  # 最新日はランク外
            },
            observed=days(0, 1, 2),
        )
        tracks = by_id(build_site.collect(connection))
        self.assertEqual(tracks["BACK"]["delta"], "再")
        self.assertEqual(tracks["DEBUT"]["delta"], "初")
        self.assertEqual(tracks["GONE"]["delta"], "—")

    def test_debut_on_the_latest_day(self):
        connection = make_conn({"A": [(CALENDAR[0], 3)], "B": [(CALENDAR[1], 9)]})
        self.assertEqual(by_id(build_site.collect(connection))["B"]["delta"], "初")

    def test_dropped_out_song_has_no_rank(self):
        connection = make_conn({"A": [(CALENDAR[0], 3)], "B": [(CALENDAR[1], 9)]})
        track = by_id(build_site.collect(connection))["A"]
        self.assertIsNone(track["rank"])
        self.assertEqual(track["delta"], "—")
        self.assertEqual(track["total_days"], 1)

    def test_single_day_of_data_has_no_delta(self):
        connection = make_conn({"A": [(CALENDAR[0], 3)]})
        self.assertEqual(by_id(build_site.collect(connection))["A"]["delta"], "")

    def test_empty_database(self):
        data = build_site.collect(song_stats.open_snapshots([]))
        self.assertEqual(data, {"dates": [], "months": [], "tracks": []})

    def test_label_comes_from_the_latest_row(self):
        connection = song_stats.open_snapshots([])
        connection.executemany(
            "INSERT INTO snapshots (chart_date, rank, track_id, name, artist_name)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (CALENDAR[0], 1, "A", "旧題", "歌手"),
                (CALENDAR[1], 1, "A", "新題", "歌手"),
            ],
        )
        self.assertEqual(by_id(build_site.collect(connection))["A"]["name"], "新題")


class TestSorting(unittest.TestCase):
    def setUp(self):
        # A: 古参で長期在籍  B: 新入り  C: 最新日はランク外
        self.connection = make_conn(
            {
                "A": [(day, 5) for day in days(0, 1, 2, 3)],
                "B": [(CALENDAR[3], 2)],
                "C": [(CALENDAR[0], 1)],
            },
            observed=days(0, 1, 2, 3),
        )
        self.tracks = build_site.collect(self.connection)["tracks"]

    def order(self, sort: str) -> list[str]:
        """埋め草は観測日を成立させるためだけの行なので、順序の検証からは外す。"""
        ordered = build_site.sorted_tracks(self.tracks, sort)
        return [t["track_id"] for t in ordered if t["track_id"] != "FILLER"]

    def test_rank_puts_unranked_last(self):
        self.assertEqual(self.order("rank")[:2], ["B", "A"])
        self.assertEqual(self.order("rank")[-1], "C")

    def test_newcomer_orders_by_newest_debut(self):
        self.assertEqual(self.order("newcomer")[0], "B")

    def test_veteran_is_the_reverse_end(self):
        self.assertEqual(self.order("veteran")[-1], "B")

    def test_longhit_orders_by_total_days(self):
        self.assertEqual(self.order("longhit")[0], "A")

    def test_every_sort_keeps_all_rows(self):
        for sort, _ in build_site.SORTS:
            with self.subTest(sort=sort):
                self.assertEqual(len(self.order(sort)), len(self.tracks) - 1)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.connection = make_conn(
            {"A": [(CALENDAR[0], 1), (CALENDAR[1], 2)]}, observed=days(0, 1)
        )
        self.data = build_site.collect(self.connection)

    def test_list_page_is_well_formed(self):
        page = build_site.list_page(self.data, ["all"], "all", "rank")
        parser = WellFormed()
        parser.feed(page)
        self.assertEqual(parser.errors, [])
        self.assertEqual(parser.stack, [])

    def test_list_page_is_indexable(self):
        page = build_site.list_page(self.data, ["all"], "all", "rank")
        self.assertNotIn("noindex", page)

    def test_search_page_is_noindex(self):
        """検索結果は無数の組み合わせが生まれるため、索引させない。"""
        page = build_site.search_page(self.data)
        self.assertIn('name="robots"', page)
        self.assertIn("noindex", page)

    def test_footer_states_when_observation_began(self):
        page = build_site.list_page(self.data, ["all"], "all", "rank")
        self.assertIn("2026年8月から観測開始", page)

    def test_controls_are_always_present(self):
        """並べ替えと絞り込みは、どのページからでも切り替えられること。"""
        for sort, label in build_site.SORTS:
            page = build_site.list_page(self.data, ["all", "2026-01"], "all", sort)
            with self.subTest(sort=sort):
                self.assertIn(label, page)
                self.assertIn("2026年1月", page)
                self.assertIn('aria-current="true"', page)

    def test_viewport_is_declared(self):
        page = build_site.list_page(self.data, ["all"], "all", "rank")
        self.assertIn('name="viewport"', page)

    def test_month_filter_narrows_the_list(self):
        connection = make_conn(
            {"JAN": [("2026-01-05", 1)], "FEB": [("2026-02-05", 1)]},
            observed=["2026-01-05", "2026-02-05"],
        )
        data = build_site.collect(connection)
        january = build_site.list_page(data, ["all", "2026-01"], "2026-01", "rank")
        self.assertIn("曲JAN", january)
        self.assertNotIn("曲FEB", january)

    def test_markup_in_song_names_is_escaped(self):
        connection = make_conn({"X": [(CALENDAR[0], 1)]})
        connection.execute("UPDATE snapshots SET name = ? WHERE track_id = 'X'", ("<b>&",))
        data = build_site.collect(connection)
        page = build_site.list_page(data, ["all"], "all", "rank")
        self.assertNotIn("<b>&", page)
        self.assertIn("&lt;b&gt;&amp;", page)

    def test_search_index_escapes_closing_tags(self):
        """曲名に </script> が入っても、埋め込んだ索引が途中で切れないこと。"""
        connection = make_conn({"X": [(CALENDAR[0], 1)]})
        connection.execute(
            "UPDATE snapshots SET name = ? WHERE track_id = 'X'", ("</script>",)
        )
        page = build_site.search_page(build_site.collect(connection))
        self.assertNotIn("</script>x", page)
        self.assertIn("<\\/script>", page)


class TestBuild(unittest.TestCase):
    def test_writes_every_combination(self):
        connection = make_conn(
            {"A": [("2026-01-05", 1)], "B": [("2026-02-05", 1)]},
            observed=["2026-01-05", "2026-02-05"],
        )
        original = song_stats.open_snapshots
        song_stats.open_snapshots = lambda *args, **kwargs: connection
        try:
            with tempfile.TemporaryDirectory() as directory:
                written = build_site.build(directory)
                names = sorted(os.path.basename(path) for path in written)
        finally:
            song_stats.open_snapshots = original
        # (すべて + 2か月) × 4通り + 検索
        self.assertEqual(len(names), 3 * 4 + 1)
        self.assertIn("index.html", names)
        self.assertIn("search.html", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
