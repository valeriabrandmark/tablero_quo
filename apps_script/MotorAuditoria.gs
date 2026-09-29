// ============================================================================
//  MotorAuditoria.gs — planilla "2026 - LOGISTICA BRANDMARK - UNIBRAND"
// ============================================================================
//
//  COPIA DE RESPALDO. El original vive adentro del Google Sheet. Ver el
//  README de esta carpeta.
//
//  PENDIENTE: `subirAuditoriaASupabase` (al final del archivo) es el camino
//  viejo, por /rest/v1 con la SERVICE KEY guardada en la planilla. No puede
//  escribir --bronze.auditoria_sevillanita no tiene permisos para ningun rol
//  de la API-- y lo reemplaza `subirAuditoria`, en auditoria_sevillanita.gs,
//  que va por la Edge Function. Se borra junto con la propiedad
//  SUPABASE_SERVICE_KEY.
// ============================================================================

// ============================================================================
// MOTOR DE AUDITORÍA — archivo único, consolidado. Reemplaza a PendienteSubir,
// AnalisisTarifa, AnalisisPeso, AccionesFinal y AnalisisFinanciero, que ya no
// deben existir como archivos separados (se borran después de pegar esto).
// ============================================================================

// ===== PENDIENTE DE SUBIR =====
const HOJA_FLETE_PS = "FLETE";
const COLOR_PENDIENTE = "#FFF2CC";
const NOMBRE_COL_PENDIENTE = "_pendiente_subir";

function _colPendiente_() {
  const CANTIDAD_COLUMNAS_PRINCIPAL = 25;
  return CANTIDAD_COLUMNAS_PRINCIPAL + 4;
}

function onEdit(e) {
  try {
    const rango = e.range;
    const hoja = rango.getSheet();
    if (hoja.getName() !== HOJA_FLETE_PS) return;
    if (rango.getRow() === 1) return;

    const headers = hoja.getRange(1, 1, 1, hoja.getLastColumn()).getValues()[0];
    const idxNeto = headers.indexOf("NETO COBRADO POR EL TRANSPORTE");
    if (idxNeto === -1) return;

    const colNeto = idxNeto + 1;
    const primeraColEditada = rango.getColumn();
    const ultimaColEditada = rango.getLastColumn();
    if (colNeto < primeraColEditada || colNeto > ultimaColEditada) return;

    const colPend = _colPendiente_();
    _asegurarEncabezadoPendiente_(hoja, colPend);

    const filaDesde = rango.getRow();
    const filaHasta = rango.getLastRow();
    for (let f = filaDesde; f <= filaHasta; f++) {
      hoja.getRange(f, colPend).setValue("SI");
      hoja.getRange(f, colNeto).setBackground(COLOR_PENDIENTE);
    }
  } catch (err) {
    Logger.log("onEdit (PendienteSubir) error: " + err);
  }
}

function _asegurarEncabezadoPendiente_(hoja, colPend) {
  const actual = hoja.getRange(1, colPend).getValue();
  if (actual === NOMBRE_COL_PENDIENTE) return;
  hoja.getRange(1, colPend).setValue(NOMBRE_COL_PENDIENTE);
  const letraCol = columnaALetra_PS(colPend);
  const filaLabel = 1, colLabel = colPend + 2, colValor = colPend + 3;
  hoja.getRange(filaLabel, colLabel).setValue("⏳ Fletes cargados sin subir a Supabase:");
  hoja.getRange(filaLabel, colLabel).setFontWeight("bold");
  hoja.getRange(filaLabel, colValor).setFormula('=COUNTIF(' + letraCol + '2:' + letraCol + '10000;"SI")');
  hoja.getRange(filaLabel, colValor).setFontWeight("bold").setBackground(COLOR_PENDIENTE);
}

function marcarFletesComoSincronizados() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName(HOJA_FLETE_PS);
  const ui = SpreadsheetApp.getUi();
  const headers = hoja.getRange(1, 1, 1, hoja.getLastColumn()).getValues()[0];
  const idxNeto = headers.indexOf("NETO COBRADO POR EL TRANSPORTE");
  const colPend = _colPendiente_();
  if (idxNeto === -1 || hoja.getRange(1, colPend).getValue() !== NOMBRE_COL_PENDIENTE) {
    ui.alert("No encontré la columna de pendientes todavía. Cargá o editá al menos un flete primero.");
    return;
  }
  const colNeto = idxNeto + 1;
  const ultimaFila = hoja.getLastRow();
  if (ultimaFila < 2) return;
  const datos = hoja.getRange(2, 1, ultimaFila - 1, hoja.getLastColumn()).getValues();
  let limpiadas = 0;
  for (let i = 0; i < datos.length; i++) {
    const fila = i + 2;
    const marcado = datos[i][colPend - 1] === "SI";
    const tieneNeto = datos[i][colNeto - 1] !== "" && datos[i][colNeto - 1] !== null;
    if (marcado && tieneNeto) {
      hoja.getRange(fila, colPend).setValue("");
      hoja.getRange(fila, colNeto).setBackground(null);
      limpiadas++;
    }
  }
  ui.alert("✅ Marcadas como sincronizadas: " + limpiadas + " fila(s).\n\nSi el contador de arriba no da 0, son filas que quedaron vacías después de haber sido editadas -- revisalas antes de darlas por perdidas.");
}

