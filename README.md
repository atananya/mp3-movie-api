# MP3 Movie API

This FastAPI application stores one movie collection in SQLite and serves it over HTTP. `POST /upload` reads a CSV, checks that it has exactly the `title`, `release_year`, `rating` and `genre` columns, and replaces the stored collection. `src/database.py` creates the SQLite connection and the `movies` table, and `src/storage.py` (`MovieStorage`) handles saving, retrieving, counting and filtering movies with SQL. `POST /movies/process` loads the stored movies, runs them through the MP1 validation and cleaning functions in `data_pipeline/`, and saves the cleaned result back to SQLite. `GET /movies` returns movies with a total count, and `GET /movies/search` filters by minimum rating and returns only the requested columns. `src/cache.py` (`MovieCache`) caches `GET /movies` responses in FakeRedis for 90 seconds, keyed by `limit`. Every successful upload or processing call clears the cache so the next retrieval reads fresh data from SQLite. Because the data lives in `data/movies.db`, it survives server restarts.

## Run the API

```bash
python -m uvicorn main:app --reload
```

Then open http://127.0.0.1:8000/docs.

## Run the tests

```bash
python -m pytest tests -v
```

## Sample processing request

After uploading `fixtures/sample_movies.csv`, send `POST /movies/process` with:

```json
{
  "validation": {
    "required_columns": ["title", "release_year", "rating", "genre"],
    "numeric_columns": ["release_year", "rating"]
  },
  "processing": {
    "remove_duplicates": true,
    "missing": {"enabled": true, "axis": "rows"},
    "outliers": {"enabled": true, "columns": ["rating"], "method": "iqr", "threshold": 1.5}
  }
}
```

Expected result:

```json
{
  "validation_rows_removed": 0,
  "cleaning_report": {
    "rows_before": 20,
    "rows_after": 16,
    "rows_removed": 4,
    "columns_before": 4,
    "columns_after": 4,
    "columns_removed": 0
  }
}
```