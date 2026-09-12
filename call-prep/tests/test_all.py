"""Run:  python3 -m unittest discover -s call-prep/tests   (or from call-prep/: python3 -m unittest tests.test_all)"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
FIX = ROOT / "tests" / "fixtures"

import filter_events  # noqa: E402
import research  # noqa: E402
import build_email  # noqa: E402


class FilterEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((FIX / "events-2026-09-14.json").read_text())
        cls.res = filter_events.run(data["events"], "2026-09-14", "America/Los_Angeles")

    def test_external_selection(self):
        ids = [m["id"] for m in self.res["external"]]
        self.assertEqual(ids, ["knj1", "knj2", "laleva", "lifepharm"])

    def test_duplicate_booking_marked(self):
        by_id = {m["id"]: m for m in self.res["external"]}
        self.assertEqual(by_id["knj2"]["duplicate_of"], "knj1")
        self.assertNotIn("duplicate_of", by_id["knj1"])

    def test_company_extraction(self):
        by_id = {m["id"]: m for m in self.res["external"]}
        self.assertEqual(by_id["knj1"]["company"], "KNJ")                 # booking form
        self.assertEqual(by_id["lifepharm"]["company"], "LifePharm")      # booking form
        self.assertEqual(by_id["laleva"]["company"], "Laleva")            # EIN in form -> title

    def test_links_and_booking(self):
        m = {m["id"]: m for m in self.res["external"]}["lifepharm"]
        self.assertEqual(m["meeting_link"], "https://us02web.zoom.us/j/8185908611")
        self.assertEqual(m["booking_link"], "HubSpot meeting link (wfikhman)")
        self.assertEqual(m["phone"], "9492909995")
        self.assertEqual(m["duration_min"], 15)

    def test_exclusions(self):
        reasons = {m["id"]: m["reason"] for m in self.res["excluded"]}
        self.assertEqual(reasons["allday1"], "all-day event")
        self.assertEqual(reasons["pmhuddle"], "declined by owner")
        self.assertEqual(reasons["brandmgrs"], "declined by owner")
        self.assertEqual(reasons["cyinsyre"], "cancelled")
        self.assertEqual(reasons["nextday"], "not on target date")

    def test_internal_and_personal(self):
        kinds = {m["id"]: m["kind"] for m in self.res["internal"]}
        self.assertEqual(kinds["landrover"], "personal block")
        self.assertEqual(kinds["conway"], "personal block")
        self.assertEqual(kinds["lily"], "personal block")   # zoom link but organizer is self, no attendees
        self.assertEqual(kinds["sales"], "internal meeting")
        self.assertEqual(kinds["rundown"], "internal meeting")
        self.assertEqual({m["id"]: m["my_response"] for m in self.res["internal"]}["rundown"], "tentative")

    def test_timezone_normalised(self):
        m = {m["id"]: m for m in self.res["internal"]}["rundown"]   # given in -06:00
        self.assertTrue(m["start"].startswith("2026-09-14T08:30:00-07:00"))


class AmazonParsing(unittest.TestCase):
    def test_search_results(self):
        html = (FIX / "amazon-search.html").read_text()
        rows = research.parse_search(html, "Acme Naturals")
        self.assertEqual(len(rows), 5)
        by = {r["asin"]: r for r in rows}
        self.assertTrue(by["B0SPONSOR1"]["sponsored"])
        self.assertFalse(by["B0ACME0001"]["sponsored"])
        self.assertEqual(by["B0ACME0001"]["reviews"], 2310)
        self.assertEqual(by["B0ACME0001"]["rating"], 4.6)
        self.assertEqual(by["B0ACME0001"]["price"], 61.0)
        self.assertTrue(by["B0ACME0001"]["brand_match"])
        self.assertFalse(by["B0OTHER001"]["brand_match"])
        self.assertFalse(by["B0SPONSOR1"]["brand_match"])          # "Acme Glow" is not "Acme Naturals"
        self.assertIsNone(by["B0ACME0003"]["reviews"])             # missing -> None, never guessed
        self.assertEqual(by["B0ACME0001"]["storefront_url"], "https://www.amazon.com/stores/AcmeNaturals/page/ABC-123")
        self.assertEqual(research.find_storefront(html, "Acme Naturals"), "https://www.amazon.com/stores/AcmeNaturals/page/ABC-123")

    def test_product_3p(self):
        p = research.parse_product((FIX / "amazon-product-3p.html").read_text(), "B0ACME0001")
        self.assertEqual(p["sold_by"], "BargainBeautyCo")
        self.assertEqual(p["ships_from"], "BargainBeautyCo")
        self.assertFalse(p["fulfilled_by_amazon"])
        self.assertFalse(p["is_1p"])
        self.assertEqual(p["other_offers"], 4)
        self.assertEqual(p["reviews"], 2310)
        self.assertEqual(p["price"], 61.0)
        self.assertEqual(p["byline_brand"], "Acme Naturals")
        self.assertIn("no A+ content", p["quality_flags"])
        self.assertIn("no Brand Story", p["quality_flags"])
        self.assertIn("only 3 images", p["quality_flags"])
        self.assertIn("only 3 bullets", p["quality_flags"])

    def test_product_1p_new_layout(self):
        p = research.parse_product((FIX / "amazon-product-1p.html").read_text(), "B0BIG00001")
        self.assertEqual(p["sold_by"], "Amazon.com")
        self.assertTrue(p["is_1p"])
        self.assertTrue(p["fulfilled_by_amazon"])
        self.assertTrue(p["has_aplus"])
        self.assertTrue(p["has_brand_story"])
        self.assertIn("out of stock", p["quality_flags"])
        self.assertNotIn("no A+ content", p["quality_flags"])

    def test_aod_sellers(self):
        a = research.parse_aod((FIX / "amazon-aod.html").read_text())
        self.assertEqual(a["seller_count"], 3)
        self.assertEqual(a["distinct_sellers"], ["Acme Naturals LLC", "BargainBeautyCo", "DiscountDermDeals"])
        self.assertEqual(a["offers"][1]["price"], 58.75)

    def test_blocked_detection(self):
        self.assertTrue(research.Fetcher.looks_blocked("<html>Enter the characters you see below ... api-services-support@amazon.com"))
        self.assertFalse(research.Fetcher.looks_blocked((FIX / "amazon-search.html").read_text()))


class DTCParsing(unittest.TestCase):
    def test_platform(self):
        self.assertEqual(research.detect_platform((FIX / "shopify-home.html").read_text()), "Shopify")
        self.assertEqual(research.detect_platform("<script src='/wp-content/plugins/woocommerce/x.js'>"), "WooCommerce")
        self.assertEqual(research.detect_platform("<html>hello</html>"), "custom / unknown")

    def test_where_to_buy_and_amazon_link(self):
        home = (FIX / "shopify-home.html").read_text()
        w = research.where_to_buy(home, "https://acme.com")
        self.assertTrue(w["found"])
        self.assertEqual(w["url"], "https://acme.com/pages/where-to-buy")
        self.assertIsNone(research.links_to_amazon(home))
        self.assertEqual(research.links_to_amazon((FIX / "wtb.html").read_text()), "https://www.amazon.com/stores/AcmeNaturals/page/ABC-123")

    def test_shopify_products(self):
        prods = research.shopify_products((FIX / "shopify-products.json").read_text(), "https://acme.com", 5)
        self.assertEqual([p["title"] for p in prods], ["Bakuchiol Retinol Serum", "Gentle Milky Cleanser", "Clarify and Brighten Peeling Mask"])
        self.assertEqual(prods[0]["price"], 89.0)
        self.assertEqual(prods[1]["price"], 38.0)
        self.assertEqual(prods[1]["price_max"], 64.0)
        self.assertEqual(prods[0]["url"], "https://acme.com/products/bakuchiol-retinol-serum")

    def test_jsonld(self):
        html = '<script type="application/ld+json">{"@type":"Product","name":"Thing","url":"/p/thing","offers":{"price":"12.50","availability":"http://schema.org/InStock"}}</script>'
        p = research.jsonld_products(html, "https://x.com", 5)
        self.assertEqual(p[0]["price"], 12.5)
        self.assertEqual(p[0]["url"], "https://x.com/p/thing")
        self.assertTrue(p[0]["available"])


class PriceComparison(unittest.TestCase):
    def test_gap_detection(self):
        dtc = research.shopify_products((FIX / "shopify-products.json").read_text(), "https://acme.com", 5)
        amz = research.parse_search((FIX / "amazon-search.html").read_text(), "Acme Naturals")
        cmp = research.compare_prices(dtc, amz)
        by = {c["dtc_title"]: c for c in cmp}
        self.assertIn("Bakuchiol Retinol Serum", by)
        self.assertEqual(by["Bakuchiol Retinol Serum"]["amazon_price"], 61.0)
        self.assertEqual(by["Bakuchiol Retinol Serum"]["gap_pct"], -31.5)
        self.assertTrue(by["Bakuchiol Retinol Serum"]["flag"].startswith("amazon cheaper"))
        self.assertIn("Gentle Milky Cleanser", by)
        self.assertEqual(by["Gentle Milky Cleanser"]["flag"], "amazon cheaper (possible MAP / reseller undercut)")
        self.assertIn("Clarify and Brighten Peeling Mask", by)
        self.assertEqual(by["Clarify and Brighten Peeling Mask"]["flag"], "parity")

    def test_status_lines(self):
        rep = research.Report(brand="Acme Naturals", site=None)
        rep.amazon = {"brand_matched_results": 0}
        self.assertIn("NOT on Amazon", research.build_status(rep))
        rep.amazon = {"brand_matched_results": 3, "storefront_url": "https://www.amazon.com/stores/x", "top_listing": {"sold_by": "BargainBeautyCo", "is_1p": False}, "all_offers": {"seller_count": 3}}
        s = research.build_status(rep)
        self.assertIn("does NOT control the buy box", s)
        self.assertIn("3 distinct seller(s)", s)
        self.assertIn("Storefront", s)
        rep.amazon = {"brand_matched_results": 3, "top_listing": {"sold_by": "Acme Naturals LLC", "is_1p": False}, "all_offers": {}}
        self.assertIn("controls the listing", research.build_status(rep))
        rep.amazon = {"brand_matched_results": 3, "top_listing": {"sold_by": "Amazon.com", "is_1p": True}}
        self.assertIn("1P", research.build_status(rep))
        rep.amazon = {"brand_matched_results": None}
        self.assertIn("unverified", research.build_status(rep))


class EndToEndOffline(unittest.TestCase):
    """Whole research() pipeline against the fixture manifest (no network)."""

    def test_full_card(self):
        rep = research.research("Acme Naturals", "acmenaturals.com", deep=2, fixture_dir=str(FIX))
        self.assertEqual(rep.dtc["platform"], "Shopify")
        self.assertTrue(rep.dtc["where_to_buy"]["found"])
        self.assertEqual(rep.dtc["site_links_to_amazon"], "https://www.amazon.com/stores/AcmeNaturals/page/ABC-123")
        self.assertEqual(len(rep.dtc["products"]), 3)
        self.assertEqual(rep.amazon["brand_matched_results"], 3)
        self.assertEqual(rep.amazon["sponsored_brand_matched"], 0)
        self.assertEqual(rep.amazon["storefront_url"], "https://www.amazon.com/stores/AcmeNaturals/page/ABC-123")
        self.assertEqual([r["asin"] for r in rep.amazon["top_by_reviews"]], ["B0ACME0001", "B0ACME0002"])
        self.assertEqual(rep.amazon["avg_rating"], 4.5)
        self.assertEqual(rep.amazon["top_listing"]["sold_by"], "BargainBeautyCo")
        self.assertEqual(rep.amazon["all_offers"]["seller_count"], 3)
        self.assertIn("does NOT control the buy box", rep.status_line)
        self.assertIn("3 distinct seller(s)", rep.status_line)
        gaps = {g["dtc_title"]: g for g in rep.price_comparison}
        self.assertEqual(gaps["Bakuchiol Retinol Serum"]["gap_pct"], -31.5)
        self.assertTrue(any("B0ACME0001: no A+ content" == f for f in rep.amazon["quality_flags"]))
        self.assertTrue(any(u.startswith("https://www.amazon.com/dp/B0ACME0001") for u in rep.sources))
        self.assertEqual(rep.failures, [])

    def test_blocked_amazon_degrades(self):
        import tempfile, os, json as _json
        with tempfile.TemporaryDirectory() as d:
            _json.dump({"https://www.amazon.com/s?k=Nobody": "blocked.html"}, open(os.path.join(d, "manifest.json"), "w"))
            open(os.path.join(d, "blocked.html"), "w").write("<html>Enter the characters you see below api-services-support@amazon.com</html>")
            rep = research.research("Nobody", None, fixture_dir=d)
        self.assertIn("unverified", rep.status_line)
        self.assertTrue(any("blocked" in f for f in rep.failures))
        self.assertTrue(any("no DTC site" in f for f in rep.failures))


class EmailBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = json.loads((FIX / "brief.json").read_text())

    def test_subject(self):
        self.assertEqual(build_email.subject_for(self.brief), "Call Prep — Mon, Sep 14 — 2 meetings")
        self.assertEqual(build_email.subject_for({**self.brief, "mode": "supplement", "meetings": self.brief["meetings"][:1]}), "Call Prep Supplement — Mon, Sep 14 — 1 meeting")

    def test_chronological_order(self):
        html = build_email.render_html(self.brief)
        self.assertLess(html.index(">KNJ<"), html.index(">Laleva Natural<"))

    def test_links_and_flags(self):
        html = build_email.render_html(self.brief)
        self.assertIn('href="https://www.amazon.com/s?k=Laleva+Natural"', html)
        self.assertIn('href="https://app.hubspot.com/contacts/22483434/record/0-1/537864439526"', html)
        self.assertIn("[unavailable]: amazon", html)
        self.assertIn("Internal:</b> 11:00 Sales Meeting", html)
        self.assertIn("also booked 10:15 AM", html)
        self.assertIn("Data problems this run", html)

    def test_escaping(self):
        b = {**self.brief, "summary": ["<script>alert(1)</script>"]}
        self.assertIn("&lt;script&gt;", build_email.render_html(b))

    def test_empty_day(self):
        b = {"date": "2026-09-15", "summary": ["No external meetings today."], "meetings": [], "internal": ["9:00 Standup"]}
        html = build_email.render_html(b)
        self.assertIn("No external meetings today", html)
        self.assertIn("9:00 Standup", html)
        self.assertEqual(build_email.subject_for(b), "Call Prep — Tue, Sep 15 — 0 meetings")

    def test_text_version(self):
        txt = build_email.render_text(self.brief)
        self.assertIn("## 8:45 AM-9:00 AM  KNJ", txt)
        self.assertIn("HISTORY: Discovery 9/10", txt)


if __name__ == "__main__":
    unittest.main()
