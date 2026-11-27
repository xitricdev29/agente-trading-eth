// Cliente de la API del backend. La API key NO está en el código ni en variables VITE_*:
// el usuario la escribe en la UI y se guarda solo en sessionStorage (se borra al cerrar la pestaña).

export type Estado = "detenido" | "en_marcha" | "pausado" | "bloqueado";

export interface Decision {
  id: number;
  modo: string;
  corrida: string;
  politica: string;
  fecha_vela: string;
  precio_cierre: number;
  pronostico: number;
  senal_regla: string;
  indicadores: Record<string, number>;
  propuesta: { accion: string; justificacion: string } | null;
  accion_propuesta: string | null;
  accion_final: string;
  aprobada: number;
  motivo_riesgo: string;
  latencia_llm_ms: number | null;
  herramientas: UsoHerramienta[];
  asesor_externo: { opinion?: string; latencia_ms?: number; error?: string } | null;
  modelo_version: string;
  llm_modelo: string | null;
}

export interface Panel {
  version: string;
  simbolo: string;
  estado: Estado;
  politica: string;
  replay_en_curso: boolean;
  modelo: { version: string; tipo: string; delta: number; procedencia: Record<string, unknown> };
  llm_modelo: string;
  ultima_decision: Decision | null;
  cartera: { efectivo: number; unidades: number; en_posicion: boolean; precio_valoracion: number | null; patrimonio: number; capital_inicial: number };
  riesgo: Record<string, number | boolean>;
  ultimo_ciclo: { fecha: string; resultado: string } | null;
  ultimo_error: ErrorReg | null;
  llm_herramientas: boolean;
  asesor_externo: boolean;
  evidencia_llm: EvidenciaLLM | null;
  ciclos: { intervalo_s: number; total: number; programados: number | null; primero: string | null; ultimo: string | null; velas: number };
}

export interface UsoHerramienta {
  nombre?: string;
  argumentos?: Record<string, unknown>;
  permitida?: boolean;
  resultado_resumen?: string;
  aviso?: string;
}

export interface EvidenciaLLM {
  fecha_utc: string;
  ollama_version: string;
  modelo: { nombre: string; parametros: string; cuantizacion: string; tamano_gb: number; digest: string };
  inferencia: { latencia_total_ms: number; tokens_por_segundo: number | null; tiempo_carga_s: number };
  procesador: string;
  inferencia_local: boolean;
  hardware: { sistema: string; cpu: string; ram_gb: number | null; gpus: string[] };
}

export interface ErrorReg {
  id: number;
  creado_en: string;
  etapa: string;
  tipo: string;
  mensaje: string;
  accion: string;
}

export interface PuntoPatrimonio {
  fecha: string;
  politica: string;
  patrimonio: number;
}

export interface MetricasPolitica {
  decisiones: number | null;
  patrimonio_final: number;
  rend_neto_pct: number;
  max_drawdown_pct: number;
  operaciones: number;
  win_rate: number | null;
  costos: number;
  acciones_rechazadas: number | null;
  latencia_media_ms: number | null;
  abortado: string | null;
}

export interface ResumenReplay {
  corrida: string;
  inicio_ejecucion?: string;
  fin?: string;
  ciclos?: number;
  politicas?: Record<string, MetricasPolitica>;
  estado?: string;
}

async function pedir<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
  const r = await fetch(ruta, opciones);
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(cuerpo.detail ?? `HTTP ${r.status}`);
  return cuerpo as T;
}

export const api = {
  panel: () => pedir<Panel>("/api/panel"),
  decisiones: (modo = "vivo", corrida = "vivo") => pedir<Decision[]>(`/api/decisiones?modo=${modo}&corrida=${encodeURIComponent(corrida)}&limite=60`),
  patrimonio: (modo = "vivo", corrida = "vivo") => pedir<PuntoPatrimonio[]>(`/api/patrimonio?modo=${modo}&corrida=${encodeURIComponent(corrida)}`),
  errores: () => pedir<ErrorReg[]>("/api/errores?limite=30"),
  replays: () => pedir<ResumenReplay[]>("/api/replays"),
  control: (accion: string, clave: string, cuerpo?: unknown) =>
    pedir<Record<string, unknown>>(`/api/control/${accion}`, {
      method: "POST",
      headers: { "X-API-Key": clave, "Content-Type": "application/json" },
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    }),
};
