#!/usr/bin/env python3
"""Step 3: Amazon + DTC research for one brand. Standalone, testable, honest.

    python3 bin/research.py --brand "Dr. Squatch" --site drsquatch.com
    python3 bin/research.py --brand "Olipop" --site drinkolipop.com --json
    python3 bin/research.py --brand "LifePharm" --site lifepharm.com --deep 2 --pages 2

What it does, in order:
  1. DTC site: detect platform (Shopify / WooCommerce / BigCommerce / ...),
     pull product prices (Shopify exposes /products.json; others via JSON-LD),
     look for a "Where to Buy" page and whether it links to Amazon.
  2. Amazon search page for the brand: brand-matched results, sponsored vs
     organic, prices, ratings, review counts, storefront link.
  3. Top listing(s) by review count: buy box seller (1P/3P), other-offer
     count, distinct sellers via the all-offers panel, A+ / Brand Story,
     availability, image count, title length.
  4. Price comparison: DTC price vs Amazon price for matching titles.

Every number comes from a parsed page and carries the URL it came from.
Anything that could not be parsed is reported as "unverified" and listed
under `failures`. Nothing is estimated.

Fetching uses Playwright (headless Chromium) when installed, because Amazon
blocks plain HTTP clients; it falls back to `requests` otherwise. Pass
--no-browser to force the fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urljoin

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None
try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    print("missing dependency: pip install beautifulsoup4 lxml requests", file=sys.stderr)
    raise

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
UNVERIFIED = "unverified"
AMAZON = "https://www.amazon.com"
STOPWORDS = {"the", "and", "for", "with", "of", "a", "an", "in", "to", "by", "oz", "fl", "ml", "pack", "count", "ct"}


# --------------------------------------------------------------------------- fetching


@dataclass
class FetchResult:
    url: str
    status: str            # ok | blocked | error | http_<code>
    html: str | None = None
    note: str = ""


class Fetcher:
    """Playwright first (Amazon needs a real browser), requests as fallback."""

    def __init__(self, use_browser: bool = True, timeout: int = 25, verbose: bool = False, fixture_dir: str | None = None):
        self.timeout = timeout
        self.verbose = verbose
        self.cache: dict[str, FetchResult] = {}
        self.sources: list[str] = []
        self._pw = self._browser = self._ctx = None
        # Offline mode for tests/demos: <fixture_dir>/manifest.json maps URL -> file name.
        self.fixtures: dict[str, str] | None = None
        fixture_dir = fixture_dir or os.environ.get("RESEARCH_FIXTURE_DIR")
        if fixture_dir:
            self.fixture_dir = fixture_dir
            self.fixtures = json.load(open(os.path.join(fixture_dir, "manifest.json")))
        self.use_browser = use_browser and not self.fixtures and self._try_start_browser()

    def _try_start_browser(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._ctx = self._browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1366, "height": 900})
            return True
        except Exception as exc:  # browser missing, sandbox, etc.
            self.log(f"playwright unavailable ({exc.__class__.__name__}); using requests")
            return False

    def close(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                obj and obj.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [fetch] {msg}", file=sys.stderr)

    @staticmethod
    def looks_blocked(html: str) -> bool:
        low = html[:20000].lower()
        return any(s in low for s in ("api-services-support@amazon.com", "type the characters you see", "robot check", "captcha", "enter the characters you see below"))

    def get(self, url: str, kind: str = "html") -> FetchResult:
        if url in self.cache:
            return self.cache[url]
        self.sources.append(url)
        if self.fixtures is not None:
            res = self._get_fixture(url)
        else:
            res = self._get_browser(url) if (self.use_browser and kind == "html") else self._get_requests(url)
        if res.html and kind == "html" and self.looks_blocked(res.html):
            res = FetchResult(url, "blocked", None, "bot check / captcha page")
        self.log(f"{res.status} {url} {res.note}")
        self.cache[url] = res
        return res

    def _get_fixture(self, url: str) -> FetchResult:
        name = self.fixtures.get(url)
        if not name:
            return FetchResult(url, "http_404", None, "not in fixture manifest")
        return FetchResult(url, "ok", open(os.path.join(self.fixture_dir, name), encoding="utf-8").read())

    def _get_requests(self, url: str) -> FetchResult:
        if requests is None:
            return FetchResult(url, "error", None, "requests not installed")
        try:
            r = requests.get(url, headers=HEADERS, timeout=self.timeout, allow_redirects=True)
        except Exception as exc:
            return FetchResult(url, "error", None, f"{exc.__class__.__name__}: {str(exc)[:120]}")
        if r.status_code != 200:
            return FetchResult(url, f"http_{r.status_code}", r.text if r.status_code < 500 else None)
        return FetchResult(url, "ok", r.text)

    def _get_browser(self, url: str) -> FetchResult:
        try:
            page = self._ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            # Amazon sometimes shows a "Continue shopping" interstitial before the real page.
            try:
                btn = page.locator("button:has-text('Continue shopping')")
                if btn.count():
                    btn.first.click(timeout=3000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            time.sleep(1.0)
            html = page.content()
            page.close()
            return FetchResult(url, "ok", html)
        except Exception as exc:
            return FetchResult(url, "error", None, f"{exc.__class__.__name__}: {str(exc)[:120]}")


# --------------------------------------------------------------------------- helpers


def norm_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS and len(t) > 1}


def collapsed(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def brand_matches(brand: str, title: str) -> bool:
    if not title:
        return False
    if collapsed(brand) and collapsed(brand) in collapsed(title):
        return True
    toks = sorted(norm_tokens(brand), key=len, reverse=True)
    return bool(toks) and toks[0] in norm_tokens(title)


def parse_money(text: str) -> float | None:
    m = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def parse_int(text: str) -> int | None:
    m = re.search(r"([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def parse_rating(text: str) -> float | None:
    m = re.search(r"(\d(?:\.\d)?)\s*out of\s*5", text or "")
    return float(m.group(1)) if m else None


def title_similarity(a: str, b: str) -> float:
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb))


# --------------------------------------------------------------------------- Amazon parsing


def parse_search(html: str, brand: str) -> list[dict]:
    """Parse an Amazon search results page into result dicts."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for card in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = card.get("data-asin", "").strip()
        if not asin:
            continue
        title_el = card.select_one("h2 span") or card.select_one("h2")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        price_el = card.select_one("span.a-price > span.a-offscreen")
        rating_el = card.select_one("span.a-icon-alt") or card.select_one('span[aria-label*="out of 5"]')
        reviews_el = (
            card.select_one('span[aria-label][class*="s-underline-text"]')
            or card.select_one('a[href*="customerReviews"] span')
            or card.select_one("span.s-underline-text")
        )
        sponsored = bool(
            card.select_one(".puis-sponsored-label-text")
            or card.select_one('[data-component-type="sp-sponsored-result"]')
            or re.search(r"\bSponsored\b", card.get_text(" ", strip=True)[:200])
        )
        store = card.select_one('a[href*="/stores/"]')
        out.append(
            {
                "asin": asin,
                "title": title,
                "url": f"{AMAZON}/dp/{asin}",
                "price": parse_money(price_el.get_text()) if price_el else None,
                "rating": parse_rating(rating_el.get_text() if rating_el and rating_el.get_text(strip=True) else (rating_el.get("aria-label", "") if rating_el else "")),
                "reviews": parse_int(reviews_el.get("aria-label") or reviews_el.get_text()) if reviews_el else None,
                "sponsored": sponsored,
                "brand_match": brand_matches(brand, title),
                "storefront_url": urljoin(AMAZON, store["href"].split("?")[0]) if store and store.get("href") else None,
            }
        )
    return out