function columnaALetra_PS(n) {
  let s = "";
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

// ===== ANALISIS_TARIFA (Eje 1) =====
function calcularAnalisisTarifa() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const flHoja = ss.getSheetByName('FLETE');
  const parHoja = ss.getSheetByName('PARAMETROS');
  const tkHoja = ss.getSheetByName('TARIFARIO_KG');
  const mrHoja = ss.getSheetByName('MAPEO_RAMALES');
  if (!flHoja || !parHoja || !tkHoja || !mrHoja) { ui.alert('Falta alguna hoja (FLETE, PARAMETROS, TARIFARIO_KG o MAPEO_RAMALES).'); return; }

  const MINIMO_BASE = Number(parHoja.getRange('B2').getValue());
  const SEGURO_PCT = Number(parHoja.getRange('B3').getValue());
  const UMBRAL = Number(parHoja.getRange('B5').getValue()) || 10000;

  const MESES = { ENE:0, FEB:1, MAR:2, ABR:3, MAY:4, JUN:5, JUL:6, AGO:7, SEP:8, OCT:9, NOV:10, DIC:11 };
  const coefFilas = [];
  for (let r = 2; r < 50; r++) {
    const celda = parHoja.getRange(r, 8).getValue();
    if (celda === '' || celda === null) break;
    const coef = Number(parHoja.getRange(r, 10).getValue());
    let fechaMes = null;
    if (celda instanceof Date) fechaMes = new Date(celda.getFullYear(), celda.getMonth(), 1);
    else {
      const m = String(celda).match(/^([A-ZÑ]{3})[.\-\s].*?(\d{4})/i);
      if (m && (m[1].toUpperCase() in MESES)) fechaMes = new Date(Number(m[2]), MESES[m[1].toUpperCase()], 1);
    }
    if (fechaMes) coefFilas.push({ fecha: fechaMes, coef: coef });
  }
  coefFilas.sort((a, b) => a.fecha - b.fecha);
  function coeficienteParaFecha(fecha) {
    let elegido = coefFilas.length ? coefFilas[0].coef : 1;
    for (const f of coefFilas) { if (f.fecha <= fecha) elegido = f.coef; else break; }
    return elegido;
  }

  const mrDatos = mrHoja.getDataRange().getValues();
  const mapaRamal = {};
  for (let i = 1; i < mrDatos.length; i++) { const loc = String(mrDatos[i][0] || '').trim().toUpperCase(); if (loc) mapaRamal[loc] = mrDatos[i][2]; }

  const tkDatos = tkHoja.getDataRange().getValues();
  const tkHeaders = tkDatos[0];
  const colPorRamal = {};
  for (let c = 1; c < tkHeaders.length; c++) colPorRamal[String(tkHeaders[c]).trim().toUpperCase()] = c;
  const filaPorTramo = {};
  for (let i = 1; i < tkDatos.length; i++) filaPorTramo[Number(tkDatos[i][0])] = i;
  function tarifaTabla(kg, ramal) {
    let tramo = Math.ceil(kg / 10) * 10;
    if (tramo < 10) tramo = 10;
    if (tramo > 1000) tramo = 1000;
    const filaIdx = filaPorTramo[tramo];
    const colIdx = colPorRamal[String(ramal).trim().toUpperCase()];
    if (filaIdx === undefined || colIdx === undefined) return { tramo: tramo, valor: null };
    return { tramo: tramo, valor: Number(tkDatos[filaIdx][colIdx]) };
  }

  const flDatos = flHoja.getDataRange().getValues();
  const flHead = flDatos[0];
  const idx = {};
  ['FECHA', 'LOCALIDAD', 'TRANSPORTE', 'KG TRANSP', 'NETO FACTURADO AL CLIENTE', 'NETO COBRADO POR EL TRANSPORTE', 'N° FACTURA SEVILLANITA'].forEach(function (h) { idx[h] = flHead.indexOf(h); });
  const faltantes = Object.keys(idx).filter(function (h) { return idx[h] === -1; });
  if (faltantes.length) { ui.alert('Faltan columnas en FLETE: ' + faltantes.join(', ')); return; }

  const salida = [];
  let sinMapeo = 0, sobrecobro = 0, cobroDeMenos = 0, ok = 0;
  for (let i = 1; i < flDatos.length; i++) {
    const fila = flDatos[i];
    if (fila[idx['TRANSPORTE']] !== 'SEVILLANITA BRAND') continue;
    const kgTransp = fila[idx['KG TRANSP']];
    const netoCobrado = fila[idx['NETO COBRADO POR EL TRANSPORTE']];
    if (kgTransp === '' || kgTransp === null || netoCobrado === '' || netoCobrado === null) continue;
    const fecha = fila[idx['FECHA']];
    const localidad = String(fila[idx['LOCALIDAD']] || '').trim();
    const netoFacturado = Number(fila[idx['NETO FACTURADO AL CLIENTE']]) || 0;
    const factura = fila[idx['N° FACTURA SEVILLANITA']];
    const ramal = mapaRamal[localidad.toUpperCase()];
    if (!ramal) { salida.push([factura, fecha, localidad, 'SIN MAPEO', kgTransp, '', '', '', '', '', '', 'SIN MAPEO']); sinMapeo++; continue; }
    const resultadoTarifa = tarifaTabla(Number(kgTransp), ramal);
    const tramo = resultadoTarifa.tramo;
    const tabla = resultadoTarifa.valor;
    const coef = coeficienteParaFecha(fecha instanceof Date ? fecha : new Date(fecha));
    const minimoVigente = MINIMO_BASE * coef;
    const tablaCoef = (tabla || 0) * coef;
    const esperadoSinSeguro = Math.max(tablaCoef, minimoVigente);
    const seguro = netoFacturado * SEGURO_PCT;
    const esperadoTotal = esperadoSinSeguro + seguro;
    const diferencia = Number(netoCobrado) - esperadoTotal;
    let veredicto;
    if (diferencia > UMBRAL) { veredicto = 'SOBRECOBRO'; sobrecobro++; }
    else if (diferencia < -UMBRAL) { veredicto = 'COBRO DE MENOS'; cobroDeMenos++; }
    else { veredicto = 'OK'; ok++; }
    salida.push([factura, fecha, localidad, ramal, Number(kgTransp), tramo, Math.round(coef * 1000000) / 1000000,
      Math.round(esperadoTotal * 100) / 100, Math.round(seguro * 100) / 100, Number(netoCobrado), Math.round(diferencia * 100) / 100, veredicto]);
  }

  let at = ss.getSheetByName('ANALISIS_TARIFA');
  if (!at) at = ss.insertSheet('ANALISIS_TARIFA');
  at.clear();
  const headers = ['N° FACTURA', 'FECHA', 'LOCALIDAD', 'RAMAL', 'KG DECLARADO', 'TRAMO', 'COEFICIENTE', 'ESPERADO TOTAL', 'SEGURO', 'COBRADO', 'DIFERENCIA', 'VEREDICTO'];
  at.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  if (salida.length) {
    at.getRange(2, 1, salida.length, headers.length).setValues(salida);
    at.getRange(2, 2, salida.length, 1).setNumberFormat('dd/mm/yyyy');
    at.getRange(2, 8, salida.length, 3).setNumberFormat('$#,##0.00');
    at.getRange(2, 10, salida.length, 2).setNumberFormat('$#,##0.00');
  }
  at.setFrozenRows(1);
  ui.alert('✅ Análisis de tarifa calculado.\n\nFacturas procesadas: ' + salida.length + '\n  OK: ' + ok + '\n  SOBRECOBRO: ' + sobrecobro + '\n  COBRO DE MENOS: ' + cobroDeMenos + '\n  Sin mapeo de ramal: ' + sinMapeo);
}

