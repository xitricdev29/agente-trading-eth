# Informe del proyecto final: agente de trading ETH/USDT con LLM local

**Autor:** Sebastián López · **Materia:** Deep Learning para Finanzas · **Profesor:** Rubén Miguel Rodríguez Rangel · Universidad Iberoamericana León
**Repositorio:** https://github.com/xitricdev29/agente-trading-eth · **Versión:** 0.3.1

> Las cifras de este informe provienen de los registros del agente. Las tablas completas y actualizadas están en [`RESULTADOS.md`](RESULTADOS.md), que se regenera con `python scripts/generar_informe.py`. Las cifras marcadas con ⟳ se actualizan antes de entregar.

---

## 1. Objetivo y alcance
Construir un agente que observe velas diarias de ETH/USDT en MEXC, consulte el modelo predictivo de la práctica, pida una propuesta a un **LLM ejecutado localmente con Ollama**, aplique controles de riesgo deterministas y ejecute **solo en modo Paper** (50,000 USDT simulados, 0% de comisión). El agente **no envía órdenes reales**: no existe ninguna ruta de código que opere en MEXC.

## 2. Arquitectura y tecnologías
UI en **React + TypeScript + Vite** → backend **FastAPI** (Python) → datos públicos de **MEXC**, modelo **PyTorch**, **gateway con API key** → **Ollama local**, motor de riesgo, motor Paper y persistencia **SQLite**. El diagrama, la estructura y las instrucciones están en el [README](../README.md).

**Ciclo (una decisión por vela diaria cerrada):**
1. Observar y validar los datos.
2. Calcular las variables con el contrato del entrenamiento.
3. Obtener el pronóstico.
4. Consultar al LLM local, que puede llamar herramientas de solo lectura.
5. Validar el JSON de la respuesta.
6. Aplicar los controles de riesgo.
7. Ejecutar en Paper en la apertura de t+1.
8. Registrar todo y mostrarlo en la UI.

## 3. Modelo predictivo
MLP de 32 neuronas con 20 rezagos de rendimiento diario, **entrenado en Colab con GPU Tesla T4** (PyTorch 2.14, CUDA 13.0). El pronóstico es el promedio de 3 semillas, y la regla del capítulo 9 es comprar si el pronóstico es mayor que δ = 0.05%. `backend/modelo/contrato.json` versiona las variables, los escaladores (ajustados solo con TR), la arquitectura, el umbral y la procedencia. Los pesos se exportaron del notebook ejecutado en T4 con `scripts/exportar_modelo.py` (`procedencia.valido_t4 = true`).

**Limitación conocida (práctica, capítulos 8, 9 y 11):** el modelo no supera de forma distinguible al pronóstico cero, ni en VA ni en TE. El agente no promete rentabilidad: su objetivo es un ciclo de decisión **seguro, trazable y evaluable**.

## 4. LLM local: evidencia
| Concepto | Valor registrado (24 nov 2026) |
|---|---|
| Ollama | v0.40.17 |
| Modelo | `llama3.2:3b` (3.2B parámetros, cuantización Q4_K_M) |
| Hardware | Intel Core i7-11800H @ 2.30 GHz, 12.6 GB RAM |
| Procesador de inferencia | mixto: 80% del modelo en GPU |
| Latencia de prueba | 324 ms; 3.5 s promedio por decisión del agente (incluye la ronda de herramientas) |
| Dirección | gateway 127.0.0.1:8100 → Ollama 127.0.0.1:11434 (local) |
| Prueba sin red | ⟳ `scripts/evidencia_ollama.py --sin-red` con el Wi-Fi apagado |

## 5. Herramientas, permisos y asesor externo
El LLM puede consultar tres herramientas **de solo lectura**: velas recientes cerradas, su historial de decisiones y los límites de riesgo. Funcionan con lista blanca y un máximo de 3 llamadas por decisión; cualquier otra llamada se rechaza y queda registrada. Ninguna herramienta opera, cambia la configuración ni ve datos posteriores a la vela.

El **asesor externo** proporcionado por el profesor se integra como **dato adicional**. Su clave vive solo en el `.env` del servidor. La decisión la sigue proponiendo el LLM local y la valida el motor de riesgo; si el asesor falla, el ciclo continúa sin él. ⟳ Resultado de su uso, si se configura.

## 6. Seguridad
- **Ollama no tiene autenticación por defecto:** el gateway exige `Bearer <clave>`, compara con `hmac.compare_digest` y solo permite `/api/chat`, `/tags`, `/version`, `/show` y `/ps`. Descargar o borrar modelos queda bloqueado.
- **Los controles del agente** (iniciar, pausar, detener, liquidar, ciclo, replay y evidencia) exigen `X-API-Key`. Los intentos inválidos se rechazan con 401 y se registran.
- **Secretos solo en `backend/.env`** (excluido del repositorio). El frontend no contiene claves. Una prueba automatizada verifica que **el LLM no recibe secretos**.
- Los datos externos (MEXC, asesor) se tratan como datos, nunca como instrucciones.

