/**
 * ARREGLO DE Historial_Cancelaciones — reemplaza DOS funciones de
 * `Sigma_AgingCtaCte.gs` (la planilla "Analisis de cuenta corriente").
 *
 * ⚠️ NO AGREGAR ESTE ARCHIVO AL PROYECTO. En Apps Script una funcion
 * duplicada NO da error: gana la ultima que carga, en silencio, y las dos
 * quedan ahi para que alguien lea la que no corre. Hay que PEGAR estas dos
 * funciones ENCIMA de las que ya existen con el mismo nombre.
 *
 * =========================================================================
 *  QUE ESTABA PASANDO
 * =========================================================================
 *
 * La deteccion de cancelaciones quedo congelada en julio de 2026 y ademas
 * duplico sus 76 filas. Las dos cosas salen del mismo error.
 *
 * La columna "Periodo" de `Historial_Scoring` es una celda de FECHA, y el
 * codigo la leia con `String(fila[1])`. Eso no da "2026-07": da
 *
 *     "Wed Jul 01 2026 00:00:00 GMT-0300 (Argentina Standard Time)"
 *
 * De ahi salen los dos sintomas:
 *
 * 1. CONGELADO EN JULIO. El ultimo periodo se elegia comparando esos textos
 *    con `>`. Y como texto, "Sat Aug 01..." NO es mayor que "Wed Jul 01...":
 *    la S va antes que la W. Julio gana siempre, sin importar cuantos meses
 *    pasen. Verificado en Supabase: los 152 registros dicen julio y la
 *    ultima deteccion es del 09/08/2026.
 *
 * 2. DUPLICADO. La clave para no re-registrar una cancelacion era
 *    `clienteId|periodo`, y ese texto incluye EL NOMBRE DE LA ZONA HORARIA,
 *    que cambia con el idioma del servidor que ejecuta el trigger. En
 *    Supabase conviven las dos variantes:
 *
 *        ...GMT-0300 (Argentina Standard Time)        76 filas
 *        ...GMT-0300 (hora estandar de Argentina)     76 filas
 *
 *    Los mismos 76 clientes, contados dos veces, porque para el codigo son
 *    periodos distintos.
 *
 * EL ARREGLO NO INVENTA NADA: la planilla YA tiene `formatearCampoPeriodo()`,
 * que devuelve "yyyy-MM", y ya se usa en las funciones que suben a Supabase.
 * Lo unico que faltaba era usarla tambien aca.
 *
 * Se aprovecha para dos mejoras que vienen de la version de la otra planilla:
 *
 *   SE COMPARA POR CUIT Y NO POR clienteId. El CUIT es el mismo numero en
 *   las cuatro empresas; el clienteId no tiene por que serlo.
 *
 *   UN FRENO DE SEGURIDAD. Si un dia Sigma devuelve la mitad de los clientes
 *   --se cayo la API, cambio un filtro-- TODOS los que tenian mora
 *   apareceran "sin facturas vencidas" y se registrarian como cancelados.
 *   Son cientos de filas falsas imposibles de distinguir despues. Ante esa
 *   señal se aborta y se avisa.
 */

// ===========================================================================
//  1 de 2 — reemplaza `detectarCancelaciones` en Sigma_AgingCtaCte.gs
// ===========================================================================

