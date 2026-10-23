import unittest
import os
import tempfile
from unittest.mock import patch

import GameSummary as bot


class TeamMatchingTests(unittest.TestCase):
    def assert_match(self, title, teams, expected_team):
        matched, team = bot.is_matching_team(title, teams)
        self.assertTrue(matched)
        self.assertEqual(team, expected_team)

    def assert_no_match(self, title, teams):
        matched, team = bot.is_matching_team(title, teams)
        self.assertFalse(matched)
        self.assertIsNone(team)

    def test_exact_team_matches(self):
        self.assert_match(
            "خلاصه بازی استقلال 2 - پرسپولیس 1",
            ["استقلال"],
            "استقلال",
        )
        self.assert_match(
            "خلاصه بازی لاتزیو 1 - 2 رم",
            ["رم"],
            "رم",
        )

    def test_team_name_is_not_a_substring_match(self):
        self.assert_no_match(
            "خلاصه بازی فولاد 1 - استقلال خوزستان 0",
            ["استقلال"],
        )
        self.assert_no_match(
            "خلاصه بازی کرمانشاه 2 - تهران 1",
            ["رم"],
        )

    def test_non_football_highlights_are_rejected(self):
        self.assert_no_match(
            "خلاصه بسکتبال رم - میلان",
            ["رم"],
        )
        self.assert_no_match(
            "خلاصه بازی بسکتبال رم 102 - میلان 98",
            ["رم"],
        )
        self.assert_no_match(
            "خلاصه بازی والیبال استقلال 3 - سپاهان 1",
            ["استقلال"],
        )

    def test_title_without_a_score_is_rejected(self):
        self.assert_no_match("حواشی تمرین استقلال", ["استقلال"])
        self.assert_no_match("خلاصه بازی استقلال - پرسپولیس", ["استقلال"])

    def test_persian_and_arabic_characters_are_normalized(self):
        self.assert_match(
            "خلاصه بازی تراكتور 1 - سپاهان 0",
            ["تراکتور"],
            "تراکتور",
        )


class AdminExperienceTests(unittest.TestCase):
    def test_team_name_validation(self):
        self.assertEqual(bot.validate_team_name("  آ اس رم  "), (True, "آ اس رم"))
        self.assertFalse(bot.validate_team_name("1")[0])
        self.assertFalse(bot.validate_team_name("استقلال،پرسپولیس")[0])

    def test_destructive_actions_require_confirmation(self):
        team_keyboard = bot.build_team_delete_confirm_keyboard(2)
        channel_keyboard = bot.build_channel_delete_confirm_keyboard(3)
        self.assertEqual(
            team_keyboard["inline_keyboard"][0][0]["callback_data"],
            "team_del_do:2",
        )
        self.assertEqual(
            channel_keyboard["inline_keyboard"][0][0]["callback_data"],
            "chan_del_do:3",
        )

    def test_environment_channel_can_be_disabled_and_reenabled(self):
        original_config_file = bot.CONFIG_FILE
        original_channels_file = bot.CHANNELS_FILE
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                bot.CONFIG_FILE = os.path.join(temp_dir, "config.json")
                bot.CHANNELS_FILE = os.path.join(temp_dir, "channels.json")
                with patch.dict(
                    os.environ,
                    {
                        "CHANNEL_ID": "-1001234567890",
                        "CHANNEL_2_ID": "",
                        "CHANNELS_JSON": "[]",
                    },
                ):
                    self.assertIsNotNone(
                        bot.find_channel(bot.load_channels(), "-1001234567890")
                    )
                    bot.set_channel_disabled("-1001234567890", True)
                    self.assertIsNone(
                        bot.find_channel(bot.load_channels(), "-1001234567890")
                    )
                    bot.set_channel_disabled("-1001234567890", False)
                    self.assertIsNotNone(
                        bot.find_channel(bot.load_channels(), "-1001234567890")
                    )
        finally:
            bot.CONFIG_FILE = original_config_file
            bot.CHANNELS_FILE = original_channels_file


if __name__ == "__main__":
    unittest.main()
