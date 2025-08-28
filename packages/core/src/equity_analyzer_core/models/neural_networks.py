from sklearn.neural_network import MLPRegressor
from sklearn import metrics
import numpy as np
import pandas as pd


def entrenar_regresor_mlp(X_train, Y_train):
    """
    Entrena el modelo MLPRegressor con los datos de entrenamiento proporcionados.

    Parámetros:
    - X_train (tipo array): Características para el entrenamiento.
    - Y_train (tipo array): Valores objetivo para el entrenamiento.

    Retorna:
    - modelo_red_neuronal (MLPRegressor): Modelo entrenado.
    """
    modelo_red_neuronal = MLPRegressor(
        activation="logistic", hidden_layer_sizes=(200), max_iter=1000, solver="adam"
    )
    modelo_red_neuronal.fit(X_train, Y_train)
    return modelo_red_neuronal


def obtener_threshold_optimo(Y_test, y_pred):
    """
    Calcula el umbral óptimo para la clasificación basado en la curva ROC.

    Parámetros:
    - Y_test (tipo array): Valores objetivo verdaderos.
    - y_pred (tipo array): Valores predichos.

    Retorna:
    - optimal_threshold (float): Valor del umbral óptimo calculado.
    """
    fpr, tpr, thresholds = metrics.roc_curve(
        Y_test, y_pred
    )  # fpr = Tasa de falsos positivos que salen positivos, tpr = Tasa de positivos que salen positivos
    i = np.arange(len(tpr))
    roc = pd.DataFrame(
        {
            "fpr": pd.Series(fpr, index=i),
            "tpr": pd.Series(tpr, index=i),
            "1-fpr": pd.Series(1 - fpr, index=i),
            "tf": pd.Series(tpr - (1 - fpr), index=i),
            "thresholds": pd.Series(thresholds, index=i),
        }
    )
    optimal_threshold = roc.iloc[(roc.tf - 0).abs().argsort()[:1]]["thresholds"].values[
        0
    ]
    return optimal_threshold


def compute_predicted_return(model, X_test):
    """
    Calcula la suma de los retornos predichos usando el modelo.

    Parámetros:
    - model (MLPRegressor): Modelo MLP entrenado.
    - X_test (tipo array): Características para la predicción.

    Retorna:
    - predicted_return (float): Suma total de retornos predichos.
    """
    return np.sum(model.predict(X_test))
