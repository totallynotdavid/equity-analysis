import logging
from pathlib import Path

from equity_analyzer_core.constants import EXCEL_CONFIGURATIONS, INDEX_COLUMN
from equity_analyzer_core.stock_processing import procesar_datos_stock
from equity_analyzer_core.utils.cargadores_de_datos import validar_y_cargar_hojas
from equity_analyzer_core.utils.sistema_de_calificaciones import (
    asignar_calificaciones_y_actualizar_resultados,
)


def run_full_analysis(data_directory: Path):
    """
    Runs the full analysis on Excel files found in the given directory.
    This is the main, decoupled entry point for the core logic.

    Args:
        data_directory: A Path object pointing to the directory with input .xlsx files.

    Returns:
        A dictionary containing the analysis results, keyed by config name.
    """
    all_results_data = {}
    for config_name, config in EXCEL_CONFIGURATIONS.items():
        file_path = data_directory / config["file_name"]
        if not file_path.exists():
            logging.warning(f"File not found, skipping analysis for: {file_path}")
            continue

        logging.info(f"📂 Processing file config '{config_name}' from: {file_path}")
        valid_sheets, all_data = validar_y_cargar_hojas(str(file_path), INDEX_COLUMN)
        if all_data is None:
            continue

        results_for_config = []
        for sheet_name in valid_sheets:
            if sheet_name in all_data:
                # This function now correctly populates `results_for_config`
                procesar_datos_stock(
                    all_data[sheet_name],
                    sheet_name,
                    results_for_config,
                    config["columns"],
                )

        if not results_for_config:
            logging.warning(f"No valid data processed for config '{config_name}'.")
            continue

        asignar_calificaciones_y_actualizar_resultados(results_for_config)

        # Sort results by final_value before returning
        sorted_results = sorted(
            results_for_config, key=lambda x: x.final_value, reverse=True
        )

        # Convert namedtuples to dictionaries for easy JSON serialization
        all_results_data[config_name] = [result._asdict() for result in sorted_results]

    return all_results_data
