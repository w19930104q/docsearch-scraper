from scrapy.dupefilters import RFPDupeFilter
from w3lib.url import canonicalize_url
from scrapy.utils.python import to_bytes
import weakref
import hashlib
import os
import re

_fingerprint_cache = weakref.WeakKeyDictionary()


def _job_dir(settings):
    """Return the JOBDIR setting (None when persistence is disabled).

    scrapy.utils.job.job_dir was removed/deprecated in recent Scrapy
    versions, only the setting lookup is needed here.
    """
    return settings.get('JOBDIR')


class CustomDupeFilter(RFPDupeFilter):
    def custom_request_fingerprint(self, request, include_headers=None,
                                   remove_scheme=None):
        """
        Overridden given that some URL can have a wrong encoding (when it is comes from selenium driver) changes:
        encode.('utf-8) & in order to be no scheme compliant
        """

        # If use_anchors, anchors in URL matters since each anchor
        # define a different webpage and content (special js_rendering)
        url_for_finger_print = canonicalize_url(
            request.url) if not self.use_anchors else request.url
        url_for_hash = url_for_finger_print.encode('utf-8')

        # scheme agnosticism
        if remove_scheme:
            match_capture_any_scheme = r'(https?)(.*)'
            url_for_hash = re.sub(match_capture_any_scheme, r"\2",
                                  url_for_finger_print)

        if include_headers:
            include_headers = tuple(to_bytes(h.lower())
                                    for h in sorted(include_headers))
        cache = _fingerprint_cache.setdefault(request, {})

        if include_headers not in cache or not remove_scheme:
            # Since it is called from the same function, wee need to ensure
            # we compute the fingerprint which take into account the scheme.
            # Avoid caching
            fp = hashlib.sha1()
            fp.update(to_bytes(request.method.encode('utf-8')))
            fp.update(to_bytes(url_for_hash))
            fp.update(request.body or ''.encode('utf-8'))
            if include_headers:
                for hdr in include_headers:
                    if hdr in request.headers:
                        fp.update(hdr)
                        for v in request.headers.getlist(hdr):
                            fp.update(v)
            cache[include_headers] = fp.hexdigest()
        return cache[include_headers]

    def __init__(self, path=None, debug=False, use_anchors=False):
        super(CustomDupeFilter, self).__init__(path=path, debug=debug)
        # Spread config bool
        self.use_anchors = use_anchors
        # Scheme agnostic fingerprints (hex strings)
        self.schemeless_fingerprints = set()
        # This set will not be scheme agnostic
        self.fingerprints_with_scheme = set()

    @classmethod
    def from_crawler(cls, crawler):
        debug = crawler.settings.getbool('DUPEFILTER_DEBUG')
        use_anchors = crawler.settings.getbool('DUPEFILTER_USE_ANCHORS')
        return cls(_job_dir(crawler.settings), debug, use_anchors)

    # Kept for backward compatibility with older Scrapy versions
    @classmethod
    def from_settings(cls, settings):
        debug = settings.getbool('DUPEFILTER_DEBUG')
        use_anchors = settings.getbool('DUPEFILTER_USE_ANCHORS')
        return cls(_job_dir(settings), debug, use_anchors)

    def request_seen(self, request):
        """
        Overridden given that we have to handle the redirection case :
        - redirection could be into the same page with another scheme => need the fingerprint
          to take into account the scheme
        - avoid loop in redirection by keeping a dedicated track of it
        """

        fp = self.custom_request_fingerprint(request, remove_scheme=True)
        fp_with_scheme = self.custom_request_fingerprint(request,
                                                         remove_scheme=False)
        # Request from a redirection which is followed byt he RedirectionMiddleware
        isRedirected = request.meta.get('redirect_times') and request.meta.get(
            'redirect_times') > 0
        isFallback = request.meta.get("alternative_fallback")

        if isRedirected or isFallback:
            if fp_with_scheme in self.fingerprints_with_scheme:
                return True
            self.fingerprints_with_scheme.add(fp_with_scheme)
            # We don't want go back onto the visited web page, especially when it isn't from a redirection
            if fp not in self.schemeless_fingerprints:
                self.register_fingerprint(fp)
        else:
            if fp in self.schemeless_fingerprints:
                return True
            self.register_fingerprint(fp)
            self.fingerprints_with_scheme.add(fp_with_scheme)

    def register_fingerprint(self, fp):
        self.schemeless_fingerprints.add(fp)
        if self.file:
            # Keep the binary, length-prefixed format used by Scrapy >= 2.6
            fp_bytes = bytes.fromhex(fp)
            self.file.write(len(fp_bytes).to_bytes(2, 'big') + fp_bytes)
