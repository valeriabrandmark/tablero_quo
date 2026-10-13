/**
 * Pruebas de Sigma_AgingCtaCte.gs. Sin planilla, sin red, sin Sigma.
 *
 * POR QUE EXISTE. Los tres bugs que cubre no se ven: ninguno tira error ni
 * deja un hueco en la pantalla. Dejan la deteccion de cancelaciones CONGELADA
 * en julio duplicando las mismas 76 filas, y los 118 clientes del scoring
 * diciendo "Sin historial" con cuatro meses de fotos guardadas. Estuvo asi
 * dos meses sin que nada avisara.
 *
 * Los tres son el mismo error de fondo: la columna "Periodo" es una celda de
 * FECHA, y se la leia con String(). Eso no da "2026-07", da
 *
 *     "Wed Jul 01 2026 00:00:00 GMT-0300 (Argentina Standard Time)"
 *
 * Por eso las celdas de periodo de esta prueba son objetos Date de verdad: con
 * texto, los tres bugs desaparecen y la prueba no prueba nada.
 *
 *     node apps_script/probar_cancelaciones.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FUENTE = readFileSync(join(AQUI, "Sigma_AgingCtaCte.gs"), "utf8");

// Los servicios de Apps Script que el archivo espera. CFG, PESO, normVendedor,
// normEmpresa, claveCuit y formatearCampoPeriodo NO se stubean: las declara el
// propio archivo y se usan las de verdad.
const Utilities = {
  formatDate(f, _z, fmt) {
    const dd = String(f.getDate()).padStart(2, "0");
    const mm = String(f.getMonth() + 1).padStart(2, "0");
    if (fmt === "yyyy-MM") return `${f.getFullYear()}-${mm}`;
    if (fmt === "yyyy-MM-dd") return `${f.getFullYear()}-${mm}-${dd}`;
    if (fmt === "dd/MM/yyyy") return `${dd}/${mm}/${f.getFullYear()}`;
    throw new Error("formato no previsto: " + fmt);
  },
};
const Logger = { mensajes: [], log(m) { this.mensajes.push(String(m)); } };
// getUi() tira excepcion sin pantalla, que es el caso del trigger: avisar()
// tiene que seguir de largo. Se emula eso.
const SpreadsheetApp = { getUi() { throw new Error("sin pantalla"); } };
const PropertiesService = {
  getScriptProperties: () => ({ getProperty: () => null }),
};
const UrlFetchApp = { fetch() { throw new Error("la prueba no toca la red"); } };

const ambito = { Utilities, Logger, SpreadsheetApp, PropertiesService, UrlFetchApp };
const nombres = Object.keys(ambito);
const api = new Function(
  ...nombres,
  FUENTE +
    "\nreturn { detectarCancelaciones, guardarSnapshot, leerHistorial," +
    " generarScoringClientes, claveCuit, formatearCampoPeriodo, sigmaToken_ };",
)(...nombres.map((n) => ambito[n]));

const FALLOS = [];
function revisar(nombre, obtenido, esperado) {
  const ok = JSON.stringify(obtenido) === JSON.stringify(esperado);
  console.log(ok ? `OK  ${nombre}` : `MAL ${nombre}\n     esperado: ${JSON.stringify(esperado)}\n     obtenido: ${JSON.stringify(obtenido)}`);
  if (!ok) FALLOS.push(nombre);
}

// --- Una planilla falsa -----------------------------------------------------

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
function fila(periodo, cuit, vencidas, nombre, categoria) {
  return ["01/07/2026", periodo, "005105", nombre, cuit, "006", "0001",
          1000000, 2, vencidas, 1, 30, 25,
          categoria || (vencidas ? "RIESGOSO" : "BUENO"), 500000];
}

const JUL = new Date(2026, 6, 1);
const AGO = new Date(2026, 7, 1);
const SEP = new Date(2026, 8, 1);

console.log("\nEL TOKEN NO ESTA EN EL CODIGO");
// Si alguien lo vuelve a pegar en CFG, esta prueba no lo detecta, pero la de
// abajo si: sin la Script Property, sigmaToken_() tiene que reventar.
revisar("sin SIGMA_TOKEN, revienta con mensaje claro",
  (() => { try { api.sigmaToken_(); return "no reventó"; }
           catch (e) { return e.message.includes("SIGMA_TOKEN"); } })(), true);
revisar("y el token viejo no quedo en el archivo",
  /RRh55WZ|quomkt@sigma:[A-Za-z0-9+/=]{10}/.test(FUENTE), false);

console.log("\nformatearCampoPeriodo ES LA BASE DE TODO EL ARREGLO");
revisar("una celda de fecha da yyyy-MM", api.formatearCampoPeriodo(JUL), "2026-07");
revisar("un texto ya correcto no se toca", api.formatearCampoPeriodo("2026-08"), "2026-08");
revisar("vacio da vacio", api.formatearCampoPeriodo(""), "");
revisar("y con String() daria otra cosa", String(JUL) === "2026-07", false);

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
  // escritas[0] es el ENCABEZADO: la hoja no existia y detectarCancelaciones
  // la crea escribiendo primero los titulos. La cancelacion es la fila 1.
  const escritas = ss._hojas.Historial_Cancelaciones.escritas;
  revisar("y la imputa a 2026-09, no a julio", escritas[1][1], "2026-09");
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
{
  // Y la fila vieja con el periodo CORRUPTO tambien tiene que reconocerse,
  // porque es lo que hay en la planilla hoy: 76 filas con ese texto.
  const corrupto = "Wed Sep 01 2026 00:00:00 GMT-0300 (Argentina Standard Time)";
  const yaEsta = hoja([["09/08/2026", corrupto, "005105", "UNO", "20-11111111-1"]], 5);
  const ss = libro({
    Historial_Scoring: hoja([fila(SEP, "20-11111111-1", 1, "UNO")], 15),
    Historial_Cancelaciones: yaEsta,
  });
  // No se reconoce --el texto crudo no es "2026-09"-- y se registra de nuevo.
  // Queda documentado a proposito: es la razon por la que hay que limpiar las
  // filas viejas y no solo arreglar el codigo.
  revisar("ojo: la fila vieja corrupta NO se reconoce (hay que limpiarla)",
    api.detectarCancelaciones(ss, [{ cuit: "20-11111111-1", cantVencidas: 0 }]), 1);
}

console.log("\nEL CUIT ES LA CLAVE, NO EL ClienteId");
// El mismo CUIT en dos empresas es un solo cliente. Y 47 de las 211 filas de
// Historial_Scoring no tienen ClienteId: por eso no sirve como clave.
revisar("el CUIT se normaliza sin guiones", api.claveCuit("20-11111111-1"), "20111111111");
revisar("con espacios y puntos tambien", api.claveCuit(" 20.111.111/11 1 "), "20111111111");
revisar("vacio da vacio", api.claveCuit(null), "");

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
  const comoFecha = new Date();
  comoFecha.setDate(1);
  const ss = libro({ Historial_Scoring: hoja([fila(comoFecha, "20-11111111-1", 1, "UNO")], 15) });
  revisar("la foto de este mes ya existe -> no se duplica",
    api.guardarSnapshot(ss, [{ cuit: "20-11111111-1" }]), false);
  const viejo = libro({ Historial_Scoring: hoja([fila(JUL, "20-11111111-1", 1, "UNO")], 15) });
  revisar("si la ultima es de julio, si se guarda",
    api.guardarSnapshot(viejo, [{ cuit: "20-11111111-1" }]), true);
}

console.log("\nleerHistorial: LOS MESES EN MORA Y LA PEOR CATEGORIA");
// Este es el bug que dejaba a los 118 clientes en "Sin historial". El
// .split('-') partia por el guion del GMT-0300, parseInt daba NaN, y
// fechaPeriodo quedaba Invalid Date: el filtro de los ultimos 6 meses no
// dejaba pasar NINGUNA foto.
{
  const ss = libro({ Historial_Scoring: hoja([
    fila(JUL, "20-11111111-1", 1, "UNO", "CRÍTICO"),
    fila(AGO, "20-11111111-1", 1, "UNO", "RIESGOSO"),
    fila(SEP, "20-11111111-1", 0, "UNO", "AL DÍA")], 15) });
  const h = api.leerHistorial(ss);
  const uno = h["20111111111"];
  revisar("el cliente aparece, indexado por CUIT", !!uno, true);
  revisar("cuenta 2 meses en mora (CRITICO + RIESGOSO)", uno && uno.mesesEnMora, 2);
  revisar("la peor categoria es CRITICO", uno && uno.peorCategoria, "CRÍTICO");
  revisar("la categoria anterior es la del mes MAS RECIENTE",
    uno && uno.categoriaAnterior, "AL DÍA");
}
{
  // Y el cruce con el scoring: antes buscaba historial[clienteId] y no
  // encontraba nada.
  const ss = libro({ Historial_Scoring: hoja([
    fila(AGO, "20-11111111-1", 1, "UNO", "CRÍTICO")], 15) });
  const h = api.leerHistorial(ss);
  const filas = api.generarScoringClientes([{
    clienteId: "005105", clienteNombre: "UNO", clienteNumeroDocumento: "20-11111111-1",
    vendedor: "006", empresa: "0001", fecha: "01/09/2026", plazo: "0",
    pendientePago: 100000,
  }], h);
  revisar("el scoring encuentra su historial", filas[0].peorCategoriaHist, "CRÍTICO");
  revisar("y ya no dice 'Sin historial'", filas[0].mesesEnMoraHist, 1);
  revisar("la tendencia se calcula", filas[0].tendencia !== "Sin historial", true);
}

console.log(FALLOS.length ? `\n${FALLOS.length} FALLARON: ${FALLOS.join(", ")}` : "\nTODO OK");
process.exit(FALLOS.length ? 1 : 0);
