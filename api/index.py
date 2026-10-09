import os

from pathlib import Path

from fastapi import FastAPI
from index_api.main import app as api


# The API reads INDEX_DB when it opens the database, so set it before requests.
os.environ.setdefault(
    "INDEX_DB", str(Path(__file__).resolve().parent.parent / "data" / "deploy.sqlite")
)

# Vercel rewrites /api/* to this function, so preserve the /api root path.
api.root_path = "/api"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api)
