"""
Autor: David Duran
Fecha de creación: 05/08/2023
Fecha de moficación: 14/01/2024

Este paquete se utiliza para comprobar si cierta acción va a subir o bajar usando machine learning.
"""

import logging

# Importar funciones locales
from excel_analysis.constants import (
    EXCEL_CONFIGURATIONS,
    INDEX_COLUMN,
    TRAIN_TEST_SPLIT_RATIO,
    RESULTS_BASE_FILE_NAME,
)
from excel_analysis.utils.argument_parser import parse_argumentos
from excel_analysis.utils.cargadores_de_datos import validar_y_cargar_hojas
from excel_analysis.utils.validacion_de_datos import validar_datos_hoja
from excel_analysis.utils.mostrar_resultados import almacenar_y_mostrar_resultados
from excel_analysis.utils.entrenamiento import entrenar_y_predecir
from excel_analysis.utils.sistema_de_calificaciones import (
    asignar_calificaciones_y_actualizar_resultados,
)
from excel_analysis.utils.registro import configurar_registro, establecer_nivel_debug
from excel_analysis.utils.postprocesamiento import (
    calcular_calificaciones_y_umbral,
    guardar_resultados,
    procesar_libro,
)


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
        output_file_prefix = config_name

        valid_sheets, all_data = validar_y_cargar_hojas(file_name, index_column)
        if all_data is None:
            continue

        for sheet_name in valid_sheets:
            if sheet_name in all_data:
                procesar_datos_stock(
                    all_data[sheet_name], sheet_name, results, config["columns"]
                )

        asignar_calificaciones_y_actualizar_resultados(results)
        almacenar_y_mostrar_resultados(results, valid_sheets, output_file_prefix)

    resultado = f"{RESULTS_BASE_FILE_NAME}.xlsx"
    calificaciones_empresas = procesar_libro(resultado)
    guardar_resultados(resultado, calificaciones_empresas)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Sucedió un error: {str(e)}", exc_info=True)
