import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from data_pipeline import create_cleaning_report, process_data, validate_dataframe
from src import (
    HealthResponse,
    MovieCache,
    MovieDataResponse,
    MovieProcessingReport,
    MovieStorage,
    MovieUploadInfo,
    ProcessConfig,
    log_requests,
    setup_logging,
)
from src.storage import MOVIE_COLUMNS

setup_logging(verbose=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Movie API", version="1.0.0")
app.middleware("http")(log_requests)
storage = MovieStorage()
cache = MovieCache()


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Return the application status and version."""
    return HealthResponse(status="ok", version=app.version)


@app.post("/upload", response_model=MovieUploadInfo)
async def upload_movies(file: UploadFile = File(...)):
    """Read a movie CSV and replace the stored collection."""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix != ".csv":
        logger.warning("Unsupported file format: %s", suffix or "(none)")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{suffix or 'none'}'. Only .csv files are accepted.",
        )

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except pd.errors.EmptyDataError:
        logger.warning("Empty CSV upload: %s", filename)
        raise HTTPException(status_code=400, detail="The uploaded CSV file is empty.")
    except pd.errors.ParserError:
        logger.warning("Could not parse CSV: %s", filename)
        raise HTTPException(status_code=400, detail="The uploaded file could not be parsed as CSV.")
    except UnicodeDecodeError:
        logger.warning("Could not decode CSV: %s", filename)
        raise HTTPException(status_code=400, detail="The uploaded file is not valid UTF-8 text.")

    # Quick validation before saving
    if sorted(df.columns) != sorted(MOVIE_COLUMNS):
        logger.warning("Wrong columns in upload: %s", list(df.columns))
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have exactly these columns: {MOVIE_COLUMNS}",
        )
    if df.empty:
        logger.warning("No data rows in upload: %s", filename)
        raise HTTPException(status_code=400, detail="The uploaded CSV has no data rows.")

    df = df[MOVIE_COLUMNS]

    storage.save(df)
    cache.invalidate()
    logger.info("Uploaded movies: filename=%s, rows=%s", filename, len(df))
    return MovieUploadInfo(
        filename=filename,
        rows=len(df),
        columns=len(df.columns),
        column_names=list(df.columns),
    )


@app.get("/movies", response_model=MovieDataResponse)
def get_movies(limit: int = Query(default=100, ge=1)):
    """Return up to limit stored movies plus the total count."""
    cached = cache.get(limit)
    if cached is not None:
        logger.info("Cache hit: limit=%s", limit)
        return cached

    data = storage.select_columns(MOVIE_COLUMNS, limit)
    total_rows = storage.count_movies()
    response = {"data": data, "total_rows": total_rows}

    cache.set(limit, response)
    logger.info("Retrieved movies: returned_rows=%s", len(data))
    return response


@app.get("/movies/search", response_model=list[dict])
def query_movies(
    columns: list[str] = Query(default=["title"]),
    min_rating: float = Query(default=0, ge=0, le=10),
    limit: int = Query(default=100, ge=1),
):
    """Return id plus selected columns for movies rated at least min_rating."""
    try:
        results = storage.query_movies(columns, min_rating, limit)
    except ValueError as error:
        logger.warning("Invalid search: %s", error)
        raise HTTPException(status_code=400, detail=str(error))

    logger.info(
        "Queried movies: columns=%s, min_rating=%s, returned_rows=%s",
        columns, min_rating, len(results),
    )
    return results


@app.post("/movies/process", response_model=MovieProcessingReport)
def process_movies(config: ProcessConfig):
    """Validate and clean the stored movies using MP1 functions."""
    df = storage.get()
    if df.empty:
        logger.warning("No movies to process")
        raise HTTPException(status_code=400, detail="No movies stored. Upload a CSV first.")

    config_dict = config.model_dump()
    validation = config_dict["validation"]

    for col in validation["numeric_columns"]:
        if col not in df.columns:
            logger.warning("Numeric column not found: %s", col)
            raise HTTPException(status_code=400, detail=f"Numeric column not found: {col}")

    rows_loaded = len(df)
    try:
        validated = validate_dataframe(
            df, validation["required_columns"], validation["numeric_columns"]
        )
        validation_removed = rows_loaded - len(validated)
        logger.info("Validated movies: %d → %d rows", rows_loaded, len(validated))

        df_before = validated.copy()
        processed = process_data(validated, config_dict)
    except ValueError as error:
        logger.warning("Processing failed: %s", error)
        raise HTTPException(status_code=400, detail=str(error))

    report = create_cleaning_report(df_before, processed)
    storage.save(processed)
    cache.invalidate()

    logger.info(
        "Processed movies: validation_removed=%s, rows_before=%s, rows_after=%s",
        validation_removed, report["rows_before"], report["rows_after"],
    )
    return {
        "validation_rows_removed": validation_removed,
        "cleaning_report": report,
    }