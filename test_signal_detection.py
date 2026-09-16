import unittest

from telegram_listener import extract_direction, is_collect_command, is_gold_activation


class SignalDetectionTests(unittest.TestCase):
    def test_case_insensitive_gold_signals(self):
        for text, direction in (
            ("Gold sell now", "SELL"),
            ("gold buy now", "BUY"),
            ("GOLD SELL NOW", "SELL"),
            ("xauusd buy", "BUY"),
            ("SELL GOLD NOW", "SELL"),
            ("Gold 🔴 sell now", "SELL"),
            ("GOLD\u200b BUY NOW", "BUY"),
        ):
            with self.subTest(text=text):
                self.assertTrue(is_gold_activation(text))
                self.assertEqual(extract_direction(text), direction)

    def test_ambiguous_or_unrelated_text_is_not_activation(self):
        self.assertFalse(is_gold_activation("GOLD BUY or SELL?"))
        self.assertFalse(is_gold_activation("SELL EURUSD now"))

    def test_collect_is_exact_case_insensitive_command(self):
        self.assertTrue(is_collect_command("collect"))
        self.assertTrue(is_collect_command("COLLECT PROFIT NOW"))
        self.assertFalse(is_collect_command("collection update"))


if __name__ == "__main__":
    unittest.main()
