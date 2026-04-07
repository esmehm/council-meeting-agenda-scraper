import json
import datetime

import pytz

from aus_council_scrapers.base import register_scraper, BaseScraper, ScraperReturn
from aus_council_scrapers.constants import TIMEZONES_BY_STATE


@register_scraper
class ParramattaScraper(BaseScraper):
    ORG_ID = "06b8d045-4f33-426f-bf4a-300486492563"
    BASE_URL = "https://docspublished.com.au/CityofParramatta"
    API_URL = f"https://api.docassembler.com.au/api/documents/{ORG_ID}"

    def __init__(self):
        super().__init__("parramatta", "NSW", self.BASE_URL)

    def scraper(self) -> list[ScraperReturn]:
        years_filter = getattr(self, "years_filter", None)

        response = self.fetcher.fetch_with_requests(self.API_URL)
        documents = json.loads(response)

        results = []
        for doc in documents:
            meeting_date_str = doc.get("MeetingDate")
            if not meeting_date_str:
                continue

            meeting_dt_utc = datetime.datetime.fromisoformat(meeting_date_str).replace(
                tzinfo=datetime.timezone.utc
            )
            tz = pytz.timezone(TIMEZONES_BY_STATE["NSW"])
            meeting_dt = meeting_dt_utc.astimezone(tz)
            year = meeting_dt.year

            if years_filter and year not in years_filter:
                continue

            agenda_doc_id = doc.get("AgendaDocumentId")
            minutes_doc_id = doc.get("MinutesDocumentId")

            agenda_url = (
                f"{self.BASE_URL}/document/{agenda_doc_id}" if agenda_doc_id else None
            )
            minutes_url = (
                f"{self.BASE_URL}/document/{minutes_doc_id}" if minutes_doc_id else None
            )

            if not agenda_url and not minutes_url:
                continue

            date = meeting_dt.strftime("%Y-%m-%d")
            time = meeting_dt.strftime("%I:%M%p").lstrip("0").lower()

            results.append(
                ScraperReturn(
                    name=doc.get("DocumentTitle"),
                    date=date,
                    time=time,
                    webpage_url=self.BASE_URL,
                    agenda_url=agenda_url,
                    minutes_url=minutes_url,
                    download_url=agenda_url or minutes_url,
                )
            )

        if not results:
            self.logger.info(f"{self.council_name} scraper found no meetings")
        else:
            self.logger.info(
                f"{self.council_name} scraper found {len(results)} meetings"
            )

        return results