// ===== ANALISIS_PESO (Eje 2) =====
function calcularAnalisisPeso() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const flHoja = ss.getSheetByName('FLETE');
  const parHoja = ss.getSheetByName('PARAMETROS');
  const tkHoja = ss.getSheetByName('TARIFARIO_KG');
  const mrHoja = ss.getSheetByName('MAPEO_RAMALES');
  if (!flHoja || !parHoja || !tkHoja || !mrHoja) { ui.alert('Falta alguna hoja (FLETE, PARAMETROS, TARIFARIO_KG o MAPEO_RAMALES).'); return; }

  const MINIMO_BASE = Number(parHoja.getRange('B2').getValue());
  const TARA = Number(parHoja.getRange('E2').getValue());
  const UMBRAL = Number(parHoja.getRange('B5').getValue()) || 10000;
  if (TARA > 20) { ui.alert('⚠️ La celda E2 de PARAMETROS dice ' + TARA + '. Tiene que ser un número chico, tipo 1.881. Corregila antes de seguir — no calculé nada.'); return; }

  const MESES = { ENE:0, FEB:1, MAR:2, ABR:3, MAY:4, JUN:5, JUL:6, AGO:7, SEP:8, OCT:9, NOV:10, DIC:11 };
  const coefFilas = [];
  for (let r = 2; r < 50; r++) {
    const celda = parHoja.getRange(r, 8).getValue();
    if (celda === '' || celda === null) break;
    const coef = Number(parHoja.getRange(r, 10).getValue());
    let fechaMes = null;
    if (celda instanceof Date) fechaMes = new Date(celda.getFullYear(), celda.getMonth(), 1);
    else {
      const m = String(celda).match(/^([A-ZÑ]{3})[.\-\s].*?(\d{4})/i);
      if (m && (m[1].toUpperCase() in MESES)) fechaMes = new Date(Number(m[2]), MESES[m[1].toUpperCase()], 1);
    }
    if (fechaMes) coefFilas.push({ fecha: fechaMes, coef: coef });
  }
  coefFilas.sort((a, b) => a.fecha - b.fecha);
  function coeficienteParaFecha(fecha) {
    let elegido = coefFilas.length ? coefFilas[0].coef : 1;
    for (const f of coefFilas) { if (f.fecha <= fecha) elegido = f.coef; else break; }
    return elegido;
  }

  const mrDatos = mrHoja.getDataRange().getValues();
  const mapaRamal = {};
  for (let i = 1; i < mrDatos.length; i++) { const loc = String(mrDatos[i][0] || '').trim().toUpperCase(); if (loc) mapaRamal[loc] = mrDatos[i][2]; }

  const tkDatos = tkHoja.getDataRange().getValues();
  const colPorRamal = {};
  for (let c = 1; c < tkDatos[0].length; c++) colPorRamal[String(tkDatos[0][c]).trim().toUpperCase()] = c;
  const filaPorTramo = {};
  for (let i = 1; i < tkDatos.length; i++) filaPorTramo[Number(tkDatos[i][0])] = i;
  function fleteTabla(kg, ramal, coef) {
    let tramo = Math.ceil(kg / 10) * 10;
    if (tramo < 10) tramo = 10;
    if (tramo > 1000) tramo = 1000;
    const fi = filaPorTramo[tramo], ci = colPorRamal[String(ramal).trim().toUpperCase()];
    if (fi === undefined || ci === undefined) return null;
    const tabla = Number(tkDatos[fi][ci]);
    return Math.max(tabla * coef, MINIMO_BASE * coef);
  }

  const flDatos = flHoja.getDataRange().getValues();
  const flHead = flDatos[0];
  const idx = {};
  ['FECHA', 'LOCALIDAD', 'TRANSPORTE', 'CANTIDAD', 'KG DISTRI', 'KG TRANSP', 'N° FACTURA SEVILLANITA'].forEach(function (h) { idx[h] = flHead.indexOf(h); });
  const faltantes = Object.keys(idx).filter(function (h) { return idx[h] === -1; });
  if (faltantes.length) { ui.alert('Faltan columnas en FLETE: ' + faltantes.join(', ')); return; }

  const salida = [];
  let exceso = 0, leve = 0, consistente = 0, dudoso = 0, noConf = 0, sinDato = 0, sinMapeo = 0;
  for (let i = 1; i < flDatos.length; i++) {
    const fila = flDatos[i];
    if (fila[idx['TRANSPORTE']] !== 'SEVILLANITA BRAND') continue;
    const kgDecl = fila[idx['KG TRANSP']];
    if (kgDecl === '' || kgDecl === null) continue;
    const factura = fila[idx['N° FACTURA SEVILLANITA']];
    const fecha = fila[idx['FECHA']];
    const localidad = String(fila[idx['LOCALIDAD']] || '').trim();
    const neto = fila[idx['KG DISTRI']];
    const bultos = fila[idx['CANTIDAD']];
    let calidad, kgPorBulto = '';
    if (neto === '' || neto === null || !bultos) { calidad = 'SIN DATO'; sinDato++; }
    else {
      kgPorBulto = Number(neto) / Number(bultos);
      if (kgPorBulto < 0.05) { calidad = 'NO CONFIABLE'; noConf++; }
      else if (kgPorBulto < 0.30 || kgPorBulto > 60) { calidad = 'DUDOSO'; dudoso++; }
      else calidad = 'CONFIABLE';
    }
    let ramal = mapaRamal[localidad.toUpperCase()];
    if (!ramal) { sinMapeo++; salida.push([factura, fecha, localidad, 'SIN MAPEO', neto, bultos, kgPorBulto, calidad, kgDecl, '', '', '']); continue; }
    if (calidad !== 'CONFIABLE') { salida.push([factura, fecha, localidad, ramal, neto, bultos, kgPorBulto, calidad, kgDecl, '', '', 'REVISAR DATO PROPIO']); continue; }
    const coef = coeficienteParaFecha(fecha instanceof Date ? fecha : new Date(fecha));
    const pesoEsperado = Number(neto) + TARA * Number(bultos);
    const fD = fleteTabla(Number(kgDecl), ramal, coef);
    const fN = fleteTabla(Number(neto), ramal, coef);
    const fE = fleteTabla(pesoEsperado, ramal, coef);
    const techo = (fD !== null && fN !== null) ? fD - fN : '';
    const piso = (fD !== null && fE !== null) ? fD - fE : '';
    let veredicto;
    if (piso === '') veredicto = 'SIN TARIFARIO';
    else if (piso >= UMBRAL) { veredicto = 'EXCESO DE PESO'; exceso++; }
    else if (piso > 0) { veredicto = 'leve (bajo umbral)'; leve++; }
    else { veredicto = 'CONSISTENTE'; consistente++; }
    salida.push([factura, fecha, localidad, ramal, Number(neto), Number(bultos), Math.round(kgPorBulto * 1000) / 1000, calidad, Number(kgDecl),
      typeof techo === 'number' ? Math.round(techo * 100) / 100 : techo, typeof piso === 'number' ? Math.round(piso * 100) / 100 : piso, veredicto]);
  }

  let ap = ss.getSheetByName('ANALISIS_PESO');
  if (!ap) ap = ss.insertSheet('ANALISIS_PESO');
  ap.clear();
  const headers = ['N° FACTURA', 'FECHA', 'LOCALIDAD', 'RAMAL', 'NUESTRO NETO', 'BULTOS', 'KG/BULTO', 'CALIDAD DATO', 'KG DECLARADO', 'TECHO $', 'PISO $', 'VEREDICTO'];
  ap.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  if (salida.length) {
    ap.getRange(2, 1, salida.length, headers.length).setValues(salida);
    ap.getRange(2, 2, salida.length, 1).setNumberFormat('dd/mm/yyyy');
    ap.getRange(2, 10, salida.length, 2).setNumberFormat('$#,##0.00');
  }
  ap.setFrozenRows(1);
  ui.alert('✅ Análisis de peso calculado.\n\nEXCESO DE PESO: ' + exceso + '\nleve (bajo umbral): ' + leve + '\nCONSISTENTE: ' + consistente + '\nDUDOSO: ' + dudoso + '\nNO CONFIABLE: ' + noConf + '\nSIN DATO: ' + sinDato + '\nSIN MAPEO: ' + sinMapeo);
}

