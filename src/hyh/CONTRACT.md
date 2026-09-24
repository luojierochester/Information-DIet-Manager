# Information Diet Manager API Contract (hyh)

## 1. Scope and Boundaries
- `hyh` is the backend API orchestration/storage layer.
- `hyh` accepts raw browsing records and persists them.
- `hyh` can trigger `lsj` analysis pipeline, but does not re-implement algorithm logic.

## 2. Data Model (Minimum Viable)
### 2.1 `items` (raw records)
Required fields:
- `url`: HTTP/HTTPS URL, at most 2048 characters
- `title`: string, trimmed before validation; 1 to 300 characters after trimming
- `ts`: strict integer Unix epoch milliseconds, from `0` through `253402300799999` (year 9999); strings, booleans and floats are invalid for `/collect`
- `source`: `plugin | import`

Optional fields:
- `text`: string, at most 2000 characters (if empty, backend falls back to `title`)
- `lang`, `channel`, `author`: strings of at most 16, 32 and 120 characters respectively
- `tags`: at most 10 strings, at most 40 characters each
- `meta`: object containing only JSON values and string keys, with at most 8 nested object/array levels (root object is level 1); finite numbers only. Its compact JSON serialization with literal Unicode must fit in 8192 UTF-8 bytes
- optional fields may be omitted or `null`; unknown top-level properties are ignored for compatibility
- string limits count Unicode code points; `meta` has a byte limit instead. Strings must be valid Unicode encodable as UTF-8; lone surrogate escapes are rejected, while valid pairs such as emoji are accepted

Backend normalization:
- `text` empty -> fallback to `title`
- long text truncated to first `1000` chars
- `url_hash` and `content_hash` generated server-side

### 2.2 `embeddings`
- one row per `item_id`
- stores vector blob, model id, vector dim
- generated on item insert; analysis only backfills missing vectors

### 2.3 `stats_daily`
- per-day aggregate snapshot
- fields:
  - `total_count`
  - `channel_counts` (JSON)
  - `repeat_ratio`
  - `negative_ratio`
  - `avg_sentiment`

### 2.4 `analysis_runs`
- append-only analysis history for each run
- stores full API payload + cached flag

### 2.5 `analysis_jobs`
- orchestration state machine for API jobs
- statuses: `queued | running | completed | failed`
- stores input window, cache hit, duration, error, result payload

## 3. API Surface
### 3.1 Ingestion
- `POST /collect`
  - body: single `IngestItem`
  - successful response is exactly one of `{"inserted": 1, "duplicates": 0, "failed": 0}` or `{"inserted": 0, "duplicates": 1, "failed": 0}`
  - acknowledgement is returned only after the database transaction commits; storage errors must not be acknowledged as success
  - retry after a lost response is safe under the existing normalized-URL unique constraint: a previously committed page returns `duplicates: 1`
  - `duplicates` means the page URL already exists, not that a new visit or updated title/text was saved; repeated URLs retain their original record
  - clients may remove a queued record only after validating non-negative integer acknowledgement fields, `failed == 0`, and `inserted + duplicates == 1`; HTTP 2xx alone is insufficient
  - schema validation failures return `422`. Keep rejected records available for inspection/correction; do not silently drop them or continuously retry an unchanged invalid payload
  - validation error entries contain only `type`, `loc` and `msg`; they do not echo request values or exception contexts. Invalid metadata such as non-finite numbers or lone surrogate escapes is also rejected as `422`

- `POST /import`
  - body: `multipart/form-data` file (`csv/json/jsonl`)
  - response: `{"inserted": n, "duplicates": m, "failed": k}`
  - each parsed item uses the same field limits; invalid items contribute to `failed`. The import parser retains its legacy conversion of textual timestamps before model validation
  - a storage failure rolls back the entire insert transaction, including earlier valid rows and their embeddings; it does not return a partial-success acknowledgement

- `GET /items?page=1&page_size=20`
  - list stored raw items

### 3.2 Stats (lightweight)
- `POST /analyze/run?force=false&backfill_limit=2000`
  - fast aggregate without full algorithm pipeline
  - returns cached result when no new items

- `GET /dashboard/summary`
  - latest daily aggregate snapshot

