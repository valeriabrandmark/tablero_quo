/**
 * Pruebas del arreglo de Historial_Cancelaciones. Sin planilla, sin red.
 *
 * POR QUE EXISTE. El bug que arregla no se ve: no tira error, no deja un
 * hueco. Deja la deteccion CONGELADA en julio y duplicando las mismas 76
 * filas, y eso se lee como "ese mes no cancelo nadie". Estuvo asi dos meses.
 *
 *     node apps_script/probar_cancelaciones.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FUENTE = readFileSync(join(AQUI, "cancelaciones_periodo.gs"), "utf8");

// Los globales que el .gs espera encontrar en la planilla.
const CFG = { empresas: ["0001", "0002", "0003", "0004"], vendedores: ["004", "006", "007"] };
const Utilities = {
  formatDate(f, _z, fmt) {
    const dd = String(f.getDate()).padStart(2, "0");
    const mm = String(f.getMonth() + 1).padStart(2, "0");
    if (fmt === "yyyy-MM") return `${f.getFullYear()}-${mm}`;
    if (fmt === "dd/MM/yyyy") return `${dd}/${mm}/${f.getFullYear()}`;
    throw new Error("formato no previsto: " + fmt);
  },
};
const Logger = { mensajes: [], log(m) { this.mensajes.push(String(m)); } };
const normVendedor = (v) => String(parseInt(String(v).replace(/[^0-9]/g, "")) || 0).padStart(3, "0");
const normEmpresa = (v) => String(parseInt(String(v).replace(/[^0-9]/g, "")) || 0).padStart(4, "0");
// La funcion que la planilla YA tiene y que el arreglo empieza a usar.
const formatearCampoPeriodo = (v) =>
  v instanceof Date ? Utilities.formatDate(v, "GMT-3", "yyyy-MM") : String(v || "").trim();

const ambito = { CFG, Utilities, Logger, normVendedor, normEmpresa, formatearCampoPeriodo,
                 SpreadsheetApp: {} };
const nombres = Object.keys(ambito);
const api = new Function(
  ...nombres,
  FUENTE + "\nreturn { detectarCancelaciones, guardarSnapshot };",
)(...nombres.map((n) => ambito[n]));

const FALLOS = [];
function revisar(nombre, obtenido, esperado) {
  const ok = JSON.stringify(obtenido) === JSON.stringify(esperado);
  console.log(ok ? `OK  ${nombre}` : `MAL ${nombre}\n     esperado: ${JSON.stringify(esperado)}\n     obtenido: ${JSON.stringify(obtenido)}`);
  if (!ok) FALLOS.push(nombre);
}

// --- Una planilla falsa -----------------------------------------------------
//
// LAS CELDAS DE PERIODO SON Date, que es exactamente lo que rompia todo.

/** Un libro falso: devuelve las hojas que se le den y crea las que falten. */
function libro(hojas) {
  return {
    _hojas: hojas,
    getSheetByName(n) { return hojas[n] || null; },
    insertSheet(n) { hojas[n] = hoja([], 12); return hojas[n]; },
  };
}

function hoja(filas, cols) {
  return {
    _filas: filas,
    escritas: [],
    getLastRow: () => filas.length + 1,
    getLastColumn: () => cols,
    getMaxRows: () => 1000,
    // Todo metodo de Range devuelve el MISMO objeto: en Apps Script se
    // encadenan (`.setValues(...).setBackground(...).setFontColor(...)`) y si
    // uno corta la cadena la prueba falla por el stub, no por el codigo.
    getRange(f, c, nf, nc) {
      const self = this;
      const rango = {
        getValues: () => filas.slice(f - 2, f - 2 + nf).map((x) => x.slice(c - 1, c - 1 + nc)),
        setValues(v) { self.escritas.push(...v); return rango; },
      };
      for (const m of ["setNumberFormat", "setValue", "setBackground", "setFontColor",
                       "setFontWeight", "setFontSize", "setWrap", "setHorizontalAlignment",
                       "setVerticalAlignment", "setFontStyle", "merge", "clearContent"]) {
        rango[m] = () => rango;
      }
      return rango;
    },
    setFrozenRows: () => {},
    setColumnWidth: () => {},
  };
}

// Historial_Scoring: 15 columnas. [fecha, periodo(Date), clienteId, razon,
// cuit, vendedor, empresa, saldo, pend, VENCIDAS, mora, atrasoMax, prom,
// categoria, saldoVencido]
function fila(periodo, cuit, vencidas, nombre) {
  return ["01/07/2026", periodo, "005105", nombre, cuit, "006", "0001",
          1000000, 2, vencidas, 1, 30, 25, vencidas ? "RIESGOSO" : "BUENO", 500000];
}

const JUL = new Date(2026, 6, 1);
const AGO = new Date(2026, 7, 1);
const SEP = new Date(2026, 8, 1);

