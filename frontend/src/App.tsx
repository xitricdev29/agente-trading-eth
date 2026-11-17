import { useCallback, useEffect, useState } from "react";
import { api, type Decision, type ErrorReg, type Panel, type PuntoPatrimonio, type ResumenReplay } from "./api";

const usd = (x: number | null | undefined) =>
  x == null ? "—" : x.toLocaleString("es-MX", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const pct = (x: number | null | undefined, d = 2) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
const num = (x: number | null | undefined, d = 4) => (x == null ? "—" : x.toFixed(d));

const ETIQUETA_ESTADO: Record<string, string> = {
  en_marcha: "En marcha",
  pausado: "Pausado",
  detenido: "Detenido",
  bloqueado: "Bloqueado por riesgo",
};
const NOMBRE_POLITICA: Record<string, string> = {
  llm: "Agente con LLM",
  regla: "Modelo sin LLM (regla δ)",
  comprar_y_mantener: "Comprar y mantener",
};

function leerClave() {
  try {
    return sessionStorage.getItem("api_key") ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [decisiones, setDecisiones] = useState<Decision[]>([]);
  const [serie, setSerie] = useState<PuntoPatrimonio[]>([]);
  const [errores, setErrores] = useState<ErrorReg[]>([]);
  const [replays, setReplays] = useState<ResumenReplay[]>([]);
  const [serieReplay, setSerieReplay] = useState<PuntoPatrimonio[]>([]);
  const [clave, setClave] = useState(leerClave);
  const [aviso, setAviso] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [sinConexion, setSinConexion] = useState(false);
  const [ciclosReplay, setCiclosReplay] = useState(30);
  const [ocupado, setOcupado] = useState(false);

  const cargar = useCallback(async () => {
    try {
      const [p, d, s, e, r] = await Promise.all([api.panel(), api.decisiones(), api.patrimonio(), api.errores(), api.replays()]);
      setPanel(p); setDecisiones(d); setSerie(s); setErrores(e); setReplays(r);
      const completo = r.find((x) => x.politicas);
      setSerieReplay(completo ? await api.patrimonio("replay", completo.corrida) : []);
      setSinConexion(false);
    } catch {
      setSinConexion(true);
    }
  }, []);

  useEffect(() => {
    cargar();
    const id = setInterval(cargar, 10_000);
    return () => clearInterval(id);
  }, [cargar]);

  const guardarClave = (v: string) => {
    setClave(v);
    try { sessionStorage.setItem("api_key", v); } catch { /* almacenamiento no disponible: la clave queda solo en memoria */ }
  };

  const controlar = async (accion: string, cuerpo?: unknown) => {
    if (!clave) { setAviso({ tipo: "error", texto: "Escribe la API key del backend para usar los controles." }); return; }
    setOcupado(true);
    try {
      const r = await api.control(accion, clave, cuerpo);
      setAviso({ tipo: "ok", texto: describir(accion, r) });
      await cargar();
    } catch (e) {
      setAviso({ tipo: "error", texto: `${accion}: ${(e as Error).message}` });
    } finally {
      setOcupado(false);
    }
  };

  const d = panel?.ultima_decision;
  const c = panel?.cartera;
  const rendCartera = c ? c.patrimonio / c.capital_inicial - 1 : null;
  const ultimoReplay = replays.find((x) => x.politicas);

  return (
    <div className="pagina">
      <header className="encabezado">
        <div>
          <h1>Agente ETH/USDT</h1>
          <p className="sub">
            v{panel?.version ?? "0.1.0"} · modo <b>Paper</b> (sin órdenes reales) · datos públicos de MEXC · LLM local {panel?.llm_modelo ?? ""}
          </p>
        </div>
        <span className={`estado estado-${panel?.estado ?? "desconocido"}`}>
          {sinConexion ? "Backend sin conexión" : ETIQUETA_ESTADO[panel?.estado ?? ""] ?? "…"}
        </span>
      </header>

      <section className="controles">
        <label className="clave">
          API key del backend
          <input type="password" value={clave} autoComplete="off" placeholder="se guarda solo en esta pestaña"
                 onChange={(e) => guardarClave(e.target.value)} />
        </label>
        <div className="botones">
          <button className="primario" disabled={ocupado} onClick={() => controlar("iniciar")}>Iniciar</button>
          <button disabled={ocupado} onClick={() => controlar("pausar")}>Pausar</button>
          <button className="peligro" disabled={ocupado} onClick={() => controlar("detener")}>Detener</button>
          <button disabled={ocupado} onClick={() => controlar("ciclo")}>Ejecutar ciclo ahora</button>
          <button disabled={ocupado || !c?.en_posicion} onClick={() => controlar("liquidar")} title="Vende toda la posición Paper al precio actual">
            Liquidar posición
          </button>
        </div>
        {aviso && <p className={`aviso aviso-${aviso.tipo}`}>{aviso.texto}</p>}
        {panel?.estado === "bloqueado" && <p className="aviso aviso-error">Se alcanzó un límite de riesgo: no se envían nuevas órdenes. Revisa la posición y pulsa Iniciar para reanudar.</p>}
      </section>

      <section className="tarjetas">
        <Tarjeta titulo="Activo y vela">
          <Dato grande>{panel?.simbolo ?? "ETHUSDT"}</Dato>
          <Fila k="Última vela cerrada" v={d?.fecha_vela ?? "—"} />
          <Fila k="Cierre" v={usd(d?.precio_cierre)} />
          <Fila k="Último ciclo" v={panel?.ultimo_ciclo ? `${fechaCorta(panel.ultimo_ciclo.fecha)} · ${panel.ultimo_ciclo.resultado}` : "—"} />
        </Tarjeta>
        <Tarjeta titulo="Pronóstico del modelo">
          <Dato grande clase={d && d.pronostico > 0 ? "pos" : "neg"}>{d ? `${d.pronostico > 0 ? "+" : ""}${pct(d.pronostico, 3)}` : "—"}</Dato>
          <Fila k="Rendimiento estimado" v="día siguiente" />
          <Fila k="Umbral δ" v={pct(panel?.modelo.delta, 2)} />
          <Fila k="Señal de la regla" v={d?.senal_regla ?? "—"} />
          <Fila k="Modelo" v={panel ? `${panel.modelo.tipo} v${panel.modelo.version}` : "—"} />
        </Tarjeta>
        <Tarjeta titulo="Indicadores (hasta t)">
          <Fila k="Rendimiento del día" v={pct(d?.indicadores?.rend_ultimo_dia)} />
          <Fila k="Distancia a SMA20" v={pct(d?.indicadores?.dist_sma20)} />
          <Fila k="Volatilidad 20 d" v={pct(d?.indicadores?.vol20)} />
          <Fila k="Rendimiento 20 d" v={pct(d?.indicadores?.rend_acum_20d)} />
        </Tarjeta>
        <Tarjeta titulo="Acción propuesta">
          <Dato grande>{d?.accion_propuesta ?? "—"}</Dato>
          <p className="motivo">{d?.propuesta?.justificacion ?? "Sin decisiones todavía."}</p>
          <Fila k="Política" v={NOMBRE_POLITICA[panel?.politica ?? ""] ?? "—"} />
          <Fila k="Latencia LLM" v={d?.latencia_llm_ms != null ? `${d.latencia_llm_ms.toFixed(0)} ms` : "—"} />
        </Tarjeta>
        <Tarjeta titulo="Riesgo">
          <Dato grande clase={d ? (d.aprobada ? "pos" : "neg") : ""}>{d ? (d.aprobada ? "Aprobada" : "Rechazada") : "—"}</Dato>
          <p className="motivo">{d?.motivo_riesgo ?? ""}</p>
          <Fila k="Acción ejecutada" v={d?.accion_final ?? "—"} />
          <Fila k="Exposición máx." v={pct(panel?.riesgo.exposicion_maxima as number, 0)} />
          <Fila k="Pérdida diaria máx." v={pct(panel?.riesgo.perdida_diaria_maxima as number, 0)} />
          <Fila k="Antigüedad máx. dato" v={`${panel?.riesgo.antiguedad_maxima_horas ?? "—"} h`} />
        </Tarjeta>
        <Tarjeta titulo="Posición y patrimonio">
          <Dato grande>{usd(c?.patrimonio)}</Dato>
          <Fila k="Resultado vs capital" v={<span className={rendCartera != null && rendCartera >= 0 ? "pos" : "neg"}>{pct(rendCartera)}</span>} />
          <Fila k="Efectivo" v={usd(c?.efectivo)} />
          <Fila k="Posición" v={c ? `${num(c.unidades, 4)} ETH` : "—"} />
          <Fila k="Capital inicial" v={usd(c?.capital_inicial)} />
        </Tarjeta>
      </section>

      <section className="bloque">
        <h2>Patrimonio en vivo</h2>
        <Grafica serie={serie} capital={c?.capital_inicial ?? 50000} />
      </section>

      <section className="bloque">
        <h2>Decisiones en vivo</h2>
        <TablaDecisiones filas={decisiones} />
      </section>

      <section className="bloque">
        <div className="titulo-con-accion">
          <h2>Replay histórico y comparación</h2>
          <div className="replay-form">
            <label>Velas <input type="number" min={20} max={200} value={ciclosReplay}
                                onChange={(e) => setCiclosReplay(Number(e.target.value))} /></label>
            <button disabled={ocupado || panel?.replay_en_curso} onClick={() => controlar("replay", { ciclos: ciclosReplay, incluir_llm: true })}>
              {panel?.replay_en_curso ? "Replay en curso…" : "Ejecutar replay"}
            </button>
          </div>
        </div>
        <p className="nota">El replay repite velas diarias ya cerradas con una cartera separada; está etiquetado como replay y no se mezcla con la operación en vivo.</p>
        {ultimoReplay ? <TablaReplay r={ultimoReplay} /> : <p className="vacio">Aún no hay replays.</p>}
        {serieReplay.length > 0 && <Grafica serie={serieReplay} capital={c?.capital_inicial ?? 50000} />}
      </section>

      <section className="bloque">
        <h2>Bitácora de eventos y errores</h2>
        <table>
          <thead><tr><th>Fecha (UTC)</th><th>Etapa</th><th>Tipo</th><th>Mensaje</th><th>Acción</th></tr></thead>
          <tbody>
            {errores.map((e) => (
              <tr key={e.id} className={e.tipo === "cambio_estado" ? "" : "fila-error"}>
                <td>{fechaCorta(e.creado_en)}</td><td>{e.etapa}</td><td>{e.tipo}</td><td>{e.mensaje}</td><td>{e.accion}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function describir(accion: string, r: Record<string, unknown>) {
  if (accion === "ciclo") return `Ciclo: ${r.resultado}${r.mensaje ? ` — ${r.mensaje}` : ""}`;
  if (accion === "replay") return String(r.mensaje);
  if (accion === "liquidar") return r.resultado === "liquidado" ? "Posición liquidada en Paper." : "No había posición.";
  if (accion === "detener") return `Agente detenido. ${r.posicion_abierta ? "La posición sigue abierta (detener no liquida)." : ""}`;
  return `Estado: ${ETIQUETA_ESTADO[String(r.estado)] ?? r.estado}`;
}

function fechaCorta(iso: string) {
  return iso.replace("T", " ").slice(0, 16);
}

function Tarjeta({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return <article className="tarjeta"><h3>{titulo}</h3>{children}</article>;
}
function Dato({ children, grande, clase = "" }: { children: React.ReactNode; grande?: boolean; clase?: string }) {
  return <div className={`dato ${grande ? "grande" : ""} ${clase}`}>{children}</div>;
}
function Fila({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="fila"><span>{k}</span><span>{v}</span></div>;
}

function TablaDecisiones({ filas }: { filas: Decision[] }) {
  if (!filas.length) return <p className="vacio">Sin decisiones en vivo. Pulsa Iniciar: el agente decide una vez por vela diaria cerrada.</p>;
  return (
    <div className="desplazable">
      <table>
        <thead><tr><th>Vela</th><th>Cierre</th><th>Pronóstico</th><th>Señal</th><th>Propuesta</th><th>Final</th><th>Riesgo</th><th>Justificación</th></tr></thead>
        <tbody>
          {filas.map((f) => (
            <tr key={f.id}>
              <td>{f.fecha_vela}</td><td>{usd(f.precio_cierre)}</td><td>{pct(f.pronostico, 3)}</td><td>{f.senal_regla}</td>
              <td>{f.accion_propuesta ?? "—"}</td><td>{f.accion_final}</td>
              <td className={f.aprobada ? "pos" : "neg"}>{f.aprobada ? "aprobada" : "rechazada"}</td>
              <td className="just">{f.propuesta?.justificacion ?? f.motivo_riesgo}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TablaReplay({ r }: { r: ResumenReplay }) {
  const pols = Object.entries(r.politicas ?? {});
  return (
    <div className="desplazable">
      <p className="nota">Corrida <code>{r.corrida}</code> · ejecución del {r.inicio_ejecucion} al {r.fin} · {r.ciclos} ciclos · mismos costos y periodo para las tres estrategias.</p>
      <table>
        <thead><tr><th>Estrategia</th><th>Rend. neto</th><th>Máx. drawdown</th><th>Operaciones</th><th>Win Rate</th><th>Rechazadas</th><th>Latencia media</th><th>Patrimonio final</th></tr></thead>
        <tbody>
          {pols.map(([k, m]) => (
            <tr key={k}>
              <td>{NOMBRE_POLITICA[k] ?? k}{m.abortado ? " (interrumpido)" : ""}</td>
              <td className={m.rend_neto_pct >= 0 ? "pos" : "neg"}>{m.rend_neto_pct.toFixed(2)}%</td>
              <td>{m.max_drawdown_pct.toFixed(2)}%</td><td>{m.operaciones}</td>
              <td>{m.win_rate == null ? "no definido" : pct(m.win_rate, 0)}</td>
              <td>{m.acciones_rechazadas ?? "—"}</td>
              <td>{m.latencia_media_ms != null ? `${m.latencia_media_ms.toFixed(0)} ms` : "—"}</td>
              <td>{usd(m.patrimonio_final)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const COLORES: Record<string, string> = { llm: "var(--serie-1)", regla: "var(--serie-2)", manual: "var(--serie-3)" };

function Grafica({ serie, capital }: { serie: PuntoPatrimonio[]; capital: number }) {
  if (serie.length < 2) return <p className="vacio">Se necesitan al menos dos puntos para graficar.</p>;
  const pols = [...new Set(serie.map((p) => p.politica))];
  const fechas = [...new Set(serie.map((p) => p.fecha))].sort();
  const vals = serie.map((p) => p.patrimonio).concat(capital);
  const min = Math.min(...vals), max = Math.max(...vals);
  const W = 800, H = 220, M = { l: 100, r: 12, t: 12, b: 24 };
  const x = (f: string) => M.l + (fechas.indexOf(f) / Math.max(1, fechas.length - 1)) * (W - M.l - M.r);
  const y = (v: number) => M.t + (1 - (v - min) / Math.max(1e-9, max - min)) * (H - M.t - M.b);
  return (
    <figure className="grafica">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Patrimonio por fecha">
        <line x1={M.l} x2={W - M.r} y1={y(capital)} y2={y(capital)} className="referencia" />
        <text x={M.l - 6} y={y(max) + 4} className="eje" textAnchor="end">{usd(max)}</text>
        <text x={M.l - 6} y={y(min)} className="eje" textAnchor="end">{usd(min)}</text>
        <text x={M.l} y={H - 6} className="eje">{fechas[0]}</text>
        <text x={W - M.r} y={H - 6} className="eje" textAnchor="end">{fechas[fechas.length - 1]}</text>
        {pols.map((p) => {
          const pts = serie.filter((s) => s.politica === p).map((s) => `${x(s.fecha)},${y(s.patrimonio)}`).join(" ");
          return <polyline key={p} points={pts} fill="none" stroke={COLORES[p] ?? "var(--serie-3)"} strokeWidth={2} />;
        })}
      </svg>
      <figcaption>
        {pols.map((p) => <span key={p} className="leyenda"><i style={{ background: COLORES[p] ?? "var(--serie-3)" }} />{NOMBRE_POLITICA[p] ?? p}</span>)}
        <span className="leyenda"><i className="ref" />Capital inicial</span>
      </figcaption>
    </figure>
  );
}
