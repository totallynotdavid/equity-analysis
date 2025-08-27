import argparse


def parse_argumentos():
    """
    Parsea y valida los argumentos proporcionados al script.
    """
    parser = argparse.ArgumentParser(
        description="Analiza hojas de cálculo para predecir el comportamiento de las acciones utilizando machine learning."
    )
    parser.add_argument(
        "--debug",
        help="Activa el modo de depuración para obtener una salida detallada del proceso.",
        type=lambda x: (str(x).lower() == "true"),
        default=False,
    )
    return parser.parse_args()
