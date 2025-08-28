"""
Autor: David Duran
Fecha de creación: 05/08/2023
Fecha de moficación: 14/01/2024
Este paquete se utiliza para comprobar si cierta acción va a subir o bajar usando machine learning.
"""

import logging

from equity_analyzer_core.constants import TRAIN_TEST_SPLIT_RATIO
from equity_analyzer_core.utils.entrenamiento import entrenar_y_predecir
from equity_analyzer_core.utils.postprocesamiento import (
    calcular_calificaciones_y_umbral,
)
from equity_analyzer_core.utils.validacion_de_datos import validar_datos_hoja


def procesar_datos_stock(df, sheet_name, results_list, columns):
    """
    Procesar los datos de una hoja de cálculo para predecir el comportamiento de una acción.
    Esta función se encarga de entrenar el modelo, predecir los valores y asignar una calificación a la acción.
    Llama a las funciones de otros módulos para realizar estas tareas.
    Parámetros:
    - df: DataFrame que contiene los datos de la acción.
    - sheet_name: Nombre de la hoja de cálculo.
    - results_list: Lista donde se almacenan los resultados de cada acción.
    - columns: Diccionario que contiene los nombres de las columnas del DataFrame.
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
        f"💰 Valor final de esta hoja: {resultado_hoja.final_value}, Threshold: {resultado_hoja.optimal_threshold}, Grado: {resultado_hoja.grade}"
    )