def find_storefront(html: str, brand: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select('a[href*="/stores/"]'):
        href = a.get("href", "")
        text = a.get_text(" ", strip=True)
        if brand_matches(brand, text) or brand_matches(brand, href):
            return urljoin(AMAZON, href.split("?")[0])
    return None


def parse_product(html: str, asin: str) -> dict:
    """Parse a product detail page for ownership + listing-quality signals."""
    soup = BeautifulSoup(html, "lxml")
    text = lambda sel: (soup.select_one(sel).get_text(" ", strip=True) if soup.select_one(sel) else "")

    title = text("#productTitle")
    byline = text("#bylineInfo")
    store_link = soup.select_one('#bylineInfo[href*="/stores/"], #bylineInfo a[href*="/stores/"]')
    byline_brand = re.sub(r"^(Visit the|Brand:)\s*", "", byline).replace(" Store", "").strip() or None

    merchant = text("#merchantInfo") or text("#merchant-info") or text("#sellerProfileTriggerId")
    sold_by = ships_from = None
    m = re.search(r"Ships from and sold by\s*(.+?)(?:\s*\.|$)", merchant, re.I)
    if m:  # classic 3P self-fulfilled wording
        sold_by = ships_from = m.group(1).strip()
    else:
        m = re.search(r"Sold by\s*:?\s*(.+?)(?:\s+and\s+Fulfilled|\s*\.|$)", merchant, re.I)
        if m:
            sold_by = m.group(1).strip()
        elif text("#sellerProfileTriggerId"):
            sold_by = text("#sellerProfileTriggerId")
        m = re.search(r"Ships from\s*:?\s*(.+?)(?:\s+Sold by|\s*\.|$)", merchant, re.I)
        if m:
            ships_from = m.group(1).strip()
    # Newer layout: offer-display feature rows.
    for row in soup.select("div.offer-display-feature-text, div[data-csa-c-content-id*='desktop-merchant-info'], div[data-csa-c-content-id*='desktop-ship-from']"):
        rt = row.get_text(" ", strip=True)
        if re.search(r"^Sold by", rt, re.I) or "desktop-merchant-info" in (row.get("data-csa-c-content-id") or ""):
            sold_by = sold_by or re.sub(r"^Sold by\s*:?\s*", "", rt, flags=re.I).strip()
        if re.search(r"^Ships from", rt, re.I) or "desktop-ship-from" in (row.get("data-csa-c-content-id") or ""):
            ships_from = ships_from or re.sub(r"^Ships from\s*:?\s*", "", rt, flags=re.I).strip()

    is_1p = bool(sold_by and re.fullmatch(r"amazon(\.com)?", sold_by.strip(), re.I))
    fulfilled_by_amazon = bool(ships_from and "amazon" in ships_from.lower())

    olp = text("#olpLinkWidget_feature_div") or text("#olp_feature_div") or text("#dynamic-aod-ingress-box")
    m = re.search(r"New\s*\((\d+)\)\s*from", olp) or re.search(r"(\d+)\s+(?:new|other)\s+(?:offers?|sellers?)", olp, re.I)
    other_offers = int(m.group(1)) if m else None

    price = parse_money(text("#corePrice_feature_div span.a-offscreen") or text("#corePriceDisplay_desktop_feature_div span.a-offscreen") or text("span.a-price span.a-offscreen"))
    rating = parse_rating(text("#acrPopover") or text("span[data-hook='rating-out-of-text']") or text("#averageCustomerReviews"))
    reviews = parse_int(text("#acrCustomerReviewText"))
    availability = text("#availability")
    images = len(soup.select("#altImages li.imageThumbnail, #altImages li.item"))
    aplus_el = soup.select_one("#aplus_feature_div, #aplus")
    has_aplus = bool(aplus_el and len(aplus_el.get_text(strip=True)) > 40)
    has_brand_story = bool(soup.select_one("#brandStory_feature_div .apm-brand-story-hero, #brandStory_feature_div [data-aplus-module], .apm-brand-story-carousel"))
    bullets = len(soup.select("#feature-bullets li"))

    flags: list[str] = []
    if not has_aplus:
        flags.append("no A+ content")
    if not has_brand_story:
        flags.append("no Brand Story")
    if title and len(title) < 60:
        flags.append(f"thin title ({len(title)} chars)")
    if images and images < 5:
        flags.append(f"only {images} images")
    if availability and re.search(r"unavailable|out of stock|temporarily", availability, re.I):
        flags.append("out of stock")
    if bullets and bullets < 4:
        flags.append(f"only {bullets} bullets")

    return {
        "asin": asin,
        "url": f"{AMAZON}/dp/{asin}",
        "title": title or None,
        "title_len": len(title) if title else None,
        "byline_brand": byline_brand,
        "storefront_url": urljoin(AMAZON, store_link["href"].split("?")[0]) if store_link and store_link.get("href") else None,
        "sold_by": sold_by,
        "ships_from": ships_from,
        "is_1p": is_1p if sold_by else None,
        "fulfilled_by_amazon": fulfilled_by_amazon if ships_from else None,
        "other_offers": other_offers,
        "price": price,
        "rating": rating,
        "reviews": reviews,
        "availability": availability or None,
        "image_count": images or None,
        "has_aplus": has_aplus,
        "has_brand_story": has_brand_story,
        "bullet_count": bullets or None,
        "quality_flags": flags,
    }


def parse_aod(html: str) -> dict:
    """Parse the all-offers panel (ajax fragment) into a seller list."""
    soup = BeautifulSoup(html, "lxml")
    offers: list[dict] = []
    boxes = [b for b in soup.select("div[id^='aod-']") if re.fullmatch(r"aod-(pinned-)?offer\d*", b.get("id", ""))]
    for box in boxes:
        seller = None
        for sel in ("[id*='soldBy'] a", "[id*='soldBy'] span.a-size-small:not(.a-color-tertiary)", "[id*='soldBy'] span"):
            el = box.select_one(sel)
            txt = el.get_text(" ", strip=True) if el else ""
            if txt and not re.fullmatch(r"sold by:?", txt, re.I):
                seller = txt
                break
        price_el = box.select_one("span.a-price span.a-offscreen")
        ships_el = box.select_one("[id*='shipsFrom'] span.a-size-small:not(.a-color-tertiary), [id*='shipsFrom'] a")
        if seller or price_el:
            offers.append({"seller": seller, "price": parse_money(price_el.get_text()) if price_el else None, "ships_from": ships_el.get_text(" ", strip=True) if ships_el else None})
    sellers = sorted({o["seller"] for o in offers if o["seller"]})
    return {"offers": offers, "distinct_sellers": sellers, "seller_count": len(sellers) if offers else None}


# --------------------------------------------------------------------------- DTC parsing


def detect_platform(html: str) -> str:
    low = (html or "").lower()
    if "cdn.shopify.com" in low or "myshopify.com" in low or "shopify.theme" in low:
        return "Shopify"
    if "woocommerce" in low or "wp-content/plugins/woocommerce" in low:
        return "WooCommerce"
    if "bigcommerce" in low or "cdn11.bigcommerce.com" in low:
        return "BigCommerce"
    if "squarespace" in low:
        return "Squarespace"
    if "wixstatic.com" in low or "wix.com" in low:
        return "Wix"
    if "salesforce commerce" in low or "demandware" in low:
        return "Salesforce Commerce Cloud"
    if "magento" in low or "mage/" in low:
        return "Magento"
    if "webflow" in low:
        return "Webflow"
    return "custom / unknown"


def where_to_buy(html: str, base: str) -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = a["href"]
        if re.search(r"where to buy|stockists?|retailers?|find (us|a store)|store locator|buy now", label + " " + href, re.I):
            return {"found": True, "url": urljoin(base, href), "label": label[:60]}
    return {"found": False, "url": None, "label": None}


def links_to_amazon(html: str) -> str | None:
    m = re.search(r'href="(https?://(?:www\.)?amazon\.com/[^"]*)"', html or "", re.I)
    return m.group(1) if m else None


def shopify_products(raw_json: str, base: str, limit: int) -> list[dict]:
    data = json.loads(raw_json)
    out = []
    for p in data.get("products", []):
        variants = [v for v in p.get("variants", []) if v.get("price")]
        if not variants:
            continue
        prices = [float(v["price"]) for v in variants]
        available = any(v.get("available", True) for v in variants)
        out.append(
            {
                "title": p.get("title"),
                "url": f"{base.rstrip('/')}/products/{p.get('handle')}",
                "price": min(prices),
                "price_max": max(prices),
                "available": available,
                "variants": len(variants),
            }
        )
        if len(out) >= limit:
            break
    return out


def jsonld_products(html: str, base: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html or "", "lxml")
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "ItemList":
                items.extend(e.get("item", e) for e in item.get("itemListElement", []) if isinstance(e, dict))
                continue
            if item.get("@type") not in ("Product",):
                continue
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price") or offers.get("lowPrice")
            if price is None:
                continue
            out.append({"title": item.get("name"), "url": urljoin(base, item.get("url") or offers.get("url") or ""), "price": float(price), "available": "InStock" in str(offers.get("availability", ""))})
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- comparison


def compare_prices(dtc: list[dict], amazon: list[dict], threshold: float = 0.05) -> list[dict]:
    matches = []
    for d in dtc:
        best, score = None, 0.0
        for a in amazon:
            if not a.get("brand_match") or a.get("price") is None:
                continue
            s = title_similarity(d["title"], a["title"])
            if s > score:
                best, score = a, s
        if best and score >= 0.5:
            gap = best["price"] - d["price"]
            pct = gap / d["price"] if d["price"] else 0
            matches.append(
                {
                    "dtc_title": d["title"],
                    "dtc_price": d["price"],
                    "dtc_url": d["url"],
                    "amazon_title": best["title"],
                    "amazon_price": best["price"],
                    "amazon_url": best["url"],
                    "gap": round(gap, 2),
                    "gap_pct": round(pct * 100, 1),
                    "flag": "amazon cheaper (possible MAP / reseller undercut)" if pct < -threshold else ("amazon pricier" if pct > threshold else "parity"),
                    "match_confidence": round(score, 2),
                }
            )
    return matches


# --------------------------------------------------------------------------- orchestration


@dataclass
class Report:
    brand: str
    site: str | None
    dtc: dict = field(default_factory=dict)
    amazon: dict = field(default_factory=dict)
    price_comparison: list = field(default_factory=list)
    status_line: str = ""
    failures: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


def research_dtc(f: Fetcher, site: str, brand: str, n_products: int, rep: Report) -> None:
    base = site if site.startswith("http") else f"https://{site}"
    home = f.get(base)
    dtc: dict[str, Any] = {"site": base, "platform": UNVERIFIED, "products": [], "where_to_buy": None, "site_links_to_amazon": None}
    if home.status != "ok" or not home.html:
        rep.failures.append(f"dtc homepage {home.status}: {home.note or base}")
        rep.dtc = dtc
        return
    # follow www redirect for later relative links
    dtc["platform"] = detect_platform(home.html)
    dtc["where_to_buy"] = where_to_buy(home.html, base)
    dtc["site_links_to_amazon"] = links_to_amazon(home.html)
    if dtc["where_to_buy"]["found"] and not dtc["site_links_to_amazon"]:
        wtb = f.get(dtc["where_to_buy"]["url"])
        if wtb.status == "ok" and wtb.html:
            dtc["site_links_to_amazon"] = links_to_amazon(wtb.html)

    products: list[dict] = []
    if dtc["platform"] == "Shopify":
        pj = f.get(f"{base.rstrip('/')}/products.json?limit=50", kind="json")
        if pj.status == "ok" and pj.html:
            try:
                products = shopify_products(pj.html, base, n_products)
                dtc["products_source"] = pj.url
            except Exception as exc:
                rep.failures.append(f"products.json parse error: {exc}")
        else:
            rep.failures.append(f"products.json {pj.status}")
    if not products:
        products = jsonld_products(home.html, base, n_products)
        if products:
            dtc["products_source"] = base
    if not products:
        rep.failures.append("dtc prices: no Shopify feed or JSON-LD products found (unverified)")
    dtc["products"] = products
    rep.dtc = dtc


def research_amazon(f: Fetcher, brand: str, pages: int, deep: int, rep: Report) -> None:
    az: dict[str, Any] = {
        "search_url": f"{AMAZON}/s?k={quote_plus(brand)}",
        "storefront_url": None,
        "results_on_page": None,
        "brand_matched_results": None,
        "sponsored_brand_matched": None,
        "catalog_size_note": UNVERIFIED,
        "top_by_reviews": [],
        "avg_rating": None,
        "top_listing": None,
        "all_offers": None,
        "quality_flags": [],
        "results": [],
    }
    all_results: list[dict] = []
    for page in range(1, pages + 1):
        url = az["search_url"] + (f"&page={page}" if page > 1 else "")
        res = f.get(url)
        if res.status != "ok" or not res.html:
            rep.failures.append(f"amazon search p{page} {res.status}: {res.note}")
            break
        parsed = parse_search(res.html, brand)
        if not parsed:
            rep.failures.append(f"amazon search p{page}: 0 results parsed (layout change or empty)")
        all_results.extend(parsed)
        az["storefront_url"] = az["storefront_url"] or find_storefront(res.html, brand)

    if not all_results:
        rep.amazon = az
        return

    matched = [r for r in all_results if r["brand_match"]]
    organic = [r for r in matched if not r["sponsored"]]
    az["results_on_page"] = len(all_results)
    az["brand_matched_results"] = len(matched)
    az["sponsored_brand_matched"] = len(matched) - len(organic)
    az["catalog_size_note"] = f"{len(matched)} brand-matched ASINs across {pages} search page(s) (approx. lower bound)"
    az["storefront_url"] = az["storefront_url"] or next((r["storefront_url"] for r in matched if r.get("storefront_url")), None)
    rated = [r["rating"] for r in matched if r.get("rating") is not None]
    az["avg_rating"] = round(sum(rated) / len(rated), 2) if rated else None
    with_reviews = sorted([r for r in matched if r.get("reviews") is not None], key=lambda r: r["reviews"], reverse=True)
    az["top_by_reviews"] = with_reviews[:3]
    az["results"] = matched

    targets = with_reviews[:deep] if with_reviews else matched[:deep]
    for i, t in enumerate(targets):
        pr = f.get(t["url"])
        if pr.status != "ok" or not pr.html:
            rep.failures.append(f"amazon product {t['asin']} {pr.status}: {pr.note}")
            continue
        prod = parse_product(pr.html, t["asin"])
        az["storefront_url"] = az["storefront_url"] or prod.get("storefront_url")
        az["quality_flags"].extend(f"{t['asin']}: {fl}" for fl in prod["quality_flags"])
        if i == 0:
            az["top_listing"] = prod
            aod = f.get(f"{AMAZON}/gp/product/ajax/ref=dp_aod_NEW_mbc?asin={t['asin']}&pc=dp&experienceId=aodAjaxMain")
            if aod.status == "ok" and aod.html:
                az["all_offers"] = {**parse_aod(aod.html), "source": aod.url}
            else:
                rep.failures.append(f"amazon all-offers {t['asin']} {aod.status}")
        else:
            az.setdefault("other_listings", []).append(prod)
    rep.amazon = az


def build_status(rep: Report) -> str:
    az, brand = rep.amazon, rep.brand
    if az.get("brand_matched_results") is None:
        return f"Amazon presence {UNVERIFIED} (search page could not be read)"
    if az["brand_matched_results"] == 0:
        return f"{brand} is NOT on Amazon (0 brand-matched results on page 1)"
    top = az.get("top_listing") or {}
    seller = top.get("sold_by")
    store = " with a Storefront (Brand Registry)" if az.get("storefront_url") else ", no Storefront found"
    if top.get("is_1p"):
        return f"On Amazon as 1P (Sold by Amazon){store}"
    if seller:
        controls = brand_matches(brand, seller)
        who = "the brand" if controls else f"a third-party reseller ({seller})"
        sellers = az.get("all_offers", {}) or {}
        n = sellers.get("seller_count")
        n_txt = f"; {n} distinct seller(s) on the top listing" if n is not None else ""
        verdict = "controls the listing" if controls else "does NOT control the buy box"
        return f"On Amazon, 3P; buy box held by {who}; {brand} {verdict}{n_txt}{store}"
    return f"On Amazon ({az['brand_matched_results']} brand-matched results){store}; buy box owner {UNVERIFIED}"


def print_card(rep: Report) -> None:
    d, a = rep.dtc, rep.amazon
    v = lambda x: UNVERIFIED if x in (None, "", []) else x
    print(f"\n==================== {rep.brand} ====================")
    print(f"STATUS: {rep.status_line}\n")
    print("AMAZON")
    print(f"  search:      {a.get('search_url')}")
    print(f"  storefront:  {v(a.get('storefront_url'))}")
    print(f"  catalog:     {v(a.get('catalog_size_note'))}")
    print(f"  avg rating:  {v(a.get('avg_rating'))}   sponsored brand results: {v(a.get('sponsored_brand_matched'))}")
    for r in a.get("top_by_reviews", []):
        print(f"  top:         {r['reviews']:,} reviews | {r['rating']}★ | ${r['price']} | {r['title'][:70]}  <{r['url']}>")
    top = a.get("top_listing")
    if top:
        print(f"  top listing: sold by {v(top.get('sold_by'))} | ships from {v(top.get('ships_from'))} | 1P={v(top.get('is_1p'))} | other offers: {v(top.get('other_offers'))}")
        ao = a.get("all_offers") or {}
        print(f"  sellers:     {v(ao.get('seller_count'))} distinct {ao.get('distinct_sellers') or ''}")
    if a.get("quality_flags"):
        print("  flags:       " + "; ".join(a["quality_flags"]))
    print("\nDTC")
    print(f"  site:        {v(d.get('site'))}   platform: {v(d.get('platform'))}")
    wtb = d.get("where_to_buy") or {}
    print(f"  where2buy:   {wtb.get('url') if wtb.get('found') else 'none found'}   links to amazon: {v(d.get('site_links_to_amazon'))}")
    for p in d.get("products", []):
        print(f"  ${p['price']:<8} {p['title'][:60]}  <{p['url']}>")
    print("\nPRICE GAPS")
    if not rep.price_comparison:
        print("  none matched (needs both a DTC price and a brand-matched Amazon price for the same item)")
    for m in rep.price_comparison:
        print(f"  {m['flag']:<48} DTC ${m['dtc_price']} vs AMZ ${m['amazon_price']} ({m['gap_pct']:+}%)  {m['dtc_title'][:40]}")
        print(f"      {m['dtc_url']}\n      {m['amazon_url']}")
    if rep.failures:
        print("\nUNAVAILABLE / UNVERIFIED")
        for x in rep.failures:
            print(f"  - {x}")
    print()


def research(brand: str, site: str | None, *, pages: int = 1, deep: int = 1, n_products: int = 5, use_browser: bool = True, verbose: bool = False, fixture_dir: str | None = None) -> Report:
    rep = Report(brand=brand, site=site)
    f = Fetcher(use_browser=use_browser, verbose=verbose, fixture_dir=fixture_dir)
    try:
        if site:
            research_dtc(f, site, brand, n_products, rep)
        else:
            rep.failures.append("no DTC site supplied (pass --site)")
        research_amazon(f, brand, pages, deep, rep)
        rep.price_comparison = compare_prices(rep.dtc.get("products", []), rep.amazon.get("results", []))
        rep.status_line = build_status(rep)
        rep.sources = f.sources
    finally:
        f.close()
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", required=True)
    ap.add_argument("--site", help="brand's own site, e.g. drsquatch.com")
    ap.add_argument("--pages", type=int, default=1, help="Amazon search pages to read (default 1)")
    ap.add_argument("--deep", type=int, default=1, help="product pages to open, by review count (default 1)")
    ap.add_argument("--products", type=int, default=5, help="DTC products to price (default 5)")
    ap.add_argument("--no-browser", action="store_true", help="skip Playwright, use plain HTTP")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--out", help="write JSON report here")
    ap.add_argument("--fixtures", help="offline mode: directory with manifest.json (URL -> file); also RESEARCH_FIXTURE_DIR")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    rep = research(args.brand, args.site, pages=args.pages, deep=args.deep, n_products=args.products, use_browser=not args.no_browser, verbose=args.verbose, fixture_dir=args.fixtures)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rep.to_dict(), fh, indent=2)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
    else:
        print_card(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
