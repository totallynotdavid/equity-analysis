from collections import namedtuple

RESULTS_BASE_FILE_NAME = "resultados"  # Nombre base para los archivos de resultados

INDEX_COLUMN = "FECHA"
TRAIN_TEST_SPLIT_RATIO = 0.8

WEIGHTS = {
    "data_diaria": 0.3,
    "data_mensual": 0.5,
    "data_mensual_macro": 0.2,
}

EXCEL_CONFIGURATIONS = {
    "data_diaria": {
        "file_name": "MEXBOL.xlsx",
        "columns": {
            "price": "PX_LAST",
            "features": [
                "BB_MA",
                "BB_UPPER",
                "BB_LOWER",
                "BB_WIDTH",
                "BB_PERCENT",
                "MOMENTUM",
                "MOM_MA",
                "MACD",
                "MACD_SIGNAL",
                "MACD_DIFF",
                "TAS_K",
                "TAS_D",
                "TAS_DS",
                "TAS_DSS",
                "WLPR",
                "DMI_PLUS",
                "DMI_MINUS",
                "ADX",
                "ADXR",
                "PX_MID",
                "PX_LOW",
                "PX_HIGH",
                "PX_OPEN",
                "PX_VOLUME",
                "RSI_14D",
                "RSI_30D",
                "RSI_9D",
                "RSI_3D",
            ],
            "detail": "DETALLE",
        },
    },
    "data_mensual": {
        "file_name": "IFMEXICO.xlsx",
        "columns": {
            "price": "PX_LAST",
            "features": [
                "EV_TO_T12M_SALES",
                "EV_TO_T12M_EBITDA",
                "EV_TO_T12M_EBIT",
                "RETURN_ON_ASSET",
                "RETURN_ON_CAP",
                "IS_DILUTED_EPS",
                "HISTORICAL_MARKET_CAP",
                "TRAIL_12M_NET_SALES",
                "NET_INCOME",
                "EBITDA",
                "GROSS_MARGIN",
                "OPER_MARGIN",
                "PROF_MARGIN",
            ],
            "detail": "DETALLE",
        },
    },
    "data_mensual_macro": {
        "file_name": "IEMEXICO.xlsx",
        "columns": {
            "price": "PX_LAST",
            "features": [
                "Inflacion_Mensual",
                "PMI_Manufacturero",
                "Confianza_Consumidor",
                "IMEF_Manufacturero",
                "Tasa_Desempleo",
                "Inflacion_Anual",
                "Tasa_de_Interes",
                "IPP_SIN_ALIMENTOS",
                "Produccion_Industrial",
                "Precios_al_Productor",
                "Balanza_Cuenta_Corriente",
            ],
            "detail": "DETALLE",
        },
    },
}

SheetResult = namedtuple(
    "SheetResult",
    [
        "sheet_name",
        "final_value",
        "grade",
        "optimal_threshold",
        "predicted_return",
        "performance_grade",
        "final_value_grade",
    ],
    defaults=[None],
)
