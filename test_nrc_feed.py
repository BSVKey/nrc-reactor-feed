import unittest
from pathlib import Path

import nrc_feed

FIX = Path(__file__).parent / "fixtures"


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.s = nrc_feed.summarize_status(nrc_feed.parse_status((FIX / "status-sample.txt").read_text()))

    def test_latest_and_counts(self):
        self.assertEqual(self.s["latest"], "2026-10-30")
        self.assertEqual(self.s["previous"], "2026-10-29")
        self.assertEqual(self.s["units"], 5)
        self.assertEqual(self.s["online"], 4)
        self.assertEqual(self.s["offline"], ["Callaway"])

    def test_changes_and_reduced(self):
        self.assertIn(("Oconee 1", 88, 87), self.s["changes"])
        self.assertIn(("Watts Bar 2", 97, 96), self.s["changes"])
        self.assertNotIn("Byron 2", [c[0] for c in self.s["changes"]])
        self.assertIn(("Oconee 1", 87), self.s["reduced"])

    def test_per_unit_history(self):
        rows = {u.unit: u for u in self.s["unit_rows"]}
        self.assertEqual(rows["Callaway"].offline_streak, 4)
        self.assertEqual(rows["Byron 2"].days_offline_365, 1)
        self.assertEqual(rows["Vogtle 2"].days_reduced_365, 1)

    def test_bad_file(self):
        with self.assertRaises(ValueError):
            nrc_feed.summarize_status(nrc_feed.parse_status("<html>Access Denied</html>"))


class EventTests(unittest.TestCase):
    def setUp(self):
        self.ev = nrc_feed.parse_events((FIX / "en-2026-10-30.txt").read_text())

    def test_numbers_and_categories(self):
        self.assertEqual([e.number for e in self.ev], ["55519", "58474", "58504"])
        self.assertEqual(self.ev[0].category, "Agreement State")
        self.assertEqual(self.ev[1].category, "Non-Agreement State")

    def test_fields(self):
        e = self.ev[1]
        self.assertEqual(e.facility, "US Army")
        self.assertEqual(e.state, "GA")
        self.assertTrue(e.fields["10 CFR Section"].startswith("30.50(b)(1)"))
        self.assertEqual(self.ev[0].fields["County"], "")

    def test_body_is_clean(self):
        self.assertIn("UNPLANNED CONTAMINATION", self.ev[1].text)
        self.assertNotIn("Agreement State\n", self.ev[1].text[-20:])
        self.assertNotIn("Page Last Reviewed", self.ev[2].text)

    def test_html_input(self):
        html = "<html><body><p>Power Reactor</p><p>Event Number: 1</p><p>Facility: X</p><p>Event Text</p><p>hi &amp; bye</p></body></html>"
        ev = nrc_feed.parse_events(html)
        self.assertEqual(ev[0].facility, "X")
        self.assertIn("hi & bye", ev[0].text)


class DashboardTests(unittest.TestCase):
    def test_render(self):
        s = nrc_feed.summarize_status(nrc_feed.parse_status((FIX / "status-sample.txt").read_text()))
        ev = nrc_feed.parse_events((FIX / "en-2026-10-30.txt").read_text())
        page = nrc_feed.render_dashboard(s, ev, {"status": "ab" * 32})
        self.assertIn("<title>NRC Reactor Feed</title>", page)
        self.assertIn("Callaway", page)
        self.assertIn("58474", page)
        self.assertIn("ab" * 32, page)
        self.assertNotIn("{{", page)

    def test_digest_stable(self):
        self.assertEqual(nrc_feed.digest("x"), nrc_feed.digest("x"))
        self.assertEqual(len(nrc_feed.digest("x")), 64)


if __name__ == "__main__":
    unittest.main()
