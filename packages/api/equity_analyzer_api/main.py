from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class Health(BaseModel):
    status: str
    as_of: date | None


app = FastAPI(
    title="Equity Analysis API",
    description="Read-only access to daily stock scores.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4321", "http://127.0.0.1:4321"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Health:
    return Health(status="ok", as_of=None)
