"""Excepciones específicas del agente. Cada una corresponde a un escenario de error del protocolo."""


class ErrorAgente(Exception):
    tipo = "error"


class ErrorMEXC(ErrorAgente):
    """API de MEXC sin respuesta, con límite de solicitudes o con datos inválidos."""
    tipo = "mexc_sin_respuesta"


class ErrorDatoAtrasado(ErrorAgente):
    """La última vela cerrada es más antigua que el límite configurado."""
    tipo = "dato_atrasado"


class ErrorDatos(ErrorAgente):
    """Velas desordenadas, duplicadas, con huecos o con precios no válidos."""
    tipo = "datos_invalidos"


class ErrorOllama(ErrorAgente):
    """El gateway u Ollama no responden."""
    tipo = "ollama_detenido"


class ErrorRespuestaLLM(ErrorAgente):
    """El LLM devolvió JSON inválido o una acción/símbolo/fecha no permitidos."""
    tipo = "json_invalido"


class ErrorSaldoInsuficiente(ErrorAgente):
    """La orden Paper no alcanza el tamaño mínimo o no hay saldo."""
    tipo = "saldo_insuficiente"


class ErrorEventoDuplicado(ErrorAgente):
    """La vela ya fue procesada; no se vuelve a operar."""
    tipo = "evento_duplicado"