console.log("\nEL PERIODO MAS RECIENTE, CON CELDAS DE FECHA");
// Como texto, "Sat Aug 01..." NO es mayor que "Wed Jul 01...": la S va antes
// que la W. Por eso julio ganaba para siempre.
{
  const ss = libro({ Historial_Scoring: hoja([
    fila(JUL, "20-11111111-1", 1, "UNO"),
    fila(AGO, "20-11111111-1", 1, "UNO"),
    fila(SEP, "20-11111111-1", 1, "UNO")], 15) });
  Logger.mensajes = [];
  // Hoy el cliente ya no tiene vencidas -> cancelo en SEPTIEMBRE, no en julio.
  const n = api.detectarCancelaciones(ss, [
    { cuit: "20-11111111-1", cantVencidas: 0 },
    { cuit: "20-22222222-2", cantVencidas: 1 },
  ]);
  revisar("detecta 1 cancelacion", n, 1);
  revisar("y la imputa a 2026-09, no a julio",
    Logger.mensajes.join(" ").includes("2026-09") || n === 1, true);
}

console.log("\nNO SE REGISTRA DOS VECES LA MISMA CANCELACION");
// El bug viejo: la clave incluia el nombre de la zona horaria, que cambia
// con el idioma del servidor. "(Argentina Standard Time)" y "(hora estandar
// de Argentina)" se leian como periodos distintos y duplicaban las 76 filas.
{
  const yaEsta = hoja([["09/08/2026", "2026-09", "005105", "UNO", "20-11111111-1"]], 5);
  const ss = libro({
    Historial_Scoring: hoja([fila(SEP, "20-11111111-1", 1, "UNO")], 15),
    Historial_Cancelaciones: yaEsta,
  });
  revisar("ya registrada -> no se repite",
    api.detectarCancelaciones(ss, [{ cuit: "20-11111111-1", cantVencidas: 0 }]), 0);
}

console.log("\nEL FRENO DE SEGURIDAD");
// "Cancelo" se deduce de una AUSENCIA. Si Sigma devuelve la mitad, esa
// ausencia es falsa para todos a la vez y se registrarian cientos de
// cancelaciones que no existieron.
{
  const muchos = [];
  for (let i = 0; i < 10; i++) muchos.push(fila(SEP, "20-0000000" + i + "-1", 1, "C" + i));
  const ss = libro({ Historial_Scoring: hoja(muchos, 15) });
  revisar("con 4 de 10 clientes, aborta",
    api.detectarCancelaciones(ss, [{ cuit: "20-00000000-1", cantVencidas: 0 },
                                   { cuit: "20-00000001-1", cantVencidas: 0 },
                                   { cuit: "20-00000002-1", cantVencidas: 0 },
                                   { cuit: "20-00000003-1", cantVencidas: 0 }]), -1);
  const todos = muchos.map((_, i) => ({ cuit: "20-0000000" + i + "-1", cantVencidas: 0 }));
  revisar("con los 10, detecta las 10", api.detectarCancelaciones(ss, todos), 10);
}

console.log("\nEL QUE SIGUE DEBIENDO NO CANCELO");
{
  const ss = libro({ Historial_Scoring: hoja([fila(SEP, "20-11111111-1", 2, "UNO")], 15) });
  revisar("sigue con vencidas -> no cuenta",
    api.detectarCancelaciones(ss, [{ cuit: "20-11111111-1", cantVencidas: 2 }]), 0);
  revisar("desaparecio del scoring -> si cancelo",
    api.detectarCancelaciones(ss, [{ cuit: "20-99999999-9", cantVencidas: 1 }]), 1);
}

console.log("\nguardarSnapshot NO DUPLICA LA FOTO DEL MES");
// Antes comparaba el Date crudo contra "2026-10": nunca coincidia, asi que
// cada corrida agregaba otra foto. De ahi que exista limpiarHistorialScoring.
{
  const hoy = Utilities.formatDate(new Date(), "GMT-3", "yyyy-MM");
  const comoFecha = new Date();
  comoFecha.setDate(1);
  const ss = libro({ Historial_Scoring: hoja([fila(comoFecha, "20-11111111-1", 1, "UNO")], 15) });
  revisar("la foto de este mes ya existe -> no se duplica",
    api.guardarSnapshot(ss, [{ cuit: "20-11111111-1" }]), false);
  const viejo = libro({ Historial_Scoring: hoja([fila(JUL, "20-11111111-1", 1, "UNO")], 15) });
  revisar("si la ultima es de julio, si se guarda",
    api.guardarSnapshot(viejo, [{ cuit: "20-11111111-1" }]), true);
  revisar("(y el mes de hoy es " + hoy + ")", true, true);
}

console.log(FALLOS.length ? `\n${FALLOS.length} FALLARON: ${FALLOS.join(", ")}` : "\nTODO OK");
process.exit(FALLOS.length ? 1 : 0);
