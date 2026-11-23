import unittest

from pipeline.hours import (
    OperatingHoursError,
    parse_hhmm,
    parse_hours_pair,
    is_open,
)


class TestParseHHMM(unittest.TestCase):

    def test_normal_time(self):
        self.assertEqual(parse_hhmm("0900"), 540)
        self.assertEqual(parse_hhmm("1830"), 1110)

    def test_extended_end_time(self):
        self.assertEqual(parse_hhmm("2400", is_end=True), 1440)
        self.assertEqual(parse_hhmm("2500", is_end=True), 1500)
        self.assertEqual(parse_hhmm("3100", is_end=True), 1860)

    def test_empty_value(self):
        self.assertIsNone(parse_hhmm(""))
        self.assertIsNone(parse_hhmm(None))

    def test_invalid_time(self):
        for value in ("1260", "2460", "25:00", "abcd"):
            with self.subTest(value=value):
                with self.assertRaises(OperatingHoursError):
                    parse_hhmm(value)

    def test_invalid_start_time(self):
        with self.assertRaises(OperatingHoursError):
            parse_hhmm("2400")


class TestParseHoursPair(unittest.TestCase):

    def test_normal_hours(self):
        self.assertEqual(
            parse_hours_pair("0900", "1800"),
            (540, 1080),
        )

    def test_overnight_hours(self):
        self.assertEqual(
            parse_hours_pair("2200", "0600"),
            (1320, 1800),
        )

    def test_extended_overnight_hours(self):
        self.assertEqual(
            parse_hours_pair("2200", "2500"),
            (1320, 1500),
        )

    def test_full_day_hours(self):
        self.assertEqual(
            parse_hours_pair("0000", "2400"),
            (0, 1440),
        )
        # 0000~0000을 24시간 운영으로 처리하는 현재 설계안
        self.assertEqual(
            parse_hours_pair("0000", "0000"),
            (0, 1440),
        )

    def test_both_values_empty(self):
        self.assertIsNone(parse_hours_pair("", ""))

    def test_only_one_value_empty(self):
        with self.assertRaises(OperatingHoursError):
            parse_hours_pair("0900", "")

        with self.assertRaises(OperatingHoursError):
            parse_hours_pair("", "1800")

    def test_duration_over_24_hours(self):
        with self.assertRaises(OperatingHoursError):
            parse_hours_pair("0000", "2500")

    def test_overnight_disabled(self):
        with self.assertRaises(OperatingHoursError):
            parse_hours_pair(
                "2200",
                "0600",
                allow_overnight=False,
            )


class TestIsOpen(unittest.TestCase):

    def test_inside_operating_hours(self):
        start, end = parse_hours_pair("0900", "1800")
        self.assertTrue(is_open(start, end, 600))

    def test_before_opening(self):
        start, end = parse_hours_pair("0900", "1800")
        self.assertFalse(is_open(start, end, 500))

    def test_closing_time_is_exclusive(self):
        start, end = parse_hours_pair("0900", "1800")
        self.assertFalse(is_open(start, end, 1080))

    def test_after_midnight(self):
        start, end = parse_hours_pair("2200", "2500")
        self.assertTrue(is_open(start, end, 1470))
        self.assertFalse(is_open(start, end, 1500))

    def test_unknown_hours(self):
        self.assertIsNone(is_open(None, None, 600))


if __name__ == "__main__":
    unittest.main()


from pipeline.hours import classify_hours_pair, is_open_with_policy


class TestHoursClassification(unittest.TestCase):

    def test_midnight_to_midnight_is_valid(self):
        result = classify_hours_pair("0000", "0000")
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.interval, (0, 1440))

    def test_midnight_to_2400_is_valid(self):
        result = classify_hours_pair("0000", "2400")
        self.assertEqual(result.status, "valid")

    def test_same_time_other_than_midnight_is_invalid(self):
        result = classify_hours_pair("0900", "0900")
        self.assertEqual(result.status, "invalid")

    def test_25_hour_duration_is_invalid(self):
        result = classify_hours_pair("0000", "2500")
        self.assertEqual(result.status, "invalid")

    def test_extended_overnight_is_valid(self):
        result = classify_hours_pair("2200", "2500")
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.interval, (1320, 1500))

    def test_missing_hours(self):
        self.assertEqual(
            classify_hours_pair("", "").status,
            "missing",
        )
        self.assertEqual(
            classify_hours_pair("0900", "").status,
            "missing",
        )

    def test_missing_policy(self):
        result = classify_hours_pair("", "")
        self.assertTrue(
            is_open_with_policy(result, 600, missing_policy="open")
        )
        self.assertFalse(
            is_open_with_policy(result, 600, missing_policy="closed")
        )

    def test_invalid_policy(self):
        result = classify_hours_pair("0000", "2500")
        self.assertFalse(
            is_open_with_policy(result, 600, invalid_policy="closed")
        )
        self.assertTrue(
            is_open_with_policy(result, 600, invalid_policy="open")
        )

    def test_invalid_monday_does_not_affect_valid_tuesday(self):
        monday = classify_hours_pair("0000", "2500")
        tuesday = classify_hours_pair("0900", "1800")

        monday_open = is_open_with_policy(
            monday, 600, invalid_policy="closed"
        )
        tuesday_open = is_open_with_policy(
            tuesday, 600, invalid_policy="closed"
        )

        self.assertFalse(monday_open)
        self.assertTrue(tuesday_open)
