import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from token_ledger.watch.fmt import human_age, human_ms


class HumanAgeTests(unittest.TestCase):
    def test_boundaries(self):
        cases = {0: "0 s", 45: "45 s", 59: "59 s", 60: "1 min", 2520: "42 min", 3599: "59 min", 3600: "1 h",
                 11520: "3 h 12 min", 86399: "23 h 59 min", 86400: "1 d", 2 * 86400 + 5 * 3600: "2 d 5 h",
                 29 * 86400: "29 d", 30 * 86400: "1 mo", 90 * 86400: "3 mo", 360 * 86400: "1 y",
                 14 * 30 * 86400: "1 y 2 mo"}
        for sec, want in cases.items():
            self.assertEqual(human_age(sec), want, sec)
        self.assertEqual(human_age(None), "0 s")

    def test_ms(self):
        self.assertEqual(human_ms(564), "564 ms")
        self.assertEqual(human_ms(999), "999 ms")
        self.assertEqual(human_ms(65000), "1 min")
        self.assertEqual(human_ms(5000), "5 s")
