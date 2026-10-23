import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import postgres from "https://deno.land/x/postgresjs@v3.4.4/mod.js";

/**
 * Recibe la hoja del Consolidador de Ofertas que manda el Apps Script.
 *
 * Es el gemelo de `sell-in` y hace exactamente lo mismo con otra planilla:
 * guarda las filas TAL CUAL llegan en bronze.ofertas_crudo y nada mas. No
 * interpreta encabezados ni convierte porcentajes -- eso vive del lado del
 * orquestador, que es donde estan las pruebas. Dos parsers se separan sin que
 * nadie lo note.
 *
 * ---------------------------------------------------------------------------
 * POR QUE UNA FUNCION APARTE Y NO UN PARAMETRO EN `sell-in`
 *
 * Por dos motivos, y los dos son de riesgo y no de prolijidad:
 *
 *   - `sell-in` ANDA y es el unico camino por el que entra el sell in. Tocarla
 *     para agregarle un destino es arriesgar lo que funciona por ahorrar un
 *     archivo que es mitad comentarios.
 *   - El token queda separado. El de esta planilla sirve SOLO para mandar
 *     ofertas; si alguien lo saca del editor de la planilla, no puede escribir
 *     el sell in con el.
 *
 * verify_jwt va en false a proposito: quien llama es un Apps Script, no un
 * usuario logueado. La autenticacion es el header x-ofertas-token, comparado
 * en tiempo constante contra el secreto OFERTAS_TOKEN.
 */

const TOKEN = Deno.env.get("OFERTAS_TOKEN") ?? "";
const DB_URL = Deno.env.get("SUPABASE_DB_URL") ?? "";

/** Cuantas fotos se conservan. Ver el borrado de abajo. */
const FOTOS_QUE_QUEDAN = 10;

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

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return json({ error: "Solo POST" }, 405);
  }

  // FALLA CERRADO. Sin secreto configurado no acepta nada, en vez de quedar
  // abierta mientras alguien se acuerda de cargarlo.
  if (!TOKEN) {
    return json({ error: "OFERTAS_TOKEN no esta configurado en el servidor" }, 503);
  }
  if (!igual(req.headers.get("x-ofertas-token") ?? "", TOKEN)) {
    return json({ error: "Token invalido" }, 401);
  }

  let cuerpo: { hoja?: string; valores?: unknown; origen?: string };
  try {
    cuerpo = await req.json();
  } catch {
    return json({ error: "El cuerpo no es JSON" }, 400);
  }

  const valores = cuerpo.valores;
  if (!Array.isArray(valores) || valores.length < 2) {
    // Menos de dos filas es un encabezado sin datos. Guardarlo dejaria la
    // proxima foto con cero ofertas, y una foto con cero ofertas aplicada
    // pone en cero el descuento de todo el catalogo.
    return json({ error: "Se esperaban al menos un encabezado y una fila" }, 400);
  }
  if (!Array.isArray(valores[0])) {
    return json({ error: "Cada fila tiene que ser un array de celdas" }, 400);
  }

  const sql = postgres(DB_URL, { prepare: false });
  try {
    const [fila] = await sql`
      insert into bronze.ofertas_crudo (hoja, filas, filas_n, origen)
      values (${cuerpo.hoja ?? null}, ${sql.json(valores as never)},
              ${valores.length}, ${cuerpo.origen ?? "apps-script"})
      returning id, recibido
    `;
    // Se conservan las ultimas y no todas: sirven para auditar un dato raro,
    // pero la hoja entera repetida una vez por hora es basura que crece.
    await sql`
      delete from bronze.ofertas_crudo
      where id not in (
        select id from bronze.ofertas_crudo order by id desc limit ${FOTOS_QUE_QUEDAN}
      )
    `;
    return json({ ok: true, id: fila.id, recibido: fila.recibido, filas: valores.length });
  } catch (e) {
    console.error("[ofertas]", e);
    return json({ error: "No se pudo guardar", detalle: String(e).slice(0, 300) }, 500);
  } finally {
    await sql.end();
  }
});
