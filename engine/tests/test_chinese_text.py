from __future__ import annotations

import unittest

from engine.utils.chinese_text import (
    check_chinese_brackets,
    count_chinese_chars,
    list_sentence_start_repetition,
    paragraph_stats,
    split_chinese_sentences,
    surprise_word_density,
    vague_word_density,
)


class ChineseTextUtilsTests(unittest.TestCase):
    def test_split_chinese_sentences_handles_major_punctuation_and_paragraphs(self) -> None:
        text = "第一句。第二句！\n\n第三句……第四句？第五句；"
        sentences = split_chinese_sentences(text)
        self.assertEqual(sentences, ["第一句。", "第二句！", "第三句……", "第四句？", "第五句；"])

    def test_count_chinese_chars_ignores_english_digits_and_punctuation(self) -> None:
        text = "林七在A3区看见了旧账册。"
        self.assertEqual(count_chinese_chars(text), 9)

    def test_check_chinese_brackets_supports_chinese_and_english_pairs(self) -> None:
        balanced = check_chinese_brackets("“林七”走进《旧仓库》（雨夜）。")
        self.assertTrue(balanced["balanced"])
        unbalanced = check_chinese_brackets("“林七走进《旧仓库》")
        self.assertFalse(unbalanced["balanced"])
        self.assertTrue(unbalanced["mismatches"])

    def test_paragraph_stats_reports_uniformity(self) -> None:
        text = "林七在雨里停住脚步。\n\n他听见仓库深处传来铁链碰撞声。"
        stats = paragraph_stats(text)
        self.assertEqual(stats["paragraph_count"], 2)
        self.assertGreater(stats["avg_paragraph_chars"], 0)
        self.assertIn("uniformity", stats)

    def test_vague_and_surprise_density_use_per_thousand_scale(self) -> None:
        text = "林七似乎意识到，自己大概已经走进陷阱。忽然，门后竟然传来一声冷笑。"
        self.assertGreater(vague_word_density(text), 0.0)
        self.assertGreater(surprise_word_density(text), 0.0)

    def test_list_sentence_start_repetition_detects_three_sentence_runs(self) -> None:
        text = "林七抬头看雨。林七按住门栓。林七听见脚步。沈砚没有回头。"
        result = list_sentence_start_repetition(text)
        self.assertTrue(result["detected"])
        self.assertEqual(result["count"], 3)


if __name__ == "__main__":
    unittest.main()