- `GET /dashboard/visualization?days=7&from_ts=&to_ts=&limit_rows=5000&force=false`
  - on-demand visualization payload for frontend charts
  - loads raw items in the requested time window, runs `lsj` classify/sentiment/similarity/evaluator pipeline,
    and returns chart-ready global + category time series
  - `from_ts` / `to_ts` are optional Unix epoch milliseconds; if omitted, backend uses `days`
  - `force=true` bypasses the cache; the dashboard uses it for every explicit analysis request
  - cache keys carry visualization schema version 2 so pre-status-contract payloads are not reused; only ready results without warnings are reusable
  - a cached response retains its original window and generation timestamp; use `force=true` for a fresh rolling window
  - response shape:
    - `window`
      - `from_ts`, `to_ts`, `limit_rows`, `input_count`
      - `available_count`: all saved records in the requested window, counted in the same SQLite read snapshot
      - `truncated`: true when the window contains more records than were loaded
      - `processed_count`: valid rows after preprocessing, present on a ready response
      - selection remains ascending by timestamp; a truncated result is the earliest portion, not a representative sample
    - `analysis_status`: `empty | insufficient_data | unavailable | failed | ready`
      - `empty`: no input records in the requested window
      - `insufficient_data`: fewer than `minimum_records` input records; optional model dependencies are not loaded
      - `unavailable`: the pipeline could not run (dependency/configuration/runtime failure)
      - `failed`: visualization postprocessing failed
      - `ready`: chart data was produced; this does not certify model accuracy
      - non-ready responses contain empty chart collections, never invented neutral/zero measurements
    - `minimum_records`: currently 5, an implementation guard matching the default evaluator; not a scientifically validated sample-size requirement
    - `date_timezone`: currently `UTC`; a rolling seven-day window can intersect eight calendar dates
    - `category_counts`: absolute counts from the same preprocessed sample as the daily series
      - retains `shopping` and `tools` as separate categories
    - `global`
      - `time_series`: `[{date, count, avg_polarity, avg_similarity, repeat_ratio, negative_ratio, positive_ratio, neutral_ratio}]`
      - `category_distribution`, `sentiment_distribution`, `similarity_histogram`, `hourly_distribution`
    - `categories`
      - keyed by normalized category name such as `entertainment`, `learning`, `news`, `social`
      - each item: `{alias, label, time_series}`
      - alias mapping is stable for frontend drill-down:
        - `entertainment -> ent`
        - `learning -> edu`
        - `news -> news`
        - `social -> soc`
        - `shopping -> shopping`
        - `tools -> tools`
        - `other -> other`
    - `category_aliases`
      - same mapping table for client-side lookup
    - `pipeline_warning`
      - diagnostic warning for unavailable/failed analysis; clients must not treat it as a successful measurement
    - `generated_at`

- `GET /analyze/history?limit=20`
  - latest run history records

### 3.3 Full analysis orchestration (`hyh` -> `lsj`)
- `POST /analyze/run_full?force=false&from_ts=&to_ts=&limit_rows=5000`
  - load raw records from `items`
  - call `lsj` pipeline:
    1. classify -> `category`
    2. sentiment -> `sentiment`, `polarity`
    3. similarity -> `similarity`
    4. evaluator -> report
  - persist:
    - `stats_daily` (for dashboard)
    - `analysis_runs` (history)
    - `analysis_jobs` (job lifecycle)
  - if `lsj` runtime dependencies are missing, API still completes with degraded metrics and
    returns `pipeline_warning` in payload

- `GET /analyze/jobs/{job_id}`
  - job status + metadata

- `GET /analyze/result/{job_id}`
  - completed job result payload

## 4. Important Clarification
- The dashboard reads live saved-record counts from `GET /items`, independently of analysis.
- Saved items are deduplicated pages, not visit events or measured reading duration.
- The dashboard requests visualization only on explicit user action. It displays the result as an experimental snapshot.
- Visualization failures/unavailability are recorded as failed jobs, not successful measurements; `/analyze/run_full` retains its separate legacy behavior.
- Missing dates/metrics are unknown values, not zero. Zero proportions are valid observations.
- Existing `/analyze/run`, `/analyze/run_full` and `/dashboard/summary` retain their older behavior; their degraded metrics are not covered by the visualization status contract above. The current dashboard does not consume them.
- Client upload payload does **not** include:
  - `category`, `sentiment`, `polarity`, `similarity`
- These are derived fields generated inside analysis pipeline before evaluator.

## 5. Caching and Idempotency
- same input window + same `item_max_created_at` reuses previous completed result
- cache events are recorded in `analysis_jobs` (`cache_hit = 1`)

## 6. Error Contract
- invalid `/collect` request fields -> `422`; malformed/unsupported import files -> `400`
- job not found -> `404`
- result requested before completion -> `409`
- storage failure during ingestion -> `500`, without a successful acknowledgement; only normalized-URL uniqueness conflicts count as duplicates, other database constraint failures do not
- pipeline/internal failure -> `500` with `job_id` in detail where the analysis job endpoint supplies one
