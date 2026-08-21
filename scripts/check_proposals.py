#!/usr/bin/env python3
"""proposals/ に置かれた提案ファイルの書式を検査する。

提案は「判断を人間に預けたまま次へ進む」ための記録なので、節が欠けていると
後から読んでも判断できない。書き切らずに進むことを防ぐのがこのテストの役目。

  python scripts/test_proposals.py
"""

import glob
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPOSALS_DIR = os.path.join(REPO_ROOT, "proposals")

# 案内用のファイルは提案そのものではないので検査から外す
NON_PROPOSALS = {"README.md", "TEMPLATE.md"}

REQUIRED_SECTIONS = [
    "## 何に迷ったか",
    "## 選択肢",
    "## 推奨案とその理由",
    "## 保留による影響範囲",
]

FILENAME_PATTERN = re.compile(r"^\d{3}-[a-z0-9]+(-[a-z0-9]+)*\.md$")
STATUS_PATTERN = re.compile(r"^- 状態: *(未決|採用|却下)")


def proposal_paths() -> list[str]:
    if not os.path.isdir(PROPOSALS_DIR):
        return []
    return [
        path
        for path in sorted(glob.glob(os.path.join(PROPOSALS_DIR, "*.md")))
        if os.path.basename(path) not in NON_PROPOSALS
    ]


class TestProposalFormat(unittest.TestCase):
    """提案が1件も無くても通る。あるものだけを検査する。"""

    def test_filenames_are_numbered(self):
        for path in proposal_paths():
            name = os.path.basename(path)
            with self.subTest(name=name):
                self.assertRegex(
                    name,
                    FILENAME_PATTERN,
                    "提案は NNN-kebab-case-slug.md の形にする",
                )

    def test_numbers_are_unique(self):
        numbers = [os.path.basename(path)[:3] for path in proposal_paths()]
        self.assertEqual(len(numbers), len(set(numbers)), "連番が重複している")

    def test_required_sections_are_present(self):
        for path in proposal_paths():
            body = open(path, encoding="utf-8").read()
            for section in REQUIRED_SECTIONS:
                with self.subTest(name=os.path.basename(path), section=section):
                    self.assertIn(section, body, f"{section} が無い")

    def test_sections_are_not_empty(self):
        """見出しだけ置いて中身が空、という書きかけを弾く。"""
        for path in proposal_paths():
            lines = open(path, encoding="utf-8").read().splitlines()
            for section in REQUIRED_SECTIONS:
                # 見出しの次の行から、次の見出しが来るまでを中身とみなす
                body = []
                for line in lines[lines.index(section) + 1 :]:
                    if line.startswith("## "):
                        break
                    if line.strip():
                        body.append(line)
                with self.subTest(name=os.path.basename(path), section=section):
                    self.assertTrue(body, f"{section} の中身が空")

    def test_status_line_is_valid(self):
        for path in proposal_paths():
            lines = open(path, encoding="utf-8").read().splitlines()
            with self.subTest(name=os.path.basename(path)):
                self.assertTrue(
                    any(STATUS_PATTERN.match(line) for line in lines),
                    "「- 状態: 未決 / 採用 / 却下」の行が要る",
                )

    def test_listed_in_the_index(self):
        """一覧に載っていない提案は埋もれるので、README への記載を必須にする。"""
        index_path = os.path.join(PROPOSALS_DIR, "README.md")
        if not os.path.exists(index_path):
            self.skipTest("proposals/README.md が無い")
        index = open(index_path, encoding="utf-8").read()
        for path in proposal_paths():
            name = os.path.basename(path)
            with self.subTest(name=name):
                self.assertIn(name, index, f"{name} が proposals/README.md の一覧に無い")


class TestTemplate(unittest.TestCase):
    def test_template_has_every_required_section(self):
        """雛形が欠けていると、写した提案も欠ける。"""
        path = os.path.join(PROPOSALS_DIR, "TEMPLATE.md")
        if not os.path.exists(path):
            self.skipTest("proposals/TEMPLATE.md が無い")
        body = open(path, encoding="utf-8").read()
        for section in REQUIRED_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
