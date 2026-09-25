import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import postgres from "https://deno.land/x/postgresjs@v3.4.4/mod.js";

/**
 * Recibe la auditoria de fletes que manda el Apps Script de la planilla de
 * logistica y la guarda en bronze.auditoria_sevillanita.
 *
 * POR QUE UNA FUNCION Y NO LA PLANILLA ESCRIBIENDO DERECHO A LA BASE.
 * Para que el unico secreto que vive adentro del Google Sheet sea un token que
 * SOLO sirve para esto. La clave de la base se queda del lado del servidor y no
 * sale nunca. Cualquiera que pueda abrir el editor de la planilla ve el token;
 * con el, lo peor que puede hacer es mandar filas de auditoria.
 *
 * Es la misma razon --y la misma forma-- que la funcion `sell-in`. Lo unico
 * distinto es a donde escribe y con que token.
 *
 * POR QUE NO SE PUEDE POR LA API REST, que es lo primero que uno intenta:
 * `bronze` no es un esquema expuesto, la tabla no tiene permisos para ningun
 * rol de la API y tiene RLS prendida sin politicas. Las tres cosas juntas hacen
 * que la API conteste "no encuentro la tabla" aunque este ahi. Y esta bien que
 * sea asi: abrir `bronze` obligaria a meter en la planilla una clave con acceso
 * a toda la base.
 *
 * ES UN UPSERT POR FACTURA. La planilla puede reenviar la auditoria entera
 * cuantas veces quiera: cada factura queda una sola vez, con la ultima version
 * de sus veredictos. No borra nada que no venga en el envio, asi que un envio
 * parcial no se lleva puesto lo anterior.
 *
 * verify_jwt esta en false a proposito: quien llama es un Apps Script, no un
 * usuario logueado. La autenticacion es el header x-auditoria-token.
 */

const TOKEN = Deno.env.get("AUDITORIA_TOKEN") ?? "";
const DB_URL = Deno.env.get("SUPABASE_DB_URL") ?? "";

// Un envio normal son decenas de filas. El tope es para que un error de la
// planilla --una hoja con 50.000 filas vacias-- no se convierta en un insert
// gigante contra la base.
const TOPE_FILAS = 5000;

const COLUMNAS = [
  "factura", "fecha", "localidad",
  "veredicto_tarifa", "veredicto_peso", "accion", "monto_en_juego",
] as const;

/** Comparacion en tiempo constante: una comparacion normal filtra el largo. */
function igual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let dif = 0;
  for (let i = 0; i < a.length; i++) dif |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return dif === 0;
}

function json(cuerpo: unknown, status = 200): Response {
  return new Response(JSON.stringify(cuerpo), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** El numero de una celda, o null. Un texto que no es numero NO pasa como 0. */
function numero(valor: unknown): number | null {
  if (valor === null || valor === undefined || valor === "") return null;
  const n = typeof valor === "number" ? valor : Number(valor);
  return Number.isFinite(n) ? n : NaN;
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return json({ error: "Solo POST" }, 405);
  }

  // FALLA CERRADO. Sin secreto configurado no acepta nada, en vez de quedar
  // abierta mientras alguien se acuerda de cargarlo.
  if (!TOKEN) {
    return json({ error: "AUDITORIA_TOKEN no esta configurado en el servidor" }, 503);
  }
  if (!igual(req.headers.get("x-auditoria-token") ?? "", TOKEN)) {
    return json({ error: "Token invalido" }, 401);
  }

  let cuerpo: { filas?: unknown };
  try {
    cuerpo = await req.json();
  } catch {
    return json({ error: "El cuerpo no es JSON" }, 400);
  }

  const crudas = cuerpo.filas;
  if (!Array.isArray(crudas) || crudas.length === 0) {
    return json({ error: "Se esperaba filas: [] con al menos una fila" }, 400);
  }
  if (crudas.length > TOPE_FILAS) {
    return json({ error: `Demasiadas filas (${crudas.length}), el tope es ${TOPE_FILAS}` }, 400);
  }

  // SE VALIDA TODO ANTES DE ESCRIBIR NADA, y si algo esta mal se rechaza el
  // envio entero diciendo que fila es. Saltear las filas rotas en silencio
  // dejaria la planilla mostrando una auditoria que la base no tiene.
  const malas: string[] = [];
  const filas = crudas.map((f, i) => {
    const fila = (f ?? {}) as Record<string, unknown>;
    const factura = String(fila.factura ?? "").trim();
    if (!factura) malas.push(`fila ${i + 1}: sin factura`);
    const monto = numero(fila.monto_en_juego);
    if (Number.isNaN(monto)) {
      malas.push(`fila ${i + 1} (${factura}): monto_en_juego no es un numero`);
    }
    return {
      factura,
      fecha: (fila.fecha as string) || null,
      localidad: (fila.localidad as string) || null,
      veredicto_tarifa: (fila.veredicto_tarifa as string) || null,
      veredicto_peso: (fila.veredicto_peso as string) || null,
      accion: (fila.accion as string) || null,
      monto_en_juego: monto,
    };
  });
  if (malas.length) {
    return json({ error: "Hay filas invalidas", filas: malas.slice(0, 10) }, 400);
  }

  // Una factura repetida DENTRO del mismo envio rompe el upsert ("ON CONFLICT
  // DO UPDATE command cannot affect row a second time"), y es un error de la
  // planilla que conviene ver, no tapar quedandose con la ultima.
  const vistas = new Set<string>();
  const repetidas = filas.map((f) => f.factura).filter((f) => {
    if (vistas.has(f)) return true;
    vistas.add(f);
    return false;
  });
  if (repetidas.length) {
    return json({
      error: "Hay facturas repetidas en el mismo envio",
      facturas: [...new Set(repetidas)].slice(0, 10),
    }, 400);
  }

  const sql = postgres(DB_URL, { prepare: false });
  try {
    const guardadas = await sql`
      insert into bronze.auditoria_sevillanita ${sql(filas, ...COLUMNAS)}
      on conflict (factura) do update set
        fecha            = excluded.fecha,
        localidad        = excluded.localidad,
        veredicto_tarifa = excluded.veredicto_tarifa,
        veredicto_peso   = excluded.veredicto_peso,
        accion           = excluded.accion,
        monto_en_juego   = excluded.monto_en_juego,
        actualizado_en   = now()
      returning factura
    `;
    return json({ ok: true, filas: guardadas.length });
  } catch (e) {
    console.error("[auditoria-sevillanita]", e);
    return json({ error: "No se pudo guardar", detalle: String(e).slice(0, 300) }, 500);
  } finally {
    await sql.end();
  }
});
