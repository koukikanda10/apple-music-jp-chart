#!/usr/bin/env python3
"""観測記録から静的サイトを生成する。

標準ライブラリのみで動く。出力先は site/ で、data/ から毎回作り直せるため
リポジトリには含めない。

  python scripts/build_site.py

並べ替えと月別絞り込みは、組み合わせのぶんだけページを事前生成する。
JavaScript を使わずに済み、どのページも単体で開ける。ページ数は
（月数 + 1）× 並べ替え4通り で、月が増えても線形にしか増えない。

サイト内検索だけは結果が動的なので、ページ内に埋め込んだ索引をブラウザ側で
絞り込む。検索結果ページは noindex。

見た目は意図的に素のまま。配色・密度・様式は proposals/ で決める。
"""

import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import song_stats

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "site")

SITE_TITLE = "Apple Music 日本チャート 観測記録"
OBSERVATION_NOTE = "2026年8月から観測開始"

# 並べ替え。すべて昇順に統一し、降順にしたい項目は符号を反転して表現する
SORTS = [
    ("rank", "順位順"),
    ("newcomer", "新入り順"),
    ("veteran", "古株順"),
    ("longhit", "ロングヒット"),
]

# ランク外の曲を末尾へ送るための番兵。実際の順位は 1〜100
UNRANKED = 10**6

