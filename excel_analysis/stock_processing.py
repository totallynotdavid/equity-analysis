"""
Autor: David Duran
Fecha de creación: 05/08/2023
Fecha de moficación: 14/01/2024

Este paquete se utiliza para comprobar si cierta acción va a subir o bajar usando machine learning.
"""

import logging
import numpy as np

# Importar funciones locales
from excel_analysis.constants import (
    EXCEL_CONFIGURATIONS,
    SheetResult,
    INDEX_COLUMN,
    TRAIN_TEST_SPLIT_RATIO,
)
from excel_analysis.utils.data_loaders import validate_and_load_sheets
from excel_analysis.models.neural_networks import obtener_threshold_optimo
from excel_analysis.utils.grading_system import (
    assign_stock_grade,
    assign_grades_and_update_results,
)
from excel_analysis.utils.display_results import store_and_display_results
from excel_analysis.utils.data_validation import validar_datos_hoja
from excel_analysis.utils.entrenamiento import entrenar_y_predecir
from excel_analysis.utils.argument_parser import parse_argumentos
from excel_analysis.utils.logging import configurar_registro, establecer_nivel_debug


def calcular_calificaciones_y_umbral(df, y_pred, Y_test, price_column, sheet_name):
    # Asignar una calificación a la acción
    stock_grade = assign_stock_grade(df, y_pred, Y_test, price_column)
    predicted_return = np.sum(y_pred)

    # Obtener el umbral (threshold) óptimo
    optimal_threshold = obtener_threshold_optimo(Y_test, y_pred)
    conteo_positivos_reales = np.sum(Y_test)
    conteo_positivos_predichos = np.sum((y_pred > optimal_threshold).astype(int))

    final_value = conteo_positivos_reales - conteo_positivos_predichos

    return SheetResult(
        sheet_name,
        final_value,
        stock_grade,
        optimal_threshold,
        predicted_return,
        None,
        None,
    )


def process_stock_data(df, sheet_name, results_list, columns):
    """
    Procesar los datos de una hoja de cálculo para predecir el comportamiento de una acción.
    Esta función se encarga de entrenar el modelo, predecir los valores y asignar una calificación a la acción.
    Llama a las funciones de otros módulos para realizar estas tareas.

    Parámetros:
    - df: DataFrame que contiene los datos de la acción.
    - sheet_name: Nombre de la hoja de cálculo.
    - results_list: Lista donde se almacenan los resultados de cada acción.
    """
    columnas_requeridas = [
        columns["price"],
        columns["detail"],
    ] + columns["features"]

    if not validar_datos_hoja(df, sheet_name, columnas_requeridas):
        return

    logging.info(f"Procesando stock: {sheet_name}")

    modelo, y_pred, Y_test = entrenar_y_predecir(
        df, columns["features"], columns["detail"], TRAIN_TEST_SPLIT_RATIO
    )

    if modelo is None:
        logging.warning(
            f"La hoja '{sheet_name}' no tiene suficientes datos para entrenar el modelo. Ignorando esta hoja."
        )
        return

    resultado_hoja = calcular_calificaciones_y_umbral(
        df, y_pred, Y_test, columns["price"], sheet_name
    )
    results_list.append(resultado_hoja)

    logging.info(
        f"💰 Valor final de esta hoja: {resultado_hoja.final_value}, Threshold: {resultado_hoja.optimal_threshold}, Grado: {resultado_hoja.stock_grade}"
    )


# Programa principal
def main():
    args = parse_argumentos()
    configurar_registro()

    if args.debug:
        establecer_nivel_debug()
    else:
        logging.getLogger().setLevel(logging.ERROR)

    results = []

    for config_name, config in EXCEL_CONFIGURATIONS.items():
        logging.info(f"📂 Procesando archivo: {config_name}")
        file_name = config["file_name"]
        index_column = INDEX_COLUMN

        valid_sheets, all_data = validate_and_load_sheets(file_name, index_column)
        if all_data is None:
            continue

        for sheet_name in valid_sheets:
            if sheet_name in all_data:
                process_stock_data(
                    all_data[sheet_name], sheet_name, results, config["columns"]
                )

    assign_grades_and_update_results(results)
    store_and_display_results(results, valid_sheets)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Sucedió un error: {str(e)}")
