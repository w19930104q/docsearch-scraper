"""
DocSearch scraper main entry point
"""
import os
import json
import requests

from scrapy.crawler import CrawlerProcess

from .algolia_helper import AlgoliaHelper
from .config.config_loader import ConfigLoader
from .documentation_spider import DocumentationSpider
from .documentation_spider import EXIT_CODE_EXCEEDED_RECORDS
from .documentation_spider import EXIT_CODE_SEED_FAILURE
from .documentation_spider import EXIT_CODE_PAGE_FAILURE
from .documentation_spider import EXIT_CODE_CRAWL_INTERRUPTED
from .documentation_spider import STAGE_SEED
from .documentation_spider import STAGE_PAGE
from .strategies.default_strategy import DefaultStrategy
from .custom_downloader_middleware import CustomDownloaderMiddleware
from .custom_dupefilter import CustomDupeFilter
from .config.browser_handler import BrowserHandler
from .strategies.algolia_settings import AlgoliaSettings

try:
    # disable boto (S3 download)
    from scrapy import optional_features

    if 'boto' in optional_features:
        optional_features.remove('boto')
except ImportError:
    pass

EXIT_CODE_NO_RECORD = 3


def _print_crawl_failures(failures):
    """Report every failed url with its stage and the failure reason"""
    print('\033[91m> DocSearch: {} crawl failure(s), the previous index is'
          ' kept untouched:\033[0m'.format(len(failures)))
    for failure in failures:
        print('\033[91m  - [stage: {}] {} — {}\033[0m'.format(
            failure['stage'], failure['url'], failure['reason']))


def run_config(config, algolia_client=None, crawl=None):
    config = ConfigLoader(config)
    CustomDownloaderMiddleware.driver = config.driver
    DocumentationSpider.NB_INDEXED = 0
    DocumentationSpider.crawl_errors = []
    DocumentationSpider.nb_hits_exceeded = False
    DocumentationSpider.close_reason = None
    DocumentationSpider.reason_to_stop = None

    strategy = DefaultStrategy(config)

    algolia_helper = AlgoliaHelper(
        config.app_id,
        config.api_key,
        config.index_name,
        config.index_name_tmp,
        AlgoliaSettings.get(config, strategy.levels),
        config.query_rules,
        algolia_client=algolia_client
    )

    root_module = 'src.' if __name__ == '__main__' else 'scraper.src.'
    DOWNLOADER_MIDDLEWARES_PATH = root_module + 'custom_downloader_middleware.' + CustomDownloaderMiddleware.__name__
    DUPEFILTER_CLASS_PATH = root_module + 'custom_dupefilter.' + CustomDupeFilter.__name__

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    }  # Defaults for scrapy https://docs.scrapy.org/en/latest/topics/settings.html#default-request-headers

    if os.getenv("CF_ACCESS_CLIENT_ID") and os.getenv("CF_ACCESS_CLIENT_SECRET"):
        headers.update(
            {
                "CF-Access-Client-Id": os.getenv("CF_ACCESS_CLIENT_ID"),
                "CF-Access-Client-Secret": os.getenv("CF_ACCESS_CLIENT_SECRET"),
            }
        )
    elif os.getenv("IAP_AUTH_CLIENT_ID") and os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON"):
        # Imported lazily: only needed when IAP authentication is configured
        from requests_iap import IAPAuth
        iap_token = IAPAuth(
            client_id=os.getenv("IAP_AUTH_CLIENT_ID"),
            service_account_secret_dict=json.loads(
                os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON")
            ),
        )(requests.Request()).headers["Authorization"]
        headers.update({"Authorization": iap_token})

    DEFAULT_REQUEST_HEADERS = headers

    settings = {
        'LOG_ENABLED': '1',
        'LOG_LEVEL': 'ERROR',
        'USER_AGENT': config.user_agent,
        'DOWNLOADER_MIDDLEWARES': {DOWNLOADER_MIDDLEWARES_PATH: 900},
        # Need to be > 600 to be after the redirectMiddleware
        'DUPEFILTER_USE_ANCHORS': config.use_anchors,
        # Use our custom dupefilter in order to be scheme agnostic regarding link provided
        'DUPEFILTER_CLASS': DUPEFILTER_CLASS_PATH,
        'DEFAULT_REQUEST_HEADERS': DEFAULT_REQUEST_HEADERS,
        'TELNETCONSOLE_ENABLED': False
    }

    try:
        if crawl is None:
            process = CrawlerProcess(settings)

            process.crawl(
                DocumentationSpider,
                config=config,
                algolia_helper=algolia_helper,
                strategy=strategy
            )

            process.start()
            process.stop()
        else:
            # Custom crawl execution (e.g. tests driving their own reactor)
            crawl(
                DocumentationSpider,
                settings,
                config=config,
                algolia_helper=algolia_helper,
                strategy=strategy
            )
    except KeyboardInterrupt:
        # Cancelled by the user: the crawl is incomplete, the temporary
        # index must not be committed
        pass

    # Kill browser if needed
    BrowserHandler.destroy(config.driver)

    print("")

    # From here on, the temporary index is only committed (atomically, through
    # an index move) when the crawl completed without any failure. Any page
    # or seed failure, record-limit abort or interruption keeps the previous
    # index untouched, wipes the temporary one and exits with a dedicated
    # non-zero code.

    if DocumentationSpider.nb_hits_exceeded:
        print('\033[91m> DocSearch: crawling aborted: {}\033[0m'.format(
            DocumentationSpider.reason_to_stop))
        algolia_helper.clear_tmp_index()
        exit(EXIT_CODE_EXCEEDED_RECORDS)

    seed_failures = [error for error in DocumentationSpider.crawl_errors
                     if error['stage'] == STAGE_SEED]
    if len(seed_failures) > 0:
        _print_crawl_failures(seed_failures)
        algolia_helper.clear_tmp_index()
        exit(EXIT_CODE_SEED_FAILURE)

    page_failures = [error for error in DocumentationSpider.crawl_errors
                     if error['stage'] == STAGE_PAGE]
    if len(page_failures) > 0:
        _print_crawl_failures(page_failures)
        algolia_helper.clear_tmp_index()
        exit(EXIT_CODE_PAGE_FAILURE)

    if DocumentationSpider.close_reason != 'finished':
        print('\033[91m> DocSearch: crawling did not complete (reason: {}),'
              ' the previous index is kept untouched\033[0m'.format(
                  DocumentationSpider.close_reason))
        algolia_helper.clear_tmp_index()
        exit(EXIT_CODE_CRAWL_INTERRUPTED)

    if len(config.extra_records) > 0:
        algolia_helper.add_records(config.extra_records, "Extra records", False)

    if DocumentationSpider.NB_INDEXED > 0:
        algolia_helper.commit_tmp_index()
        print('Nb hits: {}'.format(DocumentationSpider.NB_INDEXED))
        config.update_nb_hits_value(DocumentationSpider.NB_INDEXED)
    else:
        print('Crawling issue: nbHits 0 for ' + config.index_name)
        exit(EXIT_CODE_NO_RECORD)
    print("")


if __name__ == '__main__':
    from os import environ

    run_config(environ['CONFIG'])
