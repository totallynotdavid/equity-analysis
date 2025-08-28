import logging


def configurar_registro(nivel=logging.INFO):
    """
    Configura la configuración de registro para la aplicación.

    :param nivel: Nivel de registro, por defecto es logging.INFO.
    """
    formato = "%(asctime)s - %(levelname)s - %(message)s"
    logging.basicConfig(format=formato, level=nivel)


def establecer_nivel_debug():
    """
    Establece el nivel de registro en DEBUG.
    """
    logging.getLogger().setLevel(logging.DEBUG)


def establecer_nivel_error():
    """
    Establece el nivel de registro en ERROR.
    """
    logging.getLogger().setLevel(logging.ERROR)
