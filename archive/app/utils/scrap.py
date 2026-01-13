import scrapy
from scrapy.crawler import CrawlerRunner
from twisted.internet import reactor, defer
from threading import Thread

class UniversalSpider(scrapy.Spider):
    name = "universal_spider"
    allowed_domains = []  # Add domains to limit scraping scope
    start_urls = []  # Add starting URLs
    file_path = None

    def __init__(self, url=None, file_path=None, *args, **kwargs):
        super(UniversalSpider, self).__init__(*args, **kwargs)
        if url:
            self.start_urls = [url]
        if file_path:
            self.file_path = file_path

    def parse(self, response):
        self.log(f"Scraping: {response.url}")
        
        # Extract all links on the page
        links = response.css('a::attr(href)').getall()
        
        # Extract page title
        title = response.css('title::text').get()
        
        # Extract all paragraphs
        paragraphs = response.css('p::text').getall()
        
        if self.file_path:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(f"URL: {response.url}\n")
                f.write(f"Title: {title}\n\n")
                f.write("Links:\n" + "\n".join(links) + "\n\n")
                f.write("Paragraphs:\n" + "\n".join(paragraphs) + "\n\n")
                f.write("-" * 80 + "\n")
        
        return self.file_path

runner = CrawlerRunner()

def run_spider(url, file_path):
    """Runs the Scrapy spider asynchronously in the Twisted reactor."""
    @defer.inlineCallbacks
    def crawl():
        yield runner.crawl(UniversalSpider, url=url, file_path=file_path)

    if not reactor.running:
        Thread(target=reactor.run, kwargs={"installSignalHandlers": False}, daemon=True).start()

    reactor.callFromThread(crawl)

def scrape_and_get_file(url, file_path):
    """Runs Scrapy without blocking the FastAPI server."""
    run_spider(url, file_path)
    return file_path  # Immediately return file path while scraping runs in the background
