import json
import os

import pandas as pd


def _as_dict(result):
    return result if isinstance(result, dict) else result._asdict()


def store_results_to_json(results, filename, key="Default"):
    """
    Guarda los resultados en un archivo JSON.

    Argumentos:
    - results (list): Lista de resultados.
    - filename (str): Nombre del archivo donde guardar los resultados.
    - key (str): Key representando el nombre original del archivo.
    """
    if os.path.exists(filename):
        with open(filename) as file:
            data = json.load(file)
    else:
        data = {}

    # Convertir los resultados a un formato de diccionario
    results_dict = [_as_dict(result) for result in results]

    # Anexar los nuevos resultados bajo la key específica
    data[key] = results_dict

    with open(filename, "w") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def store_results_to_excel(results, filename, sheet_name="Sheet1"):
    """
    Guarda los resultados en un archivo Excel.

    Argumentos:
    - results (lista): Lista de resultados.
    - filename (str): Nombre del archivo donde guardar los resultados.
    """
    df = pd.DataFrame([_as_dict(r) for r in results])

    file_exists = os.path.isfile(filename)

    with pd.ExcelWriter(
        filename, mode="a" if file_exists else "w", engine="openpyxl"
    ) as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