// ===== ACCIONES (Eje 3) =====
function calcularAcciones() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const flHoja = ss.getSheetByName('FLETE');
  const atHoja = ss.getSheetByName('ANALISIS_TARIFA');
  const apHoja = ss.getSheetByName('ANALISIS_PESO');
  if (!flHoja || !atHoja || !apHoja) { ui.alert('Falta alguna hoja. Corré primero "Calcular análisis de tarifa" y "Calcular análisis de peso".'); return; }

  const atDatos = atHoja.getDataRange().getValues();
  const mapaTarifa = {};
  for (let i = 1; i < atDatos.length; i++) { const f = atDatos[i][0]; if (!f) continue; mapaTarifa[f] = { veredicto: atDatos[i][11], diferencia: Number(atDatos[i][10]) || 0 }; }

  const apDatos = apHoja.getDataRange().getValues();
  const mapaPeso = {};
  for (let i = 1; i < apDatos.length; i++) { const f = apDatos[i][0]; if (!f) continue; mapaPeso[f] = { veredicto: apDatos[i][11], piso: Number(apDatos[i][10]) || 0 }; }

  const flDatos = flHoja.getDataRange().getValues();
  const flHead = flDatos[0];
  const idx = {};
  ['FECHA', 'LOCALIDAD', 'TRANSPORTE', 'KG TRANSP', 'NETO COBRADO POR EL TRANSPORTE', 'N° FACTURA SEVILLANITA'].forEach(function (h) { idx[h] = flHead.indexOf(h); });
  const faltantes = Object.keys(idx).filter(function (h) { return idx[h] === -1; });
  if (faltantes.length) { ui.alert('Faltan columnas en FLETE: ' + faltantes.join(', ')); return; }

  const salida = [];
  const contadores = {};
  for (let i = 1; i < flDatos.length; i++) {
    const fila = flDatos[i];
    if (fila[idx['TRANSPORTE']] !== 'SEVILLANITA BRAND') continue;
    const factura = fila[idx['N° FACTURA SEVILLANITA']];
    const fecha = fila[idx['FECHA']];
    const localidad = fila[idx['LOCALIDAD']];
    const kgTransp = fila[idx['KG TRANSP']];
    const netoCobrado = fila[idx['NETO COBRADO POR EL TRANSPORTE']];
    let accion, monto = 0, vTar = '', vPes = '';
    if (kgTransp === '' || kgTransp === null || netoCobrado === '' || netoCobrado === null) { accion = 'COMPLETAR CARGA'; }
    else {
      const tar = mapaTarifa[factura]; const pes = mapaPeso[factura];
      vTar = tar ? tar.veredicto : ''; vPes = pes ? pes.veredicto : '';
      if (vTar === 'SOBRECOBRO') { accion = 'RECLAMAR TARIFA'; monto = tar.diferencia; }
      else if (vTar === 'SIN MAPEO' || vPes === 'SIN MAPEO') { accion = 'MAPEAR DESTINO'; }
      else if (vPes === 'EXCESO DE PESO') { accion = 'RECLAMAR PESO'; monto = pes.piso; }
      else if (vPes === 'REVISAR DATO PROPIO') { accion = 'REVISAR DATO PROPIO'; }
      else { accion = 'PAGAR'; }
    }
    contadores[accion] = (contadores[accion] || 0) + 1;
    salida.push([factura, fecha, localidad, vTar, vPes, accion, Math.round(monto * 100) / 100]);
  }

  salida.sort(function (a, b) { return b[6] - a[6]; });
  const conRanking = salida.map(function (fila, i) { return fila.concat([fila[6] > 0 ? i + 1 : '']); });

  let ac = ss.getSheetByName('ACCIONES');
  if (!ac) ac = ss.insertSheet('ACCIONES');
  ac.clear();
  const headers = ['N° FACTURA', 'FECHA', 'LOCALIDAD', 'VEREDICTO TARIFA', 'VEREDICTO PESO', 'ACCIÓN', 'MONTO EN JUEGO', 'RANKING'];
  ac.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  if (conRanking.length) {
    ac.getRange(2, 1, conRanking.length, headers.length).setValues(conRanking);
    ac.getRange(2, 2, conRanking.length, 1).setNumberFormat('dd/mm/yyyy');
    ac.getRange(2, 7, conRanking.length, 1).setNumberFormat('$#,##0.00');
  }
  ac.setFrozenRows(1);

  const totalReclamar = salida.reduce(function (s, f) { return s + (f[5].indexOf('RECLAMAR') === 0 ? f[6] : 0); }, 0);
  ui.alert('✅ Acciones calculadas.\n\nCOMPLETAR CARGA: ' + (contadores['COMPLETAR CARGA'] || 0) + '\nRECLAMAR TARIFA: ' + (contadores['RECLAMAR TARIFA'] || 0) +
    '\nRECLAMAR PESO: ' + (contadores['RECLAMAR PESO'] || 0) + '\nMAPEAR DESTINO: ' + (contadores['MAPEAR DESTINO'] || 0) +
    '\nREVISAR DATO PROPIO: ' + (contadores['REVISAR DATO PROPIO'] || 0) + '\nPAGAR: ' + (contadores['PAGAR'] || 0) +
    '\n\n💰 Total a reclamar: $' + totalReclamar.toLocaleString('es-AR', {minimumFractionDigits: 2}));
}