function detectarCancelaciones(ss, filasScoring) {
  const hojaHist = ss.getSheetByName('Historial_Scoring');
  if (!hojaHist || hojaHist.getLastRow() <= 1) {
    Logger.log('Sin historial previo — no se pueden detectar cancelaciones.');
    return 0;
  }

  const ultimaFila = hojaHist.getLastRow();
  const numCols    = Math.min(hojaHist.getLastColumn(), 15);
  const datos      = hojaHist.getRange(2, 1, ultimaFila - 1, numCols).getValues();

  // EL CUIT COMO CLAVE, no el clienteId: es el mismo numero en las cuatro
  // empresas, asi que un cliente que compra en 0001 y en 0003 no se cuenta
  // dos veces.
  function clave(valor) {
    return String(valor || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase();
  }

  // `formatearCampoPeriodo` ya existe en la planilla y devuelve "yyyy-MM".
  // Usarla es TODO el arreglo: sin ella, la comparacion de abajo se hace
  // sobre "Wed Jul 01 2026 ..." y julio gana para siempre.
  let ultimoPeriodo = '';
  datos.forEach(function(fila) {
    const p        = formatearCampoPeriodo(fila[1]);
    const vendedor = normVendedor(fila[5]);
    const empresa  = normEmpresa(fila[6]);
    if (!CFG.vendedores.includes(vendedor)) return;
    if (!CFG.empresas.includes(empresa))    return;
    if (p && p > ultimoPeriodo) ultimoPeriodo = p;
  });

  if (!ultimoPeriodo) {
    Logger.log('Sin foto previa de vendedores configurados.');
    return 0;
  }

  const estadoAnterior = {};
  const vistosAnt = {};
  datos.forEach(function(fila) {
    if (formatearCampoPeriodo(fila[1]) !== ultimoPeriodo) return;
    const vendedor = normVendedor(fila[5]);
    const empresa  = normEmpresa(fila[6]);
    if (!CFG.vendedores.includes(vendedor)) return;
    if (!CFG.empresas.includes(empresa))    return;

    const cuit = clave(fila[4]);
    if (!cuit) return;
    vistosAnt[cuit] = true;

    if ((Number(fila[9]) || 0) === 0) return;   // no tenia facturas vencidas

    estadoAnterior[cuit] = {
      periodo:               ultimoPeriodo,
      clienteId:             String(fila[2] || ''),
      razonSocial:           String(fila[3] || ''),
      cuit:                  String(fila[4] || ''),
      vendedor:              vendedor,
      empresa:               empresa,
      maxAtraso:             Number(fila[11]) || 0,
      promedioAtrasoGeneral: Number(fila[12]) || 0,
      categoria:             String(fila[13] || ''),
      saldoVencido:          numCols >= 15 ? (Number(fila[14]) || 0) : 0
    };
  });

  if (Object.keys(estadoAnterior).length === 0) {
    Logger.log('Ningún cliente con mora en el período anterior (' + ultimoPeriodo + ').');
    return 0;
  }

  // EL FRENO. "Cancelo" se deduce de una AUSENCIA: el cliente ya no tiene
  // facturas vencidas. Si la descarga de hoy vino corta, esa ausencia es
  // falsa para todos a la vez.
  const clientesAntes = Object.keys(vistosAnt).length;
  if (filasScoring.length < clientesAntes * 0.5) {
    Logger.log('⚠️ Hoy hay ' + filasScoring.length + ' clientes contra ' + clientesAntes +
               ' de la foto anterior. Se aborta la deteccion por seguridad.');
    return -1;
  }

  const yaRegistrado = {};
  const hojaCanExistente = ss.getSheetByName('Historial_Cancelaciones');
  if (hojaCanExistente && hojaCanExistente.getLastRow() > 1) {
    // Se leen 5 columnas y no 3: el CUIT esta en la quinta.
    const datosCan = hojaCanExistente.getRange(2, 1, hojaCanExistente.getLastRow() - 1, 5).getValues();
    datosCan.forEach(function(fila) {
      yaRegistrado[clave(fila[4]) + '|' + formatearCampoPeriodo(fila[1])] = true;
    });
  }

  const estadoActual = {};
  filasScoring.forEach(function(f) {
    const cuit = clave(f.cuit);
    if (cuit) estadoActual[cuit] = f;
  });

  const cancelaciones  = [];
  const fechaDeteccion = Utilities.formatDate(new Date(), 'GMT-3', 'dd/MM/yyyy');

  Object.keys(estadoAnterior).forEach(function(cuit) {
    if (yaRegistrado[cuit + '|' + ultimoPeriodo]) return;

    const ant    = estadoAnterior[cuit];
    const actual = estadoActual[cuit];
    if ((actual ? (actual.cantVencidas || 0) : 0) !== 0) return;

    cancelaciones.push([
      fechaDeteccion, ant.periodo, ant.clienteId,
      ant.razonSocial, ant.cuit, ant.vendedor, ant.empresa,
      ant.saldoVencido, ant.categoria,
      ant.maxAtraso, ant.promedioAtrasoGeneral,
      actual ? 'Cancelación total — sigue activo sin mora'
             : 'Cancelación total — sin más saldo pendiente'
    ]);
  });

  if (cancelaciones.length === 0) {
    Logger.log('Sin cancelaciones NUEVAS detectadas para el período ' + ultimoPeriodo + '.');
    return 0;
  }

  const NOMBRE = 'Historial_Cancelaciones';
  var hojaCan = ss.getSheetByName(NOMBRE);
  if (!hojaCan) {
    hojaCan = ss.insertSheet(NOMBRE);
    const HEADERS = [
      'Fecha Detección', 'Período con Mora', 'ClienteId', 'Razón Social', 'CUIT',
      'Vendedor', 'Empresa', 'Saldo Vencido Cancelado ($)',
      'Categoría que Tenía', 'Días Atraso Máx al Cancelar',
      'Días Atraso Prom al Cancelar', 'Tipo de Cancelación'
    ];
    hojaCan.getRange(1, 1, 1, HEADERS.length)
           .setValues([HEADERS])
           .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold');
    hojaCan.setFrozenRows(1);
  }

  // El periodo y el CUIT como TEXTO: si la columna queda en formato fecha o
  // numero, la proxima corrida vuelve a leer un Date y el bug vuelve.
  hojaCan.getRange(1, 2, hojaCan.getMaxRows(), 1).setNumberFormat('@');
  hojaCan.getRange(1, 3, hojaCan.getMaxRows(), 1).setNumberFormat('@');
  hojaCan.getRange(1, 5, hojaCan.getMaxRows(), 1).setNumberFormat('@');

  const primeraFilaLibre = hojaCan.getLastRow() + 1;
  hojaCan.getRange(primeraFilaLibre, 1, cancelaciones.length, 12).setValues(cancelaciones);
  hojaCan.getRange(primeraFilaLibre, 8, cancelaciones.length, 1).setNumberFormat('$ #,##0.00');

  Logger.log('✓ Cancelaciones NUEVAS registradas: ' + cancelaciones.length);
  return cancelaciones.length;
}

// ===========================================================================
//  2 de 2 — reemplaza `guardarSnapshot` en Sigma_AgingCtaCte.gs
// ===========================================================================
//
// MISMO BUG, OTRO SINTOMA. El chequeo de "¿ya existe la foto de este mes?"
// comparaba el contenido CRUDO de la celda contra el texto "2026-10". Una
// celda de fecha nunca es igual a un texto, asi que el chequeo no frenaba
// nada: cada corrida agregaba otra foto del mismo mes. Por eso existe
// `limpiarHistorialScoring`, que borra los duplicados a mano.
//
// Solo cambia el `.map(...)` del chequeo. El resto queda igual.

function guardarSnapshot(ss, filasScoring) {
  const periodoActual = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM');
  const fechaHoy      = Utilities.formatDate(new Date(), 'GMT-3', 'dd/MM/yyyy');
  const NOMBRE        = 'Historial_Scoring';

  var hoja = ss.getSheetByName(NOMBRE);
  if (!hoja) {
    hoja = ss.insertSheet(NOMBRE);
    const HEADERS_H = [
      'Fecha', 'Periodo', 'ClienteId', 'Razón Social', 'CUIT',
      'Vendedor', 'Empresa', 'Saldo Total', 'Fact. Pendientes',
      'Fact. Vencidas', 'Fact. > 30 días', 'Atraso Máx',
      'Atraso Prom General', 'Categoría', 'Saldo Vencido'
    ];
    hoja.getRange(1, 1, 1, HEADERS_H.length)
        .setValues([HEADERS_H])
        .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold');
    hoja.setFrozenRows(1);
  }

  hoja.getRange(1, 2, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 3, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 5, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 6, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 7, hoja.getMaxRows(), 1).setNumberFormat('@');

  const ultimaFila = hoja.getLastRow();
  if (ultimaFila > 1) {
    // ACA ESTABA EL BUG: antes era `.map(function(r){ return r[0]; })`, que
    // devuelve el Date crudo y nunca coincide con "2026-10".
    const periodos = hoja.getRange(2, 2, ultimaFila - 1, 1).getValues()
                         .map(function(r) { return formatearCampoPeriodo(r[0]); });
    if (periodos.indexOf(periodoActual) !== -1) {
      Logger.log('Foto de ' + periodoActual + ' ya existe — no se duplica.');
      return false;
    }
  }

  const filasNuevas = filasScoring.map(function(f) {
    return [
      fechaHoy, periodoActual,
      String(f.clienteId || ''),
      f.razonSocial           || '',
      String(f.cuit || ''),
      f.vendedor              || '',
      f.empresa               || '',
      f.saldoTotal            || 0,
      f.cantFacturas          || 0,
      f.cantVencidas          || 0,
      f.cantMoraReal          || 0,
      f.maxAtraso             || 0,
      f.promedioAtrasoGeneral || 0,
      f.categoria             || '',
      f.saldoVencido          || 0
    ];
  });

  const primeraFilaLibre = hoja.getLastRow() + 1;
  hoja.getRange(primeraFilaLibre, 1, filasNuevas.length, 15).setValues(filasNuevas);
  hoja.getRange(primeraFilaLibre, 8,  filasNuevas.length, 1).setNumberFormat('#,##0.00');
  hoja.getRange(primeraFilaLibre, 15, filasNuevas.length, 1).setNumberFormat('#,##0.00');

  Logger.log('✓ Foto de ' + periodoActual + ' guardada: ' + filasNuevas.length + ' clientes.');
  return true;
}
