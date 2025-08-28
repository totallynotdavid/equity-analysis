import numpy as np


def compute_predicted_return(model, X_test):
    """
    Calcular el retorno predicho basado en las predicciones del modelo.

    Parámetros:
    - model: Modelo entrenado.
    - X_test: Datos de prueba.

    Retorna:
    - Valor de retorno predicho.
    """
    return np.sum(model.predict(X_test))


def clean_daily_returns(stock_data, price_column):
    """
    Procesar los datos de la acción para calcular los retornos diarios y manejar valores NaN o infinitos.

    Parámetro:
    - stock_data: DataFrame que contiene los datos.

    Retorna:
    - Retornos diarios de la acción limpios.
    """
    daily_returns = stock_data[price_column].pct_change().dropna()
    daily_returns.replace([np.inf, -np.inf], np.nan, inplace=True)
    daily_returns.interpolate(inplace=True)
    daily_returns.fillna(method="ffill", inplace=True)
    daily_returns.fillna(method="bfill", inplace=True)
    return daily_returns


def assign_grade_from_quantiles(value, cuantiles, grades):
    """
    Asignar una calificación basada en la posición del valor en relación con los cuantiles proporcionados.

    Parámetro:
    - value: El valor a evaluar.
    - cuantiles: Lista de cuantiles.
    - grades: Lista de calificaciones.

    Retorna:
    - Grado asignado.
    """
    for q, grade in zip(cuantiles, grades):
        if value <= q:
            return grade
    return grades[-1]


def assign_stock_grade(stock_data, y_pred, Y_test, price_column):
    """
    Asignar una calificación a la acción basada en el error de predicción y la volatilidad.

    Parámetros:
    - stock_data: DataFrame que contiene los datos
    - y_pred: Predicciones del modelo.
    - Y_test: Valores reales.

    Returns:
    - Grade (A, B, C, D, E).
    """
    prediction_error = np.mean(np.abs(y_pred - Y_test))
    volatility = clean_daily_returns(stock_data, price_column).std()

    error_quantiles = [0.4, 0.5, 0.6, 0.7]
    volatility_quantiles = [0.6, 0.8, 1.0, 1.2]

    # Decision conditions
    conditions = [
        (
            prediction_error <= error_quantiles[0]
            and volatility <= volatility_quantiles[0]
        ),
        (
            prediction_error <= error_quantiles[1]
            and volatility <= volatility_quantiles[1]
        ),
        (
            prediction_error <= error_quantiles[2]
            and volatility <= volatility_quantiles[2]
        ),
        (
            prediction_error <= error_quantiles[3]
            and volatility <= volatility_quantiles[3]
        ),
    ]

    grades = ["A", "B", "C", "D", "E"]

    for condition, grade in zip(conditions, grades):
        if condition:
            return grade

    return grades[-1]


def assign_performance_grade(predicted_returns):
    """
    Asignar una calificación de rendimiento basada en el rendimiento futuro predicho.

    Parámetro:
    - predicted_returns: Lista de retornos predichos para cada acción.

    retorna:
    - Lista de calificaciones por rendimiento.
    """
    cuantiles = [
        np.quantile(predicted_returns, 0.2),
        np.quantile(predicted_returns, 0.4),
        np.quantile(predicted_returns, 0.6),
        np.quantile(predicted_returns, 0.8),
    ]
    grades = ["E", "D", "C", "B", "A"]

    return [
        assign_grade_from_quantiles(pr, cuantiles, grades) for pr in predicted_returns
    ]


def assign_final_value_grade(final_values):
    """
    Asignar una calificación basada en el valor final.

    Parámetros:
    - final_values: Lista de valores finales para cada acción.

    Retorna:
    - Lista de calificaciones.
    """
    cuantiles = [
        np.quantile(final_values, 0.2),
        np.quantile(final_values, 0.4),
        np.quantile(final_values, 0.6),
        np.quantile(final_values, 0.8),
    ]
    grades = ["A", "B", "C", "D", "E"]

    return [assign_grade_from_quantiles(fv, cuantiles, grades) for fv in final_values]


def asignar_calificaciones_y_actualizar_resultados(results):
    # Calcular el rendimiento esperado de cada acción
    predicted_returns = [result.predicted_return for result in results]
    performance_grades = assign_performance_grade(predicted_returns)

    # Asignar una calificación utilizando el valor final de cada acción
    final_value_grades = assign_final_value_grade(
        [result.final_value for result in results]
    )

    for index, result in enumerate(results):
        updated_result = result._replace(
            performance_grade=performance_grades[index],
            final_value_grade=final_value_grades[index],
        )
        results[index] = updated_result