// ===== ANALISIS FINANCIERO (Sevillanita detalle + Sebastián básico + Global) =====
function calcularAnalisisFinanciero() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const flHoja = ss.getSheetByName('FLETE');
  if (!flHoja) { ui.alert('No encontré la hoja FLETE.'); return; }

  const UMBRAL_ALERTA = 0.5;
  const flDatos = flHoja.getDataRange().getValues();
  const flHead = flDatos[0];
  const idx = {};
  ['CLIENTE', 'TRANSPORTE', 'COSTO MERCADERIA', 'NETO FACTURADO AL CLIENTE', 'NETO COBRADO POR EL TRANSPORTE'].forEach(function (h) { idx[h] = flHead.indexOf(h); });
  const faltantes = Object.keys(idx).filter(function (h) { return idx[h] === -1; });
  if (faltantes.length) { ui.alert('Faltan columnas en FLETE: ' + faltantes.join(', ')); return; }

  const filasSev = [], filasSeb = [];
  for (let i = 1; i < flDatos.length; i++) {
    const fila = flDatos[i];
    const transp = fila[idx['TRANSPORTE']];
    const flete = fila[idx['NETO COBRADO POR EL TRANSPORTE']];
    if (flete === '' || flete === null || Number(flete) <= 0) continue;
    const registro = { cliente: fila[idx['CLIENTE']], venta: Number(fila[idx['NETO FACTURADO AL CLIENTE']]) || 0, cmv: Number(fila[idx['COSTO MERCADERIA']]) || 0, flete: Number(flete) };
    if (transp === 'SEVILLANITA BRAND') filasSev.push(registro);
    else if (transp === 'SEBASTIAN BRANDMARK') filasSeb.push(registro);
  }

  const porClienteSev = {};
  filasSev.forEach(function (f) {
    if (!f.cliente) return;
    if (!porClienteSev[f.cliente]) porClienteSev[f.cliente] = { envios: 0, venta: 0, cmv: 0, flete: 0 };
    const d = porClienteSev[f.cliente]; d.envios++; d.venta += f.venta; d.cmv += f.cmv; d.flete += f.flete;
  });
  const salidaSev = [];
  let alertasSev = 0, margenRotoSev = 0;
  for (const cliente in porClienteSev) {
    const d = porClienteSev[cliente];
    const margen = d.venta - d.cmv;
    const fv = d.venta > 0 ? d.flete / d.venta : '';
    const fm = margen > 0 ? d.flete / margen : '';
    let alerta = '';
    if (margen <= 0) { alerta = 'REVISAR COSTO MERCADERÍA (margen ≤ 0)'; margenRotoSev++; }
    else if (fm !== '' && fm > UMBRAL_ALERTA) { alerta = 'FLETE > 50% DEL MARGEN'; alertasSev++; }
    salidaSev.push([cliente, d.envios, r2(d.venta), r2(d.cmv), r2(margen), r2(d.flete), fv !== '' ? r4(fv) : '', fm !== '' ? r4(fm) : '', alerta]);
  }
  salidaSev.sort(function (a, b) { return b[5] - a[5]; });

  let hSev = ss.getSheetByName('ANALISIS_FINANCIERO_SEVILLANITA');
  if (!hSev) hSev = ss.insertSheet('ANALISIS_FINANCIERO_SEVILLANITA');
  hSev.clear();
  const encSev = ['CLIENTE', 'ENVÍOS', 'VENTA', 'CMV', 'MARGEN BRUTO', 'FLETE', 'FLETE/VENTA %', 'FLETE/MARGEN %', 'ALERTA'];
  hSev.getRange(1, 1, 1, encSev.length).setValues([encSev]).setFontWeight('bold');
  if (salidaSev.length) {
    hSev.getRange(2, 1, salidaSev.length, encSev.length).setValues(salidaSev);
    hSev.getRange(2, 3, salidaSev.length, 4).setNumberFormat('$#,##0.00');
    hSev.getRange(2, 7, salidaSev.length, 2).setNumberFormat('0.00%');
  }
  hSev.setFrozenRows(1);

  const totSeb = filasSeb.reduce(function (a, f) { a.envios++; a.venta += f.venta; a.cmv += f.cmv; a.flete += f.flete; return a; }, { envios: 0, venta: 0, cmv: 0, flete: 0 });
  const margenSeb = totSeb.venta - totSeb.cmv;

  let hSeb = ss.getSheetByName('ANALISIS_FINANCIERO_SEBASTIAN');
  if (!hSeb) hSeb = ss.insertSheet('ANALISIS_FINANCIERO_SEBASTIAN');
  hSeb.clear();
  hSeb.getRange(1, 1, 1, 2).setValues([['KPI', 'VALOR']]).setFontWeight('bold');
  const filasKpiSeb = [
    ['Envíos con flete cargado', totSeb.envios], ['Venta total', r2(totSeb.venta)], ['CMV total', r2(totSeb.cmv)],
    ['Margen bruto', r2(margenSeb)], ['Flete total', r2(totSeb.flete)],
    ['Flete / Venta %', totSeb.venta ? r4(totSeb.flete / totSeb.venta) : ''], ['Flete / Margen %', margenSeb > 0 ? r4(totSeb.flete / margenSeb) : '']
  ];
  hSeb.getRange(2, 1, filasKpiSeb.length, 2).setValues(filasKpiSeb);
  hSeb.getRange(3, 2, 3, 1).setNumberFormat('$#,##0.00');
  hSeb.getRange(6, 2, 2, 1).setNumberFormat('0.00%');
  hSeb.setFrozenRows(1);

  const totGlob = { envios: totSeb.envios, venta: totSeb.venta, cmv: totSeb.cmv, flete: totSeb.flete };
  filasSev.forEach(function (f) { totGlob.envios++; totGlob.venta += f.venta; totGlob.cmv += f.cmv; totGlob.flete += f.flete; });
  const margenGlob = totGlob.venta - totGlob.cmv;
  const fleteSevTotal = filasSev.reduce(function (s, f) { return s + f.flete; }, 0);

  let hGlob = ss.getSheetByName('ANALISIS_FINANCIERO_GLOBAL');
  if (!hGlob) hGlob = ss.insertSheet('ANALISIS_FINANCIERO_GLOBAL');
  hGlob.clear();
  hGlob.getRange(1, 1, 1, 2).setValues([['KPI', 'VALOR']]).setFontWeight('bold');
  const filasKpiGlob = [
    ['Envíos con flete cargado (los dos transportes)', totGlob.envios], ['Venta total', r2(totGlob.venta)], ['CMV total', r2(totGlob.cmv)],
    ['Margen bruto', r2(margenGlob)], ['Flete total', r2(totGlob.flete)],
    ['Flete / Venta %', totGlob.venta ? r4(totGlob.flete / totGlob.venta) : ''], ['Flete / Margen %', margenGlob > 0 ? r4(totGlob.flete / margenGlob) : ''],
    ['Flete Sevillanita ($)', r2(fleteSevTotal)], ['Flete Sevillanita (% del total)', totGlob.flete ? r4(fleteSevTotal / totGlob.flete) : ''],
    ['Flete Sebastián ($)', r2(totSeb.flete)], ['Flete Sebastián (% del total)', totGlob.flete ? r4(totSeb.flete / totGlob.flete) : '']
  ];
  hGlob.getRange(2, 1, filasKpiGlob.length, 2).setValues(filasKpiGlob);
  [3, 4, 5, 9, 11].forEach(function (r) { hGlob.getRange(r, 2).setNumberFormat('$#,##0.00'); });
  [6, 7, 10, 12].forEach(function (r) { hGlob.getRange(r, 2).setNumberFormat('0.00%'); });
  hGlob.setFrozenRows(1);

  ui.alert('✅ Análisis financiero calculado (3 hojas).\n\n— SEVILLANITA — clientes: ' + salidaSev.length + ' | alertas flete>50%margen: ' + alertasSev + ' | margen≤0: ' + margenRotoSev +
    '\n— SEBASTIÁN — envíos: ' + totSeb.envios + ' | flete: $' + totSeb.flete.toLocaleString('es-AR', {minimumFractionDigits:2}) +
    '\n— GLOBAL — flete total: $' + totGlob.flete.toLocaleString('es-AR', {minimumFractionDigits:2}) + ' (Sevillanita ' + (fleteSevTotal/totGlob.flete*100).toFixed(1) + '% / Sebastián ' + (totSeb.flete/totGlob.flete*100).toFixed(1) + '%)');
}