## 7. Riesgo y ejecución Paper
Límites fijados antes de la prueba en `config/riesgo.json`; el LLM no puede modificarlos:
- exposición máxima de 90%;
- pérdida diaria máxima de 5% (bloquea órdenes);
- antigüedad máxima del dato de 26 h;
- sin apalancamiento ni ventas en corto.

La ejecución es en la apertura de t+1, con 0.05% de deslizamiento y la precisión y el monto mínimo del par según MEXC. **Detener no liquida**; liquidar es una acción explícita.

## 8. Ciclo programado y persistencia
El programador revisa cada `INTERVALO_REVISION_S` (300 s) si hay una vela diaria nueva y decide **una vez por vela**. Cada revisión queda en la tabla `ciclos`. ⟳ Periodo de operación en vivo: del **24 nov** al **⟳**, con **⟳** revisiones y **⟳** decisiones (ver `RESULTADOS.md` §2).

La frecuencia es diaria, así que el agente **no acelera artificialmente** las observaciones. Los 20 o más ciclos de evaluación se obtienen con **replay etiquetado**, con cartera separada.

## 9. Manejo de errores
Los nueve escenarios del protocolo tienen respuesta definida y prueba automatizada (`tests/test_escenarios_error.py`): MEXC sin respuesta o con límite de solicitudes (reintentos 1, 2 y 4 s y luego pausa), dato atrasado, Ollama detenido, JSON inválido, clave incorrecta, saldo insuficiente, evento duplicado, reinicio del backend y botón de detención. En total hay **43 pruebas** que pasan.

## 10. Evaluación: ¿el LLM aporta valor?
### 10.1 Iteraciones durante el desarrollo
Las primeras pruebas con el LLM real mostraron fallas de razonamiento del modelo pequeño. Se corrigieron sin quitarle autonomía al LLM:

| Versión | Problema observado (replay de 30 velas) | Corrección |
|---|---|---|
| 0.1.0 | Propuso "comprar" con posición abierta en casi todas las velas: **26 de 30 propuestas rechazadas** por el motor de riesgo; nunca vendió | Esquema JSON con `enum` dinámico: solo puede elegir acciones válidas en su estado |
| 0.1.1 | Eligió "mantener" en **30 de 30 velas** y justificó *"no hay posición abierta… por lo que no se puede ejecutar una acción de compra"* (error de lógica con la doble negación) | Contexto en lenguaje claro (`situacion`), guía de decisión explícita y justificación **antes** de la acción |
| 0.1.2 | Comportamiento coherente: opera y respeta la posición | — |

Estas fallas muestran por qué **el motor de riesgo determinista es indispensable**: en la versión 0.1.0 bloqueó 26 propuestas inválidas sin que ocurriera ninguna operación indebida.

### 10.2 Comparación en replay (mismo periodo y costos)
Replay de 30 velas, ejecución del 2026-10-25 al 2026-11-23 (versión 0.2.0, Ollama local real):

| Estrategia | Rend. neto | Máx. drawdown | Operaciones | Win Rate | Rechazadas | Latencia media |
|---|---|---|---|---|---|---|
| Modelo sin LLM (regla δ) | −20.83% | −25.27% | 5 | 0% | 3 | — |
| Agente con LLM | −22.34% | −26.70% | 7 | 0% | 3 | 3,482 ms |
| Comprar y mantener | −27.54% | −33.05% | 1 | 0% | — | — |

⟳ Actualizar con el replay final (`RESULTADOS.md` §3).

**Lectura:**
- En un mes de caída de ETH (cerca de −27%), las dos estrategias activas perdieron menos que comprar y mantener, porque pasaron parte del tiempo en efectivo.
- El agente con LLM **no mejoró a la regla sin LLM**: hizo 2 operaciones más, perdió 1.5 puntos más y agregó 3.5 s de latencia por decisión.
- Las 3 rechazadas de cada estrategia son bloqueos del límite de pérdida diaria (5%), lo que muestra que el control funciona.

### 10.3 Conclusión
**En el periodo evaluado, el LLM no aporta valor sobre la regla del modelo**: casi siempre sigue la señal y, cuando se aparta, no mejora el resultado. Su aporte real está en la **explicación en lenguaje natural** de cada decisión y en la capacidad de consultar contexto con herramientas. El protocolo pide evaluar si aporta valor, no que lo mejore, y la conclusión honesta es que con `llama3.2:3b` y estas variables, no lo hace.

## 11. Limitaciones y trabajo futuro
- El modelo predictivo no supera al pronóstico cero; el cuello de botella son las variables, no el agente.
- Pocas decisiones en vivo (frecuencia diaria) y un solo régimen de mercado (bajista) en el replay.
- Modelo de lenguaje pequeño (3B): probar `llama3.1:8b` o similar si el hardware lo permite.
- Siguientes pasos:
  - evaluar el filtro SMA20 (capítulo 11) como control de riesgo del agente;
  - replay de más de 100 velas para cubrir varios regímenes;
  - métricas de calibración de las justificaciones del LLM.

## Anexos
- Instrucciones de ejecución: [README](../README.md).
- Tablas generadas: [`RESULTADOS.md`](RESULTADOS.md).
- Evidencia: `evidencia/ollama_*.json` y `evidencia/registros/*.csv`.
- Pruebas: `cd backend && pytest -v`.
