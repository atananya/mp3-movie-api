from pathlib import Path

import pandas as pd
import pytest

from src import MovieStorage
from src.storage import MOVIE_COLUMNS
import main


def test_movies_persist(client, test_storage, uploaded_movies):
    # A brand-new storage object on the same file = what happens after a restart
    other = MovieStorage(test_storage.db_path)
    df = other.get()

    assert len(df) == 20
    assert other.count_movies() == 20
    assert list(df.columns) == MOVIE_COLUMNS

    original = pd.read_csv(Path("fixtures") / "sample_movies.csv")
    assert df["title"].tolist() == original["title"].tolist()

    db_ids = [row["id"] for row in other.select_columns(["title"], limit=100)]

    response = client.get("/movies", params={"limit": 100})
    assert response.status_code == 200
    api_ids = [row["id"] for row in response.json()["data"]]

    assert db_ids == list(range(1, 21))
    assert api_ids == list(range(1, 21))


def test_cache_and_processing(client, uploaded_movies, processing_body):
    # 1. Fill the cache for two limits
    first_100 = client.get("/movies", params={"limit": 100})
    first_1 = client.get("/movies", params={"limit": 1})
    assert first_100.status_code == 200
    assert first_1.status_code == 200
    assert main.cache.get(100) == first_100.json()
    assert main.cache.get(1) == first_1.json()

    # 2. Repeat limit=100 -> same response (cache hit)
    repeat_100 = client.get("/movies", params={"limit": 100})
    assert repeat_100.status_code == 200
    assert repeat_100.json() == first_100.json()

    # 3. Process -> cache should be cleared
    processed = client.post("/movies/process", json=processing_body)
    assert processed.status_code == 200
    body = processed.json()
    assert body["validation_rows_removed"] == 0
    assert body["cleaning_report"]["rows_before"] == 20
    assert body["cleaning_report"]["rows_after"] == 16
    assert body["cleaning_report"]["rows_removed"] == 4
    assert main.cache.get(100) is None
    assert main.cache.get(1) is None

    # 4. Retrieve again -> fresh data from SQLite
    after = client.get("/movies", params={"limit": 100})
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["total_rows"] == 16

    expected_titles = [f"Movie {letter}" for letter in "ABCDEFGHIJKLMNOP"]
    assert [row["title"] for row in after_body["data"]] == expected_titles
    assert [row["id"] for row in after_body["data"]] == list(range(1, 17))
    assert main.cache.get(100) == after_body


def test_cache_hit_skips_database(client, uploaded_movies, monkeypatch):
    # 1. Populate the cache
    first = client.get("/movies", params={"limit": 100})
    assert first.status_code == 200

    # 2. Any database read now fails the test
    def fail_if_called(*args, **kwargs):
        pytest.fail("Database was queried on a cache hit")

    monkeypatch.setattr(main.storage, "select_columns", fail_if_called)
    monkeypatch.setattr(main.storage, "count_movies", fail_if_called)

    # 3. Same request must come from the cache
    second = client.get("/movies", params={"limit": 100})
    assert second.status_code == 200
    assert second.json() == first.json()