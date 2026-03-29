from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from aus_council_scrapers.base import BaseScraper, ScraperReturn, register_scraper


@register_scraper
class BoroondaraScraper(BaseScraper):
    def __init__(self):
        council = "boroondara"
        state = "VIC"
        base_url = "https://www.boroondara.vic.gov.au"
        super().__init__(council, state, base_url)

        # Example titles:
        # "Council Meeting - 24 November 2025"
        # "Additional Council Meeting - 20 October 2025"
        # "Council Meeting (Councillor Assignments) - 17 November 2025"
        self._event_year_re = re.compile(r"\b(19|20)\d{2}\b")
        self.default_location = "8 Inglesby Road, Camberwell, Victoria 3124"

        self._title_date_re = re.compile(
            r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+((?:19|20)\d{2})\b",
            re.I,
        )

    _REQUEST_DELAY = 1.5  # seconds between requests

    def _fetch(self, url: str) -> str:
        """Fetch a URL with a polite delay and automatic 429 retry."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                html = self.fetcher.fetch_with_requests(url)
                time.sleep(self._REQUEST_DELAY)
                return html
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 429 and attempt < max_retries - 1:
                    wait = 60 * (2 ** attempt)  # 60s, then 120s
                    self.logger.warning(
                        f"Rate limited (429). Waiting {wait}s before retry {attempt + 2}/{max_retries}..."
                    )
                    time.sleep(wait)
                else:
                    raise

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url, href)

    def _get_years_filter(self) -> set[int] | None:
        for attr in ["years_filter", "years", "year_filter", "year", "years_filter_list"]:
            v = getattr(self, attr, None)
            if v is None:
                continue
            if isinstance(v, int):
                return {v}
            if isinstance(v, (list, tuple, set)) and all(isinstance(x, int) for x in v):
                return set(v)
        return None

    def _date_from_title(self, title: str) -> str | None:
        if not title:
            return None
        m = self._title_date_re.search(title)
        if not m:
            return None
        day = int(m.group(1))
        month = m.group(2)
        year = int(m.group(3))
        try:
            dt = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _extract_event_links_from_listing(self, soup: BeautifulSoup) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []

        for a in soup.select("h2 a[href]"):
            text = (a.get_text(" ", strip=True) or "").strip()
            href = (a.get("href") or "").strip()
            if not href or "/events/" not in href:
                continue

            t = text.lower()
            # include "Council Meeting", "Urban Planning Delegated Committee Meeting", etc.
            if "meeting" not in t:
                continue

            m = self._event_year_re.search(text)
            if not m:
                continue
            year = int(m.group(0))

            out.append((self._abs(href), year))

        # de-dupe
        seen: set[str] = set()
        deduped: list[tuple[str, int]] = []
        for url, year in out:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((url, year))
        return deduped

    def _next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        """
        Robust pagination. Resolves relative hrefs against current_url (not base_url)
        so that ?page=N query-only hrefs resolve correctly.
        1) Site pager next selector (most reliable)
        2) rel=next
        3) aria/title/text contains next + href has page=
        """
        def resolve(href: str) -> str:
            return urljoin(current_url, href)

        # 1) Common Drupal pager markup
        a = soup.select_one("li.pager__item--next a[href], li.pagination__item--next a[href]")
        if a and a.get("href"):
            return resolve(a["href"])

        # 2) rel=next
        a = soup.select_one('a[rel="next"][href]')
        if a and a.get("href"):
            return resolve(a["href"])

        # 3) Heuristic search
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            text = (a.get_text(" ", strip=True) or "").lower()
            aria = (a.get("aria-label") or "").lower()
            title = (a.get("title") or "").lower()
            if ("next" in text) or ("next" in aria) or ("next" in title):
                if "page=" in href:
                    return resolve(href)

        return None

    def _looks_like_pdf_link(self, a) -> bool:
        href = (a.get("href") or "").lower()
        text = (a.get_text(" ", strip=True) or "").lower()

        if "/media/" in href and "/download" in href:
            return True
        if "[pdf]" in text:
            return True
        if ".pdf" in href:
            return True
        return False

    def _first_pdf_after_heading(self, soup: BeautifulSoup, heading_regex: re.Pattern) -> str | None:
        """
        Find a heading (h2-h6) matching heading_regex, then return the first PDF-ish link
        in the content until the next heading (h2-h6).
        """
        heading_tags = ["h2", "h3", "h4", "h5", "h6"]

        h = soup.find(heading_tags, string=heading_regex)
        if not h:
            for hx in soup.find_all(heading_tags):
                txt = (hx.get_text(" ", strip=True) or "").strip()
                if heading_regex.search(txt):
                    h = hx
                    break
        if not h:
            return None

        node = h
        while True:
            node = node.find_next_sibling()
            if node is None:
                return None
            if getattr(node, "name", None) in heading_tags:
                return None

            for a in node.select("a[href]"):
                if self._looks_like_pdf_link(a):
                    href = (a.get("href") or "").strip()
                    if href:
                        return self._abs(href)

    def _first_pdf_anywhere(self, soup: BeautifulSoup) -> str | None:
        """
        Fallback: first PDF-ish link anywhere on the page.
        Useful if headings change.
        """
        for a in soup.select("a[href]"):
            if self._looks_like_pdf_link(a):
                href = (a.get("href") or "").strip()
                if href:
                    return self._abs(href)
        return None

    def _extract_agenda_minutes(self, event_html: str) -> tuple[str | None, str | None]:
        soup = BeautifulSoup(event_html, "html.parser")

        # Agenda (prefer Revised Agenda if present, otherwise Agenda)
        agenda = self._first_pdf_after_heading(soup, re.compile(r"^\s*Revised Agenda\s*$", re.I))
        if not agenda:
            agenda = self._first_pdf_after_heading(soup, re.compile(r"^\s*Agenda\s*$", re.I))

        # Minutes: only use the "Minutes" heading.
        # "Minutes to be adopted" on Boroondara event pages refers to the *previous* meeting's
        # minutes being ratified at the current meeting — not this meeting's minutes.
        minutes = self._first_pdf_after_heading(soup, re.compile(r"^\s*Minutes\s*$", re.I))

        # Fallbacks (some pages differ slightly)
        if not agenda:
            # Some pages put the agenda PDF without a clean "Agenda" heading match
            agenda = self._first_pdf_anywhere(soup)
        # Minutes are often multiple; if heading lookup fails, don't guess aggressively.
        return agenda, minutes

    def scraper(self) -> list[ScraperReturn]:
        years_filter = self._get_years_filter()

        # Past meetings (paginated, newest-first): all meetings with published docs.
        # Upcoming meetings (main council page, also paginated): scheduled future meetings.
        listing_urls = [
            (
                "https://www.boroondara.vic.gov.au/your-council/councillors-and-meetings/"
                "council-and-committee-meetings/past-meeting-minutes-agendas-and-video-recordings"
            ),
            (
                "https://www.boroondara.vic.gov.au/your-council/councillors-and-meetings/"
                "council-and-committee-meetings"
            ),
        ]

        results: list[ScraperReturn] = []
        seen_event_urls: set[str] = set()

        for listing_url in listing_urls:
            url: str | None = listing_url
            max_pages = 200  # safety

            pages_seen = 0
            while url and pages_seen < max_pages:
                pages_seen += 1

                html = self._fetch(url)
                soup = BeautifulSoup(html, "html.parser")

                events = self._extract_event_links_from_listing(soup)

                # Stop early if we’ve paged past the target year(s) (listings are newest-first)
                if years_filter and events:
                    years_on_page = {y for _, y in events}
                    if years_on_page and max(years_on_page) < min(years_filter):
                        break

                for event_url, year in events:
                    if event_url in seen_event_urls:
                        continue
                    seen_event_urls.add(event_url)

                    if years_filter and year not in years_filter:
                        continue

                    event_html = self._fetch(event_url)
                    agenda_url, minutes_url = self._extract_agenda_minutes(event_html)

                    # Skip if neither doc exists (upcoming meetings may not have docs yet)
                    if not agenda_url and not minutes_url:
                        continue

                    event_soup = BeautifulSoup(event_html, "html.parser")
                    h1 = event_soup.find("h1")
                    name = h1.get_text(" ", strip=True) if h1 else "Council Meeting"

                    # Date: parse from title first (most reliable), then fall back to page regex.
                    date = self._date_from_title(name)

                    page_text = event_soup.get_text(" ", strip=True)
                    if not date and hasattr(self, "date_regex") and getattr(self, "date_regex"):
                        m = re.search(self.date_regex, page_text)
                        if m:
                            raw = m.group()
                            iso = self._date_from_title(f"Council Meeting - {raw}")
                            date = iso or raw

                    # Time: best-effort using base regexes
                    time = None
                    if hasattr(self, "time_regex") and getattr(self, "time_regex"):
                        tm = re.search(self.time_regex, page_text)
                        if tm:
                            time = tm.group().replace(".", ":")

                    results.append(
                        ScraperReturn(
                            name=name,
                            date=date,
                            time=time,
                            webpage_url=event_url,
                            download_url=agenda_url or minutes_url,  # backward compatibility
                            agenda_url=agenda_url,
                            minutes_url=minutes_url,
                            location=self.default_location,
                        )
                    )

                # Advance to the next page; stop if none found.
                next_url = self._next_page_url(soup, url)
                url = next_url

        if years_filter and not results:
            raise RuntimeError(f"No meetings found for year(s): {sorted(years_filter)}")

        return results