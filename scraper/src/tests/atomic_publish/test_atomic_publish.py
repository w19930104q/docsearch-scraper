# coding: utf-8
"""
Regression tests for the atomic publication of the search index.

The scraper must only replace the production index when the crawl completed
fully: any page failure, seed failure, record-limit abort or interruption
keeps the previous index untouched and exits with a dedicated non-zero code,
while the temporary index is wiped on every run so leftovers can never leak
into a later commit.

Crawls are replayed against a local site (SiteReplay) and a fake Algolia
client (FakeAlgoliaClient).
"""
import json

from ...index import EXIT_CODE_NO_RECORD
from ...documentation_spider import EXIT_CODE_EXCEEDED_RECORDS
from ...documentation_spider import EXIT_CODE_SEED_FAILURE
from ...documentation_spider import EXIT_CODE_PAGE_FAILURE
from ...documentation_spider import EXIT_CODE_CRAWL_INTERRUPTED

from .conftest import make_crawl, run_scraper
from .fake_algolia import FakeAlgoliaClient
from .site_replay import make_page

INDEX_NAME = 'test-index'
INDEX_NAME_TMP = 'test-index_tmp'

PREVIOUS_RECORD = {'objectID': 'previous', 'url': 'http://previous.example/'}


def make_config(site, start_path='/', **overrides):
    config = {
        'index_name': INDEX_NAME,
        'allowed_domains': ['127.0.0.1'],
        'start_urls': [site.url(start_path)],
        'selectors': {
            'lvl0': 'h1',
            'text': 'p'
        }
    }
    config.update(overrides)
    return json.dumps(config)


def seed_previous_index(fake_client):
    """Simulate an already published index that must be preserved"""
    fake_client.init_index(INDEX_NAME)
    fake_client.records_of(INDEX_NAME).append(dict(PREVIOUS_RECORD))
    fake_client.init_index(INDEX_NAME_TMP)


