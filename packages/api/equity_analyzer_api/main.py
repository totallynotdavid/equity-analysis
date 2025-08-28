import logging

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from equity_analyzer_api.workflows import analysis_workflow


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Equity Analysis API",
    description="Upload Excel files to run stock analysis.",
    version="1.0.0",
)

origins = [
    "http://localhost:4321",
    "http://127.0.0.1:4321",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze/")
async def analyze_excel_files(files: list[UploadFile]):
    """
    Accepts a list of Excel files, runs the analysis, and returns the results.

    The frontend must send all required files as defined in the core constants
    (e.g., MEXBOL.xlsx, IFMEXICO.xlsx, IEMEXICO.xlsx).
    """
    required_files = {"MEXBOL.xlsx", "IFMEXICO.xlsx", "IEMEXICO.xlsx"}
    uploaded_filenames = {file.filename for file in files}
    if not required_files.issubset(uploaded_filenames):
        missing = required_files - uploaded_filenames
        raise HTTPException(
            status_code=400,
            detail=f"Missing required files. Please upload all of: {', '.join(missing)}",
        )
    try:
        results = await run_in_threadpool(
            analysis_workflow.process_uploaded_files, files=files
        )
        if not results:
            raise HTTPException(
                status_code=422,
                detail="Analysis completed but produced no results. Check file contents.",
            )
        return results
    except Exception as e:
        logger.error(f"An error occurred during analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An internal server error occurred during analysis."
        )
