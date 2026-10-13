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
  "ccMapearColumnas_", "CC_DESTINO",
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

console.log(FALLOS.length ? `\n${FALLOS.length} FALLARON: ${FALLOS.join(", ")}` : "\nTODO OK");
process.exit(FALLOS.length ? 1 : 0);