# 装飾は最小限。読めることだけを担保し、様式の判断は proposals/ に預ける
STYLESHEET = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 0.75rem 2rem;
  font-family: system-ui, sans-serif;
  line-height: 1.5;
}
header, footer, main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.125rem; margin: 1rem 0 0.25rem; }
.summary { margin: 0 0 0.75rem; font-size: 0.8125rem; }
.controls {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 0.5rem 0;
  background: Canvas;
  border-bottom: 1px solid;
}
.controls div { display: flex; flex-wrap: wrap; gap: 0.25rem 0.5rem; align-items: baseline; }
.controls div + div { margin-top: 0.25rem; }
.controls b { font-weight: normal; font-size: 0.8125rem; min-width: 4.5rem; }
.controls a { font-size: 0.875rem; }
.controls [aria-current] { font-weight: bold; text-decoration: none; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.875rem; }
th, td { padding: 0.375rem 0.5rem; border-bottom: 1px solid; text-align: left; white-space: nowrap; }
th { text-align: left; font-size: 0.8125rem; }
/* 数値は下の桁から読み比べるため右揃えにし、桁位置を縦に揃える */
.num { text-align: right; font-variant-numeric: tabular-nums; }
.title { white-space: normal; min-width: 12rem; }
.title span { display: block; font-size: 0.8125rem; }
.legend { font-size: 0.8125rem; margin: 0.5rem 0 0; }
.note { font-size: 0.8125rem; margin: 0 0 0.75rem; padding: 0.5rem; border: 1px solid; }
footer { margin-top: 2rem; padding-top: 0.75rem; border-top: 1px solid; font-size: 0.8125rem; }
#q { width: 100%; padding: 0.5rem; font-size: 1rem; }
"""

SEARCH_SCRIPT = """
const index = JSON.parse(document.getElementById("index").textContent);
const q = document.getElementById("q");
const body = document.getElementById("results");
const count = document.getElementById("count");
function render() {
  const needle = q.value.trim().toLowerCase();
  const hits = needle
    ? index.filter((t) => (t.n + " " + t.a).toLowerCase().includes(needle))
    : [];
  count.textContent = needle ? hits.length + "件" : "検索語を入力してください";
  body.innerHTML = hits
    .map(
      (t) =>
        "<tr><td class='num'>" + (t.r === null ? "—" : t.r) +
        "</td><td class='title'>" + esc(t.n) + "<span>" + esc(t.a) +
        "</span></td><td class='num'>" + t.f + "</td><td class='num'>" + t.p +
        "</td><td class='num'>" + t.d + "</td></tr>"
    )
    .join("");
}
function esc(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
q.addEventListener("input", render);
render();
"""


def collect(connection) -> dict:
    """曲ごとの指標を集め、一覧に必要な形に整える。指標の算出は song_stats に任せる。"""
    dates = [
        row["chart_date"]
        for row in connection.execute(
            "SELECT DISTINCT chart_date FROM snapshots ORDER BY chart_date"
        )
    ]
    if not dates:
        return {"dates": [], "months": [], "tracks": []}

    latest = dates[-1]
    previous = dates[-2] if len(dates) >= 2 else None
    ranks_at = {
        date: {
            row["track_id"]: row["rank"]
            for row in connection.execute(
                "SELECT track_id, rank FROM snapshots WHERE chart_date = ?", (date,)
            )
        }
        for date in filter(None, (latest, previous))
    }

    months_of: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT DISTINCT track_id, substr(chart_date, 1, 7) AS month FROM snapshots"
    ):
        months_of.setdefault(row["track_id"], set()).add(row["month"])

    # 曲名は最新の行から取る。表記が変わった場合に古い名前を出さないため
    labels = connection.execute(
        """
        SELECT s.track_id, s.name, s.artist_name
        FROM snapshots AS s
        JOIN (
            SELECT track_id, MAX(chart_date) AS last_date FROM snapshots GROUP BY track_id
        ) AS m ON m.track_id = s.track_id AND m.last_date = s.chart_date
        GROUP BY s.track_id
        """
    ).fetchall()

    tracks = []
    for label in labels:
        track_id = label["track_id"]
        summary = song_stats.summary(connection, track_id)
        rank = ranks_at.get(latest, {}).get(track_id)
        tracks.append(
            {
                "track_id": track_id,
                "name": label["name"],
                "artist_name": label["artist_name"],
                "rank": rank,
                "delta": delta_for(track_id, rank, ranks_at, latest, previous, summary),
                "first_charted": summary["first_charted"],
                "peak": summary["peak"]["rank"] if summary["peak"] else None,
                "total_days": summary["total_days"],
                "months": months_of.get(track_id, set()),
            }
        )
    return {
        "dates": dates,
        "months": sorted({month for months in months_of.values() for month in months}),
        "tracks": tracks,
    }


def delta_for(track_id, rank, ranks_at, latest, previous, summary) -> str:
    """変動の表示文字列。色に頼らず、符号と語だけで方向が分かるようにする。"""
    if rank is None:
        return "—"  # 最新日はランク外
    if previous is None:
        return ""  # 比較できる前日が無い
    before = ranks_at.get(previous, {}).get(track_id)
    if before is None:
        # 差分が計算できない行。初登場と再登場は意味が違うので区別する
        return "再" if summary["first_charted"] != latest else "初"
    if before == rank:
        return "="  # 空欄にするとデータが無いのか変わらないのか判別できない
    return f"{before - rank:+d}"


def sorted_tracks(tracks: list[dict], sort: str) -> list[dict]:
    """並べ替え。日付は数値に直して符号を反転できるようにし、すべて昇順で扱う。"""

    def as_number(date: str | None) -> int:
        return int(date.replace("-", "")) if date else 0

    def rank_or_last(track: dict) -> int:
        return track["rank"] if track["rank"] is not None else UNRANKED

    keys = {
        "rank": lambda t: (rank_or_last(t), t["name"]),
        "newcomer": lambda t: (-as_number(t["first_charted"]), rank_or_last(t), t["name"]),
        "veteran": lambda t: (as_number(t["first_charted"]), rank_or_last(t), t["name"]),
        "longhit": lambda t: (-t["total_days"], as_number(t["first_charted"]), t["name"]),
    }
    return sorted(tracks, key=keys[sort])


def page_name(scope: str, sort: str) -> str:
    return "index.html" if scope == "all" and sort == "rank" else f"{scope}-{sort}.html"


def scope_label(scope: str) -> str:
    if scope == "all":
        return "すべて"
    year, month = scope.split("-")
    return f"{year}年{int(month)}月"


def document(title: str, body: str, noindex: bool = False, extra_css: str = "") -> str:
    """extra_css は提案のプレビュー用。骨組みの体裁を変えずに様式だけ差し替えられるようにする。"""
    robots = '\n  <meta name="robots" content="noindex, nofollow">' if noindex else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">{robots}
  <title>{html.escape(title)}</title>
  <style>{STYLESHEET}{extra_css}</style>
</head>
<body>
{body}
<footer>
  <p>{html.escape(OBSERVATION_NOTE)}</p>
</footer>
</body>
</html>
"""


def link(href: str, label: str, current: bool) -> str:
    """今どれが選ばれているかを、支援技術にも伝わる形で示す（DP-3.2）。"""
    mark = ' aria-current="true"' if current else ""
    return f'<a href="{href}"{mark}>{html.escape(label)}</a>'


def controls(scopes: list[str], scope: str, sort: str) -> str:
    """並べ替えと絞り込み。上部に固定し、一覧をたどっている間も選択肢が見えるようにする。"""
    sort_links = "".join(
        link(page_name(scope, key), label, key == sort) for key, label in SORTS
    )
    scope_links = "".join(
        link(page_name(candidate, sort), scope_label(candidate), candidate == scope)
        for candidate in scopes
    )
    return (
        '<nav class="controls">'
        f"<div><b>並べ替え</b>{sort_links}</div>"
        f"<div><b>月で絞る</b>{scope_links}</div>"
        '<div><b></b><a href="search.html">検索</a></div>'
        "</nav>"
    )


# 列の識別子・見出し・数値かどうか。見出しとセルを同じ定義から作り、ずれないようにする
COLUMNS = [
    ("rank", "順位", True),
    ("delta", "変動", True),
    ("title", "曲 / アーティスト", False),
    ("first", "初回", True),
    ("peak", "最高", True),
    ("days", "在籍", True),
]


def delta_kind(delta: str) -> str:
    """変動の種類。骨組みでは見た目に使わないが、様式を差し替えるときの取っ掛かりになる。"""
    if delta.startswith("+"):
        return "up"
    if delta.startswith("-"):
        return "down"
    return {"=": "flat", "初": "debut", "再": "reentry", "—": "out"}.get(delta, "none")


def table(tracks: list[dict]) -> str:
    head = "".join(
        f'<th class="c-{key}{" num" if numeric else ""}">{html.escape(label)}</th>'
        for key, label, numeric in COLUMNS
    )
    rows = []
    for track in tracks:
        values = {
            "rank": str(track["rank"]) if track["rank"] is not None else "—",
            "delta": html.escape(track["delta"]),
            "title": (
                f'{html.escape(track["name"])}<span>{html.escape(track["artist_name"])}</span>'
            ),
            "first": track["first_charted"] or "—",
            "peak": str(track["peak"]) if track["peak"] is not None else "—",
            "days": str(track["total_days"]),
        }
        cells = []
        for key, label, numeric in COLUMNS:
            # data-label は、狭い画面で見出し行を畳む様式に切り替えたときに使う
            extra = f' data-kind="{delta_kind(track["delta"])}"' if key == "delta" else ""
            cells.append(
                f'<td class="c-{key}{" num" if numeric else ""}"'
                f' data-label="{html.escape(label)}"{extra}>{values[key]}</td>'
            )
        rows.append(f'<tr>{"".join(cells)}</tr>')
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def list_page(
    data: dict, scopes: list[str], scope: str, sort: str, extra_css: str = "", note: str = ""
) -> str:
    tracks = [t for t in data["tracks"] if scope == "all" or scope in t["months"]]
    tracks = sorted_tracks(tracks, sort)
    latest = data["dates"][-1] if data["dates"] else "—"
    banner = f'<p class="note">{html.escape(note)}</p>' if note else ""
    body = f"""<header>
  <h1>{html.escape(SITE_TITLE)}</h1>
  <p class="summary">最新の観測日 {latest} ／ 観測 {len(data["dates"])}日 ／ {len(tracks)}曲</p>
  {banner}
</header>
{controls(scopes, scope, sort)}
<main>
{table(tracks)}
  <p class="legend">変動は前日との差。<b>初</b> 初登場、<b>再</b> 再登場、<b>=</b> 変動なし、<b>—</b> ランク外。
  在籍は通算の日数で、間が空いても合計する。</p>
</main>"""
    return document(f"{SITE_TITLE}｜{scope_label(scope)}", body, extra_css=extra_css)


def search_page(data: dict) -> str:
    """検索結果は動的なので、索引をページに埋め込んでブラウザ側で絞り込む。

    別ファイルを読みに行かないのは、ローカルでファイルを直接開いたときにも動くようにするため。
    """
    index = [
        {
            "n": t["name"],
            "a": t["artist_name"],
            "r": t["rank"],
            "f": t["first_charted"] or "—",
            "p": t["peak"] if t["peak"] is not None else "—",
            "d": t["total_days"],
        }
        for t in sorted_tracks(data["tracks"], "rank")
    ]
    payload = json.dumps(index, ensure_ascii=False).replace("</", "<\\/")
    body = f"""<header>
  <h1>検索</h1>
  <p class="summary"><a href="index.html">一覧へ戻る</a></p>
</header>
<main>
  <label for="q">曲名またはアーティスト名</label>
  <input id="q" type="search" autocomplete="off">
  <p class="summary" id="count"></p>
  <div class="scroll"><table>
    <thead><tr><th class="num">順位</th><th>曲 / アーティスト</th>
    <th class="num">初回</th><th class="num">最高</th><th class="num">在籍</th></tr></thead>
    <tbody id="results"></tbody>
  </table></div>
</main>
<script type="application/json" id="index">{payload}</script>
<script>{SEARCH_SCRIPT}</script>"""
    return document(f"検索｜{SITE_TITLE}", body, noindex=True)


def build(out_dir: str | None = None) -> list[str]:
    directory = out_dir or OUT_DIR
    os.makedirs(directory, exist_ok=True)
    data = collect(song_stats.open_snapshots())
    scopes = ["all"] + data["months"]

    written = []
    for scope in scopes:
        for sort, _ in SORTS:
            path = os.path.join(directory, page_name(scope, sort))
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(list_page(data, scopes, scope, sort))
            written.append(path)

    path = os.path.join(directory, "search.html")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(search_page(data))
    written.append(path)
    return written


def main() -> int:
    written = build()
    for path in written:
        print(os.path.relpath(path, REPO_ROOT))
    print(f"{len(written)}ページを生成しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
