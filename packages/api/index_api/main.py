from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from index_core.report import ScoresReport, TickerReport  # noqa: TC002
from index_core.sources.base import normalise_ticker
from index_core.store import Store, StoreError, default_db_path
from pydantic import BaseModel


# FastAPI evaluates route annotations at runtime, so keep `ScoresReport` and
# `TickerReport` imported at module scope.


class Health(BaseModel):
    status: str
    as_of: date | None


app = FastAPI(
    title="index API",
    description="Read-only access to daily stock scores. Experimental, not validated.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4321", "http://127.0.0.1:4321"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _latest_report() -> ScoresReport | None:
    path = default_db_path()
    if not path.exists():
        return None
    try:
        with Store.open(path, read_only=True) as store:
            return store.latest_report()
    except StoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/health")
def health() -> Health:
    report = _latest_report()
    return Health(status="ok", as_of=report.as_of if report else None)


@app.get("/scores")
def scores() -> ScoresReport:
    report = _latest_report()
    if report is None:
        raise HTTPException(status_code=404, detail="no scores have been computed")
    return report


@app.get("/tickers/{ticker}")
def ticker_report(ticker: str) -> TickerReport:
    """The fundamentals of one ticker, each figure with the filing it is from."""
    path = default_db_path()
    found = None
    if path.exists():
        try:
            with Store.open(path, read_only=True) as store:
                found = store.ticker_report(normalise_ticker(ticker))
        except StoreError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    if found is None:
        raise HTTPException(status_code=404, detail=f"no report for {ticker}")
    return found
