import logging


def validar_dataframe(df, columnas_requeridas):
    """
    Valida el DataFrame antes de procesar.
    Retorna True si es válido, False en caso contrario.
    """
    if not set(columnas_requeridas).issubset(df.columns):
        return False

    df.dropna(subset=columnas_requeridas, inplace=True)

    if df.empty or df.isnull().any().any():
        return False

    return True


def validar_datos_hoja(df, sheet_name, columnas_requeridas):
    if not validar_dataframe(df, columnas_requeridas):
        logging.warning(
            f"La hoja '{sheet_name}' contiene datos inconsistentes o vacíos. Ignorando esta hoja."
        )
        return False
    return True
