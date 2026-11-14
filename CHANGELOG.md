## Change Log

### Unreleased
- FIX: only publish the search index when the crawl fully succeeds. Page
  failures, seed failures, record-limit aborts and interruptions now keep
  the previous index untouched, wipe the temporary index and exit with a
  dedicated non-zero code (4: record limit, 6: seed failure, 7: page
  failure, 8: interrupted crawl). The temporary index is cleared on every
  run so leftovers from a failed or cancelled run can never be committed.

### 1.0
- NEW: Use a chromium headless selenium driver