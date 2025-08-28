import argparse
import json
import logging

from pathlib import Path

from equity_analyzer_core.analysis_runner import run_full_analysis
from equity_analyzer_core.constants import RESULTS_BASE_FILE_NAME
from equity_analyzer_core.store_data import store_results_to_excel
from equity_analyzer_core.utils.registro import (
    configurar_registro,
    establecer_nivel_debug,
)


def main():
    """Main entry point for the CLI application."""
    parser = argparse.ArgumentParser(
        description="Runs equity analysis on Excel files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "data",
        help="Directory containing the input Excel files (e.g., MEXBOL.xlsx).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "outputs",
        help="Directory to save the analysis results.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for detailed output.",
    )
    args = parser.parse_args()

    configurar_registro()
    if args.debug:
        establecer_nivel_debug()

    if not args.data_dir.is_dir():
        logging.error(f"Error: Data directory not found at '{args.data_dir}'")
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Starting analysis on files in: {args.data_dir}")

    # Call the core logic
    results_data = run_full_analysis(args.data_dir)

    if not results_data:
        logging.error(
            "Analysis finished with no results. Please check input files and logs."
        )
        return

    # The CLI is responsible for handling outputs
    json_output_path = args.output_dir / f"{RESULTS_BASE_FILE_NAME}.json"
    excel_output_path = args.output_dir / f"{RESULTS_BASE_FILE_NAME}.xlsx"

    # Save to JSON
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(results_data, f, ensure_ascii=False, indent=4)
    logging.info(f"JSON results saved to {json_output_path}")

    # Save to Excel
    for config_name, results_list in results_data.items():
        if results_list:  # Ensure there are results before trying to save
            store_results_to_excel(
                results_list,
                filename=str(excel_output_path),
                sheet_name=config_name,
            )
    logging.info(f"Excel results saved to {excel_output_path}")

    for config_name, results in results_data.items():
        if not results:
            continue
        for _result in results[:5]:
            pass


if __name__ == "__main__":
    main()
