#!/usr/bin/env python3
"""提案の案ごとにプレビューを生成する。

文章で3案を比べるより実物を見比べるほうが速く、判断も正確になるため、
提案には必ず対応するプレビューを用意する。出力は site/previews/。

  python scripts/build_previews.py

骨組みのマークアップは変えず、CSS だけを差し替えて案を作る。
そのため「プレビューでは良かったのに実装すると違う」が起きにくい。
比較の条件を揃えるため、どの案も同じ日・同じ並び順の一覧を出す。
"""

import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_site
import song_stats

REPO_ROOT = build_site.REPO_ROOT
OUT_DIR = os.path.join(build_site.OUT_DIR, "previews")

# 明暗どちらのテーマでも意味の強弱が保たれるかを見るため、両方ぶんを定義する（DP-5.7）
def direction_css(up: str, down: str, up_dark: str, down_dark: str) -> str:
    return f"""
[data-kind="up"] {{ color: {up}; }}
[data-kind="down"] {{ color: {down}; }}
@media (prefers-color-scheme: dark) {{
  [data-kind="up"] {{ color: {up_dark}; }}
  [data-kind="down"] {{ color: {down_dark}; }}
}}
"""


DENSITY_CSS = """
table {{ font-size: {font}; }}
th, td {{ padding: {padding}; line-height: {line}; }}
.title span {{ font-size: {sub}; }}
"""

# 狭い画面で見出しを畳み、各セルの前にラベルを出す。data-label を使う
STACK_CSS = """
@media (max-width: 40rem) {
  thead { display: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  tr { padding: 0.5rem 0; border-bottom: 1px solid; }
  td { border: 0; padding: 0.0625rem 0; white-space: normal; text-align: left; }
  td::before { content: attr(data-label) "  "; display: inline-block; min-width: 4rem; font-size: 0.75rem; }
  td.c-title { font-weight: bold; }
  td.c-title::before { content: ""; min-width: 0; }
  .scroll { overflow-x: visible; }
}
"""

# 狭い画面では優先度の低い列を落とす。順位と識別子は必ず残す（DP-3.7）
PRIORITY_CSS = """
@media (max-width: 40rem) {
  .c-first, .c-peak { display: none; }
}
"""

PROPOSALS = [
    {
        "id": "002",
        "title": "変動の配色",
        "options": [
            ("a", "案A 色を使わない（現状）", ""),
            (
                "b",
                "案B 上昇=赤 / 下降=青",
                direction_css("#c0392b", "#1f6feb", "#ff7b72", "#79b8ff"),
            ),
            (
                "c",
                "案C 上昇=赤 / 下降=緑",
                direction_css("#c0392b", "#1a7f37", "#ff7b72", "#56d364"),
            ),
            (
                "d",
                "案D 上昇=緑 / 下降=赤（西洋式）",
                direction_css("#1a7f37", "#c0392b", "#56d364", "#ff7b72"),
            ),
        ],
    },
    {
        "id": "003",
        "title": "一覧の情報密度",
        "options": [
            (
                "a",
                "案A 標準（現状）",
                DENSITY_CSS.format(
                    font="0.875rem", padding="0.375rem 0.5rem", line="1.5", sub="0.8125rem"
                ),
            ),
            (
                "b",
                "案B 詰めた",
                DENSITY_CSS.format(
                    font="0.8125rem", padding="0.125rem 0.5rem", line="1.25", sub="0.75rem"
                ),
            ),
            (
                "c",
                "案C ゆったり",
                DENSITY_CSS.format(
                    font="1rem", padding="0.75rem 0.5rem", line="1.6", sub="0.875rem"
                ),
            ),
        ],
    },
    {
        "id": "004",
        "title": "狭い画面での畳み方",
        "options": [
            ("a", "案A 横スクロール（現状）", ""),
            ("b", "案B 1曲1ブロックに積み上げる", STACK_CSS),
            ("c", "案C 優先度の低い列を落とす", PRIORITY_CSS),
        ],
    },
]

NOTE = "提案{id} のプレビュー — {label}。判断用の仮の見た目で、本番のサイトではありません。"


def preview_name(proposal_id: str, key: str) -> str:
    return f"{proposal_id}-{key}.html"


def build(out_dir: str | None = None) -> list[str]:
    directory = out_dir or OUT_DIR
    os.makedirs(directory, exist_ok=True)
    data = build_site.collect(song_stats.open_snapshots())
    scopes = ["all"] + data["months"]

    written = []
    for proposal in PROPOSALS:
        for key, label, css in proposal["options"]:
            page = build_site.list_page(
                data,
                scopes,
                "all",
                "rank",
                extra_css=css,
                note=NOTE.format(id=proposal["id"], label=label),
            )
            path = os.path.join(directory, preview_name(proposal["id"], key))
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(page)
            written.append(path)

    path = os.path.join(directory, "index.html")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(contents_page())
    written.append(path)
    return written


def contents_page() -> str:
    """プレビューの目次。案の切り替えを1画面から行えるようにする。"""
    sections = []
    for proposal in PROPOSALS:
        links = "".join(
            f'<li><a href="{preview_name(proposal["id"], key)}">{html.escape(label)}</a></li>'
            for key, label, _ in proposal["options"]
        )
        sections.append(
            f'<h2>提案{proposal["id"]}　{html.escape(proposal["title"])}</h2><ul>{links}</ul>'
        )
    body = f"""<header>
  <h1>提案プレビュー</h1>
  <p class="summary">判断用の仮の見た目。<a href="../index.html">骨組みの一覧へ</a></p>
</header>
<main>{"".join(sections)}
  <p class="legend">同じ日・同じ並び順の一覧に、案ごとの様式だけを当てている。
  狭い画面での挙動を見る場合は、ウィンドウ幅を 640px 以下にするか携帯で開く。</p>
</main>"""
    return build_site.document("提案プレビュー", body, noindex=True)


def main() -> int:
    written = build()
    for path in written:
        print(os.path.relpath(path, REPO_ROOT))
    print(f"{len(written)}ページを生成しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
