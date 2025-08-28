import pandas as pd

from excel_analysis.utils.data_preprocessors import (
    handle_non_numeric_values,
    normalize_data,
    ensure_float64,
    dividir_datos_entrenamiento_prueba,
)
from excel_analysis.models.neural_networks import entrenar_regresor_mlp


def entrenar_y_predecir(df, feature_columns, detail_column, train_test_split_ratio):
    """
    Entrena el modelo y predice usando el dataframe.
    Retorna el modelo, las predicciones, y Y_test.
    """
    handle_non_numeric_values(df, feature_columns + [detail_column])
    normalize_data(df, feature_columns + [detail_column])
    df = df[pd.to_numeric(df[detail_column], errors="coerce").notnull()]
    df.loc[:, detail_column] = df[detail_column].astype("float")
    ensure_float64(df, feature_columns + [detail_column])

    X_train, Y_train, X_test, Y_test = dividir_datos_entrenamiento_prueba(
        df, feature_columns, detail_column, train_test_split_ratio
    )

    if len(X_train) == 0 or len(Y_train) == 0:
        return None, None, None

    modelo_red_neuronal = entrenar_regresor_mlp(X_train, Y_train)
    y_pred = modelo_red_neuronal.predict(X_test)
    return modelo_red_neuronal, y_pred, Y_test
