from aus_council_scrapers.base import register_scraper, InfoCouncilScraper


@register_scraper
class KuRingGaiScraper(InfoCouncilScraper):
    def __init__(self):
        council = "kuringgai"
        state = "NSW"
        base_url = "https://www.krg.nsw.gov.au"
        infocouncil_url = "https://kuringgai.infocouncil.biz/"
        super().__init__(council, state, base_url, infocouncil_url)