class TestAtomicPublish:
    def test_full_success_commits_tmp_index(self, site):
        site.routes.update({
            '/': {'body': make_page('Home', 'welcome', [site.url('/ok')])},
            '/ok': {'body': make_page('Ok', 'content')},
        })
        fake_client = FakeAlgoliaClient()

        outcome = run_scraper(make_config(site), fake_client, make_crawl())

        assert 'code' not in outcome
        assert fake_client.moved_indexes() == [(INDEX_NAME_TMP, INDEX_NAME)]
        urls = {record['url'] for record in fake_client.records_of(INDEX_NAME)}
        assert site.url('/') in urls
        assert site.url('/ok') in urls

    def test_page_http_error_keeps_previous_index(self, site, capsys):
        site.routes.update({
            '/': {
                'body': make_page('Home', 'welcome',
                                  [site.url('/ok'), site.url('/broken')])
            },
            '/ok': {'body': make_page('Ok', 'content')},
            '/broken': {'status': 500, 'body': 'boom'},
        })
        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site), fake_client, make_crawl())

        assert outcome.get('code') == EXIT_CODE_PAGE_FAILURE
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
        # the temporary index is wiped so a later run cannot commit leftovers
        assert fake_client.records_of(INDEX_NAME_TMP) == []
        out = capsys.readouterr().out
        assert site.url('/broken') in out
        assert 'page' in out
        assert 'HTTP 500' in out

    def test_page_timeout_keeps_previous_index(self, site, capsys):
        site.routes.update({
            '/': {
                'body': make_page('Home', 'welcome', [site.url('/slow')])
            },
            '/slow': {'body': make_page('Slow', 'late'), 'delay': 3},
        })
        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(
            make_config(site), fake_client,
            make_crawl(extra_settings={'DOWNLOAD_TIMEOUT': 1}))

        assert outcome.get('code') == EXIT_CODE_PAGE_FAILURE
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
        assert fake_client.records_of(INDEX_NAME_TMP) == []
        out = capsys.readouterr().out
        assert site.url('/slow') in out
        assert 'page' in out

    def test_seed_failure_keeps_previous_index(self, site, capsys):
        # the start url does not exist: the seed itself cannot be fetched
        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site, start_path='/missing'),
                              fake_client, make_crawl())

        assert outcome.get('code') == EXIT_CODE_SEED_FAILURE
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
        assert fake_client.records_of(INDEX_NAME_TMP) == []
        out = capsys.readouterr().out
        assert '/missing' in out
        assert 'seed' in out

    def test_nb_hits_max_aborts_without_commit(self, site):
        site.routes.update({
            '/': {'body': make_page('Home', 'welcome', [site.url('/ok')])},
            '/ok': {'body': make_page('Ok', 'content')},
        })
        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site, nb_hits_max=1), fake_client,
                              make_crawl())

        assert outcome.get('code') == EXIT_CODE_EXCEEDED_RECORDS
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
        assert fake_client.records_of(INDEX_NAME_TMP) == []

    def test_zero_records_keeps_no_record_semantics(self, site):
        site.routes.update({
            '/': {
                'body': '<html><body><div>nothing to index</div></body></html>'
            },
        })
        fake_client = FakeAlgoliaClient()

        outcome = run_scraper(make_config(site), fake_client, make_crawl())

        assert outcome.get('code') == EXIT_CODE_NO_RECORD
        assert fake_client.moved_indexes() == []

    def test_tmp_index_is_cleared_on_every_run(self, site):
        site.routes.update({
            '/': {'body': make_page('Home', 'welcome')},
        })
        fake_client = FakeAlgoliaClient()
        # stale records left in the temporary index by a previous run
        fake_client.init_index(INDEX_NAME_TMP)
        fake_client.records_of(INDEX_NAME_TMP).append(
            {'objectID': 'stale', 'url': 'http://stale.example/'})

        outcome = run_scraper(make_config(site), fake_client, make_crawl())

        assert 'code' not in outcome
        assert fake_client.moved_indexes() == [(INDEX_NAME_TMP, INDEX_NAME)]
        object_ids = {record.get('objectID')
                      for record in fake_client.records_of(INDEX_NAME)}
        assert 'stale' not in object_ids

    def test_failed_run_records_are_not_carried_into_next_commit(self, site):
        # First run: a page fails, partial records land in the tmp index
        site.routes.update({
            '/': {
                'body': make_page('Home', 'welcome',
                                  [site.url('/partial'), site.url('/broken')])
            },
            '/partial': {'body': make_page('Partial', 'half crawled')},
            '/broken': {'status': 500, 'body': 'boom'},
        })
        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site), fake_client, make_crawl())
        assert outcome.get('code') == EXIT_CODE_PAGE_FAILURE

        # Second run on a healthy site with the same Algolia client
        site.routes.update({
            '/': {'body': make_page('Home', 'welcome', [site.url('/ok')])},
            '/ok': {'body': make_page('Ok', 'content')},
        })
        outcome = run_scraper(make_config(site), fake_client, make_crawl())

        assert 'code' not in outcome
        urls = {record['url'] for record in fake_client.records_of(INDEX_NAME)}
        assert site.url('/ok') in urls
        # nothing from the failed run leaked into the committed index
        assert site.url('/partial') not in urls
        assert PREVIOUS_RECORD['url'] not in urls

    def test_interrupted_crawl_keeps_previous_index(self, site):
        def cancelled_crawl(*args, **kwargs):
            raise KeyboardInterrupt

        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site), fake_client, cancelled_crawl)

        assert outcome.get('code') == EXIT_CODE_CRAWL_INTERRUPTED
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
        assert fake_client.records_of(INDEX_NAME_TMP) == []

    def test_unfinished_crawl_keeps_previous_index(self, site):
        def noop_crawl(*args, **kwargs):
            return None

        fake_client = FakeAlgoliaClient()
        seed_previous_index(fake_client)

        outcome = run_scraper(make_config(site), fake_client, noop_crawl)

        assert outcome.get('code') == EXIT_CODE_CRAWL_INTERRUPTED
        assert fake_client.moved_indexes() == []
        assert fake_client.records_of(INDEX_NAME) == [PREVIOUS_RECORD]
