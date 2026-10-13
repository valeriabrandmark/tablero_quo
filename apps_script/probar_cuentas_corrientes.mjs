/**
 * Pruebas del .gs de cuentas corrientes. Sin planilla, sin red.
 *
 * POR QUE EXISTE. Lo que este archivo convierte --numeros con formato
 * argentino, fechas dd/MM/yyyy, encabezados con y sin acento-- se corrompe en
 * SILENCIO: un "$ 1.234,50" leido como 1.23450 no rompe nada, entra en la
 * base y aparece en el tablero como un saldo de un peso. La unica forma de
 * verlo es probarlo.
 *
 *     node apps_script/probar_cuentas_corrientes.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FUENTE = readFileSync(join(AQUI, "cuentas_corrientes.gs"), "utf8");

// El .gs se evalua entero con los globales de Google reemplazados por lo
// minimo que necesitan las funciones puras. Asi se prueba EL ARCHIVO QUE SE
// PEGA EN LA PLANILLA y no una copia que puede quedar desincronizada.
const Utilities = {
  formatDate(fecha, _zona, formato) {
    const dd = String(fecha.getDate()).padStart(2, "0");
    const mm = String(fecha.getMonth() + 1).padStart(2, "0");
    if (formato !== "yyyy-MM-dd") throw new Error("formato no previsto: " + formato);
    return `${fecha.getFullYear()}-${mm}-${dd}`;
  },
};
const PropertiesService = { getScriptProperties: () => ({ getProperty: () => null }) };
const SpreadsheetApp = {};
const UrlFetchApp = {};
const Logger = { log() {} };

const ambito = { Utilities, PropertiesService, SpreadsheetApp, UrlFetchApp, Logger };
const nombres = Object.keys(ambito);
const exportar = [
  "ccNormalizar_", "ccTexto_", "ccNumero_", "ccFecha_", "ccConvertir_",
  "ccMapearColumnas_", "ccFilaDeEncabezados_", "ccCodigo_", "CC_DESTINO",
];
const api = new Function(
  ...nombres,
  FUENTE + "\nreturn {" + exportar.join(", ") + "};",
)(...nombres.map((n) => ambito[n]));

const FALLOS = [];
function revisar(nombre, obtenido, esperado) {
  const ok = JSON.stringify(obtenido) === JSON.stringify(esperado);
  console.log(ok ? `OK  ${nombre}` : `MAL ${nombre}\n     esperado: ${JSON.stringify(esperado)}\n     obtenido: ${JSON.stringify(obtenido)}`);
  if (!ok) FALLOS.push(nombre);
}

// --- Encabezados: se comparan sin acentos ni mayusculas --------------------
//
// Es lo que permite que la planilla nueva diga "Razón Social" donde la vieja
// decia "RAZON SOCIAL" sin tocar una linea.

revisar("saca acentos", api.ccNormalizar_("Razón Social"), "razon social");
revisar("saca mayusculas", api.ccNormalizar_("CUIT"), "cuit");
revisar("junta espacios de mas", api.ccNormalizar_("  Saldo   Total "), "saldo total");
revisar("null no rompe", api.ccNormalizar_(null), "");

// --- Numeros: el formato argentino es el caso peligroso --------------------

revisar("formato argentino con $", api.ccNumero_("$ 1.234,50"), 1234.5);
revisar("formato argentino sin simbolo", api.ccNumero_("1.234.567,89"), 1234567.89);
revisar("formato ingles", api.ccNumero_("1,234.50"), 1234.5);
revisar("un numero de verdad pasa igual", api.ccNumero_(1234.5), 1234.5);
revisar("entero sin separadores", api.ccNumero_("4500"), 4500);
revisar("negativo", api.ccNumero_("-1.234,50"), -1234.5);
revisar("solo coma decimal", api.ccNumero_("0,75"), 0.75);
revisar("porcentaje con simbolo", api.ccNumero_("37,5%"), 37.5);

// UN CERO INVENTADO SE SUMA EN EL TABLERO Y NO SE NOTA. Por eso lo que no se
// entiende va a null y no a cero.
revisar("vacio es null, NO cero", api.ccNumero_(""), null);
revisar("null es null", api.ccNumero_(null), null);
revisar("texto suelto es null", api.ccNumero_("s/d"), null);
revisar("un guion es null", api.ccNumero_("-"), null);

// --- Fechas ----------------------------------------------------------------

revisar("dd/MM/yyyy", api.ccFecha_("13/10/2026"), "2026-10-13");
revisar("d/M/yyyy sin ceros", api.ccFecha_("3/7/2026"), "2026-07-03");
revisar("con guiones", api.ccFecha_("13-10-2026"), "2026-10-13");
revisar("anio de dos digitos", api.ccFecha_("13/10/26"), "2026-10-13");
revisar("ya viene ISO", api.ccFecha_("2026-10-13"), "2026-10-13");
revisar("ISO con hora se corta", api.ccFecha_("2026-10-13T09:00:00Z"), "2026-10-13");
revisar("un Date de la planilla", api.ccFecha_(new Date(2026, 9, 13)), "2026-10-13");
revisar("vacio es null", api.ccFecha_(""), null);
revisar("un Date invalido es null", api.ccFecha_(new Date("x")), null);

// --- El CUIT es TEXTO, y esto es lo que lo cuida ---------------------------
//
// Un CUIT tratado como numero pierde el cero de adelante y deja de cruzar
// contra nada. Es el mismo problema que ya habia aparecido con el CAE.

revisar("el cero de adelante se conserva",
  api.ccConvertir_("07434567890", "texto"), "07434567890");
revisar("y no se convierte a numero",
  typeof api.ccConvertir_("07434567890", "texto"), "string");

// --- El mapeo de columnas --------------------------------------------------

const COLUMNAS = {
  cuit: { alias: ["CUIT"], tipo: "texto", requerida: true },
  razon_social: { alias: ["Razon Social", "Cliente"], tipo: "texto" },
  saldo_total: { alias: ["Saldo Total"], tipo: "numero" },
  observaciones: { alias: ["Observaciones"], tipo: "texto" },
};

const m1 = api.ccMapearColumnas_(["CUIT", "Cliente", "Saldo Total"], COLUMNAS);
revisar("mapea por el alias que exista", m1.indices, { cuit: 0, razon_social: 1, saldo_total: 2 });
revisar("y avisa de la que falta", m1.sinMapear, ["observaciones"]);

// EL CASO QUE JUSTIFICA MAPEAR POR NOMBRE: una columna nueva en el medio.
// Por posicion, de aca en adelante todo entra corrido y el tablero muestra el
// saldo en la columna del cliente sin que nada falle.
const m2 = api.ccMapearColumnas_(
  ["CUIT", "Empresa", "Razón Social", "Saldo Total"], COLUMNAS,
);
revisar("una columna nueva en el medio no corre nada",
  m2.indices, { cuit: 0, razon_social: 2, saldo_total: 3 });

const m3 = api.ccMapearColumnas_(["cuit", "razon_social", "saldo_total"], COLUMNAS);
revisar("el nombre de Supabase tambien sirve de alias",
  m3.indices, { cuit: 0, razon_social: 1, saldo_total: 2 });

const m4 = api.ccMapearColumnas_(["Cliente", "Saldo Total"], COLUMNAS);
revisar("sin la obligatoria, queda sin mapear", m4.sinMapear.includes("cuit"), true);

// --- La configuracion tiene que cerrar contra Supabase ---------------------
//
// Si una clave no coincide EXACTAMENTE con la primaria de Postgres, el
// `on_conflict` del upsert falla y no sube nada.

const ESPERADAS = {
  cuentas_corrientes_scoring: ["cuit"],
  cuentas_corrientes_aging: ["empresa", "comprobante"],
  cuentas_corrientes_cancelaciones: ["cuit", "periodo_con_mora"],
};
api.CC_DESTINO.forEach((d) => {
  revisar(`${d.tabla}: la clave es la primaria real`,
    [...d.clave].sort(), [...ESPERADAS[d.tabla]].sort());
  d.clave.forEach((c) => {
    revisar(`${d.tabla}: '${c}' esta declarada y es requerida`,
      Boolean(d.columnas[c] && d.columnas[c].requerida), true);
  });
});


// ===========================================================================
//  LA PLANILLA REAL — con datos inventados
// ===========================================================================
//
// La ESTRUCTURA es la de "Analisis de cuenta corriente" tal como la escribe
// Sigma_AgingCtaCte.gs, verificada contra el archivo del 06/10/2026. Los
// nombres, CUIT e importes son inventados: el repositorio es publico y la
// cartera de clientes no va en un repositorio.
//
// Lo que se conserva, que es lo que importa:
//   - la fila 1 combinada ("Ultima actualizacion: ...") en Scoring y Aging,
//     con los encabezados en la 2; y los encabezados en la 1 en Cancelaciones
//   - la empresa como NUMERO en el Aging (1) y como texto en el Scoring
//     ("0001")
//   - las filas SUBTOTAL de corte por vendedor
//   - los recibos: comprobante REC-xxxx, importes negativos, sin CUIT ni
//     atraso

const HOJAS = {
  Scoring_Clientes: [
    ["Scoring de riesgo | Actualizado: 06/10/2026 a las 08:38 | Empresas: 0001 y 0003"],
    ["Categoría", "Vendedor", "Razón Social", "CUIT", "Empresa",
     "Saldo Total ($)", "Saldo Vencido ($)", "Saldo por Vencer ($)", "% Exposición",
     "Fact. Pendientes", "Fact. Vencidas", "Fact. > 30 días",
     "Atraso Máx (días)", "Atraso Prom General (días)",
     "Meses en mora (últ. 6)", "Peor categoría (últ. 6)", "Tendencia"],
    ["CRÍTICO", "007", "CLIENTE DE PRUEBA UNO", "20-11111111-1", "0001",
     7120012.82, 7120012.82, 0, 1, 2, 2, 2, 87, 87, 3, "CRÍTICO", "→ Estable"],
    ["AL DÍA", "006", "CLIENTE DE PRUEBA DOS", "27-22222222-2", "0003",
     575720.84, 0, 575720.84, 0, 1, 0, 0, 0, 0, "-", "Sin historial", "Sin historial"],
  ],

  Aging_CC_0001_0003: [
    ["Última actualización: 06/10/2026 a las 08:38 | Empresas: 0001 y 0003"],
    ["Vendedor", "Razón Social", "CUIT", "Dirección", "Comprobante", "Fecha",
     "Vencimiento", "Atraso (días)", "Total", "Pendiente", "Empresa", "Observaciones"],
    // Un recibo: sin vendedor, sin CUIT, sin atraso, importes negativos.
    ["", "CLIENTE DE PRUEBA UNO", "", "", "REC-00001585", "2026-08-17",
     "", "", -104003.32, -104003.32, 1, ""],
    // Una factura normal. OJO: la empresa viene como NUMERO.
    ["006", "CLIENTE DE PRUEBA DOS", "27-22222222-2", "CALLE FALSA 123",
     "FA9-00000944", "2026-09-01", "2026-10-01", 12, 321526.8, 321526.8, 1, ""],
    ["007", "CLIENTE DE PRUEBA TRES", "20-33333333-3", "CALLE FALSA 456",
     "B93-00001487", "2026-08-15", "2026-09-14", 29, 375710.89, 375710.89, 3, ""],
    // La fila de corte por vendedor.
    ["↳ 006", "SUBTOTAL", "", "", "", "", "", "", 697237.69, 697237.69, "", ""],
  ],

  Historial_Cancelaciones: [
    ["Fecha Detección", "Período con Mora", "ClienteId", "Razón Social", "CUIT",
     "Vendedor", "Empresa", "Saldo Vencido Cancelado ($)", "Categoría que Tenía",
     "Días Atraso Máx al Cancelar", "Días Atraso Prom al Cancelar", "Tipo de Cancelación"],
    ["2026-09-14", "2026-09-01", "005105", "CLIENTE DE PRUEBA DOS", "27-22222222-2",
     "006", "0001", 1232356.74, "AL DÍA", 4, 4, "Cancelación total"],
  ],
};

function leerHoja(nombre) {
  const destino = api.CC_DESTINO.find((d) => d.hoja.includes(nombre));
  const datos = HOJAS[nombre];
  const filaEnc = api.ccFilaDeEncabezados_(datos, destino.columnas);
  const mapeo = api.ccMapearColumnas_(datos[filaEnc], destino.columnas);
  const filas = [];
  let excluidas = 0;
  for (let i = filaEnc + 1; i < datos.length; i++) {
    const fila = {};
    Object.keys(mapeo.indices).forEach((c) => {
      fila[c] = api.ccConvertir_(datos[i][mapeo.indices[c]], destino.columnas[c].tipo);
    });
    if (destino.excluir) {
      const v = api.ccNormalizar_(fila[destino.excluir.columna]);
      if (destino.excluir.valores.map(api.ccNormalizar_).includes(v)) { excluidas++; continue; }
    }
    if (destino.clave.some((c) => !fila[c])) continue;
    filas.push(fila);
  }
  return { filaEnc, sinMapear: mapeo.sinMapear, filas, excluidas };
}

console.log("\nEL ENCABEZADO NO SIEMPRE ESTA EN LA FILA 1");
// Leyendo siempre la 1 se mapearia CERO columnas y el error diria "faltan
// columnas obligatorias", que manda a buscar el problema donde no esta.
revisar("Scoring: encabezados en la fila 2", leerHoja("Scoring_Clientes").filaEnc, 1);
revisar("Aging: encabezados en la fila 2", leerHoja("Aging_CC_0001_0003").filaEnc, 1);
revisar("Cancelaciones: encabezados en la fila 1", leerHoja("Historial_Cancelaciones").filaEnc, 0);

console.log("\nTODAS LAS COLUMNAS DE LA PLANILLA REAL SE MAPEAN");
revisar("Scoring, nada sin mapear", leerHoja("Scoring_Clientes").sinMapear, []);
revisar("Aging, nada sin mapear", leerHoja("Aging_CC_0001_0003").sinMapear, []);
revisar("Cancelaciones, nada sin mapear", leerHoja("Historial_Cancelaciones").sinMapear, []);

console.log("\nLA EMPRESA DEL AGING VIENE COMO NUMERO");
// La clave primaria de `aging` es (empresa, comprobante). Subir "1" donde
// Supabase tiene "0001" NO pisa la fila: la DUPLICA.
const ag = leerHoja("Aging_CC_0001_0003");
revisar("el 1 del Aging se vuelve 0001", ag.filas[1].empresa, "0001");
revisar("y el 3 se vuelve 0003", ag.filas[2].empresa, "0003");
revisar("el Scoring ya venia bien y no se rompe",
  leerHoja("Scoring_Clientes").filas[0].empresa, "0001");
revisar("el vendedor queda en tres digitos", ag.filas[1].vendedor, "006");

console.log("\nQUE SE SUBE Y QUE NO DEL AGING");
revisar("la fila SUBTOTAL se excluye", ag.excluidas, 1);
revisar("quedan el recibo y las dos facturas", ag.filas.length, 3);
// Los recibos SI van: son los pagos a cuenta sin aplicar, y ya estan en
// Supabase. Son justo lo que explica que un saldo no cierre.
revisar("el recibo se sube", ag.filas[0].comprobante, "REC-00001585");
revisar("con importe negativo", ag.filas[0].pendiente, -104003.32);
// El atraso vacio de un recibo es "no aplica", NO "cero dias de atraso".
revisar("y con atraso en null, no en cero", ag.filas[0].atraso, null);
revisar("una factura si trae atraso", ag.filas[1].atraso, 12);

console.log("\nEL RESTO DE LOS CAMPOS");
const sc = leerHoja("Scoring_Clientes").filas[0];
revisar("el CUIT conserva los guiones", sc.cuit, "20-11111111-1");
revisar("la categoria con acento", sc.categoria, "CRÍTICO");
revisar("Fact. > 30 dias entra en fact_mayor_45", sc.fact_mayor_45, 2);
// `meses_en_mora_hist` es TEXTO en Supabase porque la planilla escribe "-"
// cuando el cliente no tiene historial. Tratarla como numero la volveria
// null y se perderia la diferencia entre "cero meses en mora" y "no sabemos".
revisar("con historial, el numero queda como texto", sc.meses_en_mora_hist, "3");
revisar("sin historial, el guion se conserva",
  leerHoja("Scoring_Clientes").filas[1].meses_en_mora_hist, "-");
revisar("y la peor categoria dice 'Sin historial'",
  leerHoja("Scoring_Clientes").filas[1].peor_categoria_hist, "Sin historial");
const ca = leerHoja("Historial_Cancelaciones").filas[0];
revisar("el periodo con mora", ca.periodo_con_mora, "2026-09-01");
revisar("el cliente_id conserva los ceros", ca.cliente_id, "005105");
revisar("la fecha de deteccion", ca.fecha_deteccion, "2026-09-14");


console.log(FALLOS.length ? `\n${FALLOS.length} FALLARON: ${FALLOS.join(", ")}` : "\nTODO OK");
process.exit(FALLOS.length ? 1 : 0);
