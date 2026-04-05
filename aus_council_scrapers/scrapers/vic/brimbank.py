from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from aus_council_scrapers.base import BaseScraper, ScraperReturn, register_scraper
from aus_council_scrapers.constants import EARLIEST_YEAR

# The static HTML served by the server uses /file/html with an alternate port.
# JavaScript in the browser rewrites these to /file/document on the standard port.
# We replicate that transformation so requests-based fetching gets the PDF URLs.
_HTML_LINK_RE = re.compile(
    r"(https://serviceapi\.brimbank\.vic\.gov\.au)(?::\d+)?(/CMServiceAPI/Record/\d+)/file/html"
)


@register_scraper
class BrimbankScraper(BaseScraper):
    def __init__(self):
        council = "brimbank"
        state = "VIC"
        base_url = "https://www.brimbank.vic.gov.au"
        super().__init__(council, state, base_url)

    def _normalise_href(self, href: str) -> str:
        """Convert static /file/html links to /file/document (the JS-rendered PDF URL)."""
        return _HTML_LINK_RE.sub(r"\1\2/file/document", href)

    def _get_years_filter(self) -> set[int] | None:
        for attr in ["years_filter", "years", "year_filter", "year"]:
            v = getattr(self, attr, None)
            if v is None:
                continue
            if isinstance(v, int):
                return {v}
            if isinstance(v, (list, tuple, set)) and all(isinstance(x, int) for x in v):
                return set(v)
        return None

    def _year_page_url(self, year: int) -> str:
        return f"{self.base_url}/about-council/your-council/agendas-and-minutes/council-meetings-{year}"

    def _parse_year_page(self, html: str, year: int) -> list[ScraperReturn]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[ScraperReturn] = []
        webpage_url = self._year_page_url(year)

        for h3 in soup.find_all("h3"):
            heading_text = h3.get_text(strip=True)
            date_match = re.search(self.date_regex, heading_text)
            if not date_match:
                continue

            raw_date = date_match.group()
            try:
                date = datetime.strptime(raw_date, "%d %B %Y").strftime("%Y-%m-%d")
            except ValueError:
                date = raw_date

            # Collect all links between this h3 and the next h3
            agenda_url = None
            minutes_url = None
            node = h3
            while True:
                node = node.find_next_sibling()
                if node is None or (getattr(node, "name", None) == "h3"):
                    break
                for a in node.find_all("a", href=True) if hasattr(node, "find_all") else []:
                    link_text = a.get_text(strip=True)
                    href = self._normalise_href(a["href"])
                    # Only consider document links (serviceapi, records API, or PDFs)
                    if not any(k in href for k in ["serviceapi", "records.brimbank", "ExternalLinkAPI", ".pdf"]):
                        continue
                    text_lower = link_text.lower()
                    if "minute" in text_lower and not minutes_url:
                        minutes_url = href
                    elif "agenda" in text_lower and not agenda_url:
                        agenda_url = href

            if not agenda_url and not minutes_url:
                continue

            name = heading_text.strip()

            results.append(
                ScraperReturn(
                    name=name,
                    date=date,
                    time=None,
                    webpage_url=webpage_url,
                    download_url=agenda_url or minutes_url,
                    agenda_url=agenda_url,
                    minutes_url=minutes_url,
                )
            )

        return results

    def scraper(self) -> list[ScraperReturn]:
        years_filter = self._get_years_filter()
        now = datetime.now()
        all_years = range(EARLIEST_YEAR, now.year + 3)

        if years_filter:
            target_years = sorted(y for y in all_years if y in years_filter)
        else:
            target_years = sorted(all_years)

        results: list[ScraperReturn] = []

        for year in target_years:
            url = self._year_page_url(year)
            try:
                html = self.fetcher.fetch_with_requests(url)
            except Exception as e:
                self.logger.debug(f"Could not fetch year {year}: {e}")
                continue

            year_results = self._parse_year_page(html, year)
            results.extend(year_results)

        # Sort by date descending (newest first)
        results.sort(key=lambda x: x.date, reverse=True)

        return results
