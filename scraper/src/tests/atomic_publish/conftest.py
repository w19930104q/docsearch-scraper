# coding: utf-8
"""
Shared fixtures and helpers for the crawl regression tests.

A single Twisted reactor is run in a background thread for the whole test
session (a reactor can only be started once per process, so CrawlerProcess
cannot be used for several crawls in one pytest run). The `crawl` callable
injected into run_config waits on that reactor through CrawlerRunner.
"""
import threading

import pytest
from twisted.internet import reactor
from twisted.internet.threads import blockingCallFromThread
from scrapy.crawler import CrawlerRunner
from scrapy.utils.python import global_object_name

from ...index import run_config
from .site_replay import SiteReplay

# Import path of the reactor installed in this process: Scrapy checks that
# its TWISTED_REACTOR setting matches the already installed reactor
INSTALLED_REACTOR = global_object_name(type(reactor))

# Upper bound (seconds) for a single scraper run in tests
RUN_TIMEOUT = 120


@pytest.fixture(scope='session', autouse=True)
def reactor_thread():
    thread = threading.Thread(target=reactor.run,
                              kwargs={'installSignalHandlers': False})
    thread.daemon = True
    thread.start()
    yield reactor
    reactor.callFromThread(reactor.stop)
    thread.join(timeout=10)


@pytest.fixture
def site():
    replay = SiteReplay({})
    yield replay
    replay.stop()


def make_crawl(extra_settings=None):
    """Build a `crawl` replacement for run_config that drives the shared
    reactor instead of CrawlerProcess.start()"""

    def crawl(spider_cls, settings, **spider_kwargs):
        settings = dict(settings)
        settings['TWISTED_REACTOR'] = INSTALLED_REACTOR
        if extra_settings:
            settings.update(extra_settings)
        runner = CrawlerRunner(settings)
        return blockingCallFromThread(
            reactor, lambda: runner.crawl(spider_cls, **spider_kwargs))

    return crawl


def run_scraper(config, fake_client, crawl):
    """Run run_config and return its outcome as a dict:
    - 'result': the return value when run_config returned normally
    - 'code': the exit code when run_config called exit()
    """
    outcome = {}
    try:
        outcome['result'] = run_config(
            config, algolia_client=fake_client, crawl=crawl)
    except SystemExit as exc:
        outcome['code'] = exc.code
    return outcome
