"""
Autor: David Duran
Fecha: 05/08/2023

Este módulo se utiliza para comprobar si cierta acción va a subir o bajar usando machine learning.
Los pasos que se siguen son:
1. Cargar los datos de un archivo Excel
2. Normalizar los datos
3. Dividir los datos en training y test data
4. Clasificar los datos con SVC (Support Vector Classification)
5. Entrenar un modelo de red neuronal
6. Obtener el umbral (threshold) óptimo
7. Comparar el último valor de y_pred con el umbral óptimo
"""

# Todo
# 1. Manejo en caso de que un valor de los datos sea NaN, usar el valor anterior
# 2. Manejo de excepciones en caso de que el archivo Excel no exista o no se pueda abrir
# 3. Manejo de excepciones en caso de que el archivo Excel no tenga las columnas que se esperan

import os
import pandas as pd
import numpy as np
from sklearn import svm, metrics
from sklearn.neural_network import MLPRegressor

# Constantes
EXCEL_FILE_NAME = 'Base2.xlsx'
COLUMN_NAMES = {
    "price": "Precio",
    "features": ['Movilveintiuno', 'Movilcincocinco', 'Movilunocuatrocuatro', 'Momentdiez', 'Momentsetenta', 'Momenttrescerocero'],
    "detail": 'Detalle',
}

# Helper functions (Son utilizados por pytest en test_data_check)
def check_data_size(df):
    return df.size

def check_data_shape(df):
    return df.shape

def check_null_values(df):
    return df.isnull().sum()

def check_top_5_price_counts(df):
    return df[COLUMN_NAMES["price"]].value_counts().head(5)

def get_column_names(df):
    return df.columns

def get_head(df, number_of_rows=5):
    return df[[COLUMN_NAMES["price"]] + COLUMN_NAMES["features"]].head(number_of_rows)

# Preprocessing
def load_data(file_name=EXCEL_FILE_NAME, single_sheet=False): # single_sheet = False para modo de producción
    """
    Cargar los datos de un archivo Excel con múltiples hojas
    """
    xls = pd.ExcelFile(file_name, engine='openpyxl')
    if single_sheet:
        return pd.read_excel(xls, xls.sheet_names[0], index_col='FECHA')
    else:
        all_sheets = {}
        for sheet_name in xls.sheet_names:
            sheet_data = pd.read_excel(xls, sheet_name, index_col='FECHA')
            if not sheet_data.empty:  # Ignorar hojas vacías
                all_sheets[sheet_name] = sheet_data
        return all_sheets

def ensure_float64(df):
    """
    Prueba de que todas las columnas son de tipo float64
    """
    if not all(df.dtypes == 'float64'):
        raise ValueError("Todas las columnas deben ser de tipo float64")

# Processing
def normalize_column(df, column_name):
    """
    Normalizar datos en una columna específica del dataframe
    """
    df[column_name] = (df[column_name] - df[column_name].min()) / (df[column_name].max() - df[column_name].min())

def normalize_data(df):
    """
    Normalizar datos en columnas específicas del dataframe
    """
    columns_to_normalize = [COLUMN_NAMES["price"]] + COLUMN_NAMES["features"]
    for column in columns_to_normalize:
        normalize_column(df, column)

def get_training_and_test_data(df):
    """
    Dividir el dataframe en training y test data
    """
    division1_df = df.iloc[0:2886, 0:9]
    division2_df = df.iloc[2887:3607, 0:9]

    feature_cols = COLUMN_NAMES["features"]
    
    X_train = division1_df[feature_cols].values
    Y_train = division1_df['Detalle'].values.astype('float')
    
    X_test = division2_df[feature_cols].values
    Y_test = division2_df['Detalle'].values.astype('float')

    ensure_float64(df)

    return X_train, Y_train, X_test, Y_test

def train_svm(X_train, Y_train):
    """
    Clasificar los datos con SVC (Support Vector Classification)
    """
    clf = svm.SVC(kernel='poly', degree=3)
    clf.fit(X_train, Y_train)
    return clf

def train_neural_network(X_train, Y_train):
    """
    Entrenar un modelo de red neuronal
    """
    nn = MLPRegressor(activation='logistic', hidden_layer_sizes=(200), max_iter=1000, solver='adam')
    nn.fit(X_train, Y_train)
    return nn

def get_optimal_threshold(Y_test, y_pred):
    fpr, tpr, thresholds = metrics.roc_curve(Y_test, y_pred) # fpr = Tasa de falsos positivos que salen positivos, tpr = Tasa de positivos que salen positivos
    i = np.arange(len(tpr))
    roc = pd.DataFrame({'fpr': pd.Series(fpr, index=i), 'tpr': pd.Series(tpr, index=i), '1-fpr': pd.Series(1-fpr, index=i), 'tf': pd.Series(tpr - (1-fpr), index=i), 'thresholds': pd.Series(thresholds, index=i)})
    optimal_threshold = roc.iloc[(roc.tf-0).abs().argsort()[:1]]['thresholds'].values[0]
    return optimal_threshold

def main():
    test_mode = os.environ.get('TEST_MODE', 'False') == 'True'
    all_data = load_data(single_sheet=False)

    if test_mode:
        # Una sola hoja para modo de prueba
        print("Ejecutando en modo de prueba")
        df = all_data[list(all_data.keys())[0]]
        process_stock_data(df, "Test Sheet")
    else:
        # Múltiples hojas para modo de producción
        number_of_sheets = len(all_data)
        print(f"Encontramos {number_of_sheets} hojas en el archivo Excel")
        for sheet_name, df in all_data.items():
            process_stock_data(df, sheet_name)

def process_stock_data(df, sheet_name):
    # Revisar que el dataframe tenga todas las columnas esperadas (price, detail y features)
    expected_columns = [COLUMN_NAMES["price"], COLUMN_NAMES["detail"]] + COLUMN_NAMES["features"]
    if not all(column in df.columns for column in expected_columns):
        print(f"🚨 Error: La hoja '{sheet_name}' no contiene todas las columnas esperadas. Ignorando esta hoja.")
        print(f"--------------------------------")
        return

    print(f"Trabajando en el stock: {sheet_name}")

    normalize_data(df)
    df = df[pd.to_numeric(df['Detalle'], errors='coerce').notnull()]
    df.loc[:, 'Detalle'] = df['Detalle'].astype('float')

    X_train, Y_train, X_test, Y_test = get_training_and_test_data(df)

    # Modelo de red neuronal
    nn = train_neural_network(X_train, Y_train)
    y_pred = nn.predict(X_test)

    # Obtener el umbral (threshold) óptimo
    optimal_threshold = get_optimal_threshold(Y_test, y_pred)

    # Comparar el último valor de y_pred con el umbral óptimo
    last_y_pred = y_pred[-1]
    print(f"Último valor de y_pred: {last_y_pred} y umbral óptimo: {optimal_threshold}")
    if last_y_pred > optimal_threshold:
        print(f"Los precios de {sheet_name} subirán 📈")
    else:
        print(f"Los precios de {sheet_name} bajarán 📉")
    
    print("--------------------------------")

if __name__ == "__main__":
    main()