// ===== MINIMO DE COMPRA =====
function calcularMinimoDeCompra() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const parHoja = ss.getSheetByName('PARAMETROS');
  const tkHoja = ss.getSheetByName('TARIFARIO_KG');
  if (!parHoja || !tkHoja) { ui.alert('Falta PARAMETROS o TARIFARIO_KG.'); return; }

  const UMBRAL_FLETE_VENTA = 0.15;
  const MINIMO_BASE = Number(parHoja.getRange('B2').getValue());

  const MESES = { ENE:0, FEB:1, MAR:2, ABR:3, MAY:4, JUN:5, JUL:6, AGO:7, SEP:8, OCT:9, NOV:10, DIC:11 };
  const coefFilas = [];
  for (let r = 2; r < 50; r++) {
    const celda = parHoja.getRange(r, 8).getValue();
    if (celda === '' || celda === null) break;
    const coef = Number(parHoja.getRange(r, 10).getValue());
    let fechaMes = null;
    if (celda instanceof Date) fechaMes = new Date(celda.getFullYear(), celda.getMonth(), 1);
    else {
      const m = String(celda).match(/^([A-ZÑ]{3})[.\-\s].*?(\d{4})/i);
      if (m && (m[1].toUpperCase() in MESES)) fechaMes = new Date(Number(m[2]), MESES[m[1].toUpperCase()], 1);
    }
    if (fechaMes) coefFilas.push({ fecha: fechaMes, coef: coef });
  }
  coefFilas.sort(function (a, b) { return a.fecha - b.fecha; });
  const coefVigente = coefFilas.length ? coefFilas[coefFilas.length - 1].coef : 1;
  const minimoVigente = MINIMO_BASE * coefVigente;
  const ventaMinima = minimoVigente / UMBRAL_FLETE_VENTA;

  const tkDatos = tkHoja.getDataRange().getValues();
  const ramales = tkDatos[0].slice(1);
  const salida = [];
  ramales.forEach(function (ramal, ci) {
    let breakpoint = null;
    for (let i = 1; i < tkDatos.length; i++) {
      const tabla = Number(tkDatos[i][ci + 1]) * coefVigente;
      if (tabla > minimoVigente) { breakpoint = Number(tkDatos[i][0]); break; }
    }
    salida.push([ramal, r2(minimoVigente), breakpoint, r2(ventaMinima)]);
  });

  let hoja = ss.getSheetByName('MINIMO_DE_COMPRA');
  if (!hoja) hoja = ss.insertSheet('MINIMO_DE_COMPRA');
  hoja.clear();
  hoja.getRange(1, 1, 1, 4).setValues([['RAMAL', 'MÍNIMO DE FLETE VIGENTE', 'HASTA CUÁNTOS KG DA LO MISMO', 'VENTA MÍNIMA RECOMENDADA (flete ≤ 15% venta)']]).setFontWeight('bold');
  hoja.getRange(2, 1, salida.length, 4).setValues(salida);
  hoja.getRange(2, 2, salida.length, 1).setNumberFormat('$#,##0.00');
  hoja.getRange(2, 4, salida.length, 1).setNumberFormat('$#,##0.00');
  hoja.getRange(1, 1, 1, 4).setWrap(true);
  hoja.setFrozenRows(1);
  hoja.setColumnWidths(1, 4, 160);
  hoja.getRange(salida.length + 3, 1).setValue('Nota: el mínimo de flete es el mismo para todos los ramales. Lo que cambia es hasta qué peso ese mínimo no se nota.').setFontStyle('italic');

  ui.alert('✅ Mínimo de compra calculado.\n\nMínimo de flete vigente: $' + minimoVigente.toLocaleString('es-AR', {minimumFractionDigits: 2, maximumFractionDigits: 2}) +
    '\nVenta mínima recomendada (15%): $' + ventaMinima.toLocaleString('es-AR', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
}

// ===== SUBIR AUDITORÍA A SUPABASE =====
function subirAuditoriaASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const ac = ss.getSheetByName('ACCIONES');
  if (!ac) { ui.alert('No encontré la hoja ACCIONES. Corré "Calcular acciones" primero.'); return; }

  const datos = ac.getDataRange().getValues();
  const filas = [];
  for (let i = 1; i < datos.length; i++) {
    const f = datos[i];
    if (!f[0]) continue;
    filas.push({
      factura: String(f[0]), fecha: (f[1] instanceof Date) ? Utilities.formatDate(f[1], 'GMT-3', 'yyyy-MM-dd') : null,
      localidad: f[2] || null, veredicto_tarifa: f[3] || null, veredicto_peso: f[4] || null, accion: f[5] || null, monto_en_juego: Number(f[6]) || 0
    });
  }
  if (filas.length === 0) { ui.alert('No hay filas para subir.'); return; }

  const url = PropertiesService.getScriptProperties().getProperty('SUPABASE_URL');
  const serviceKey = PropertiesService.getScriptProperties().getProperty('SUPABASE_SERVICE_KEY');
  if (!serviceKey) { ui.alert('Falta la Script Property SUPABASE_SERVICE_KEY.'); return; }

  const endpoint = url + '/rest/v1/auditoria_sevillanita?on_conflict=factura';
  const LOTE = 500;
  let subidas = 0;
  for (let i = 0; i < filas.length; i += LOTE) {
    const lote = filas.slice(i, i + LOTE);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: 'post', contentType: 'application/json',
      headers: { apikey: serviceKey, Authorization: 'Bearer ' + serviceKey, 'Content-Profile': 'bronze', Prefer: 'resolution=merge-duplicates,return=minimal' },
      payload: JSON.stringify(lote), muteHttpExceptions: true
    });
    if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) subidas += lote.length;
    else {
      Logger.log('Error: ' + resp.getContentText().substring(0, 300));
      ui.alert('Error al subir (revisá el log). Subidas ' + subidas + ' de ' + filas.length + ' antes de fallar.');
      return;
    }
  }
  ui.alert('✅ Subidas ' + subidas + ' filas a bronze.auditoria_sevillanita.');
}

// ===== HELPERS =====
function r2(n) { return Math.round(n * 100) / 100; }
function r4(n) { return Math.round(n * 10000) / 10000; }