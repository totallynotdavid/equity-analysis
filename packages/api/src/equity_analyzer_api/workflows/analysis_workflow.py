import logging
import shutil
import tempfile
from pathlib import Path
from typing import List

from equity_analyzer_core.analysis_runner import run_full_analysis
from fastapi import UploadFile


def process_uploaded_files(files: List[UploadFile]):
    """
    Saves uploaded files to a temporary directory and runs the core analysis.

    Args:
        files: A list of UploadFile objects from the FastAPI request.

    Returns:
        The dictionary of results from the core analysis runner.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        logging.info(f"Created temporary directory for analysis: {temp_path}")

        for file in files:
            file_path = temp_path / file.filename
            try:
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                logging.info(f"Saved uploaded file to {file_path}")
            finally:
                file.file.close()

        # Call the synchronous analysis function
        results = run_full_analysis(data_directory=temp_path)

    logging.info("Temporary directory cleaned up. Analysis complete.")
    return results