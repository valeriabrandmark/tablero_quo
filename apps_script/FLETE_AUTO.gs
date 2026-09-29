// ============================================================================
//  FLETE_AUTO.gs — planilla "2026 - LOGISTICA BRANDMARK - UNIBRAND"
// ============================================================================
//
//  COPIA DE RESPALDO. El original vive adentro del Google Sheet (Extensiones ->
//  Apps Script) y ES EL UNICO LUGAR DONDE CORRE. Esto es una copia para poder
//  verlo en un diff y recuperarlo si alguien lo borra. Ver apps_script/README.md.
//
//  Trae los datos de Supabase a la hoja FLETE y sube lo que se carga a mano.
// ============================================================================

// ===== CONFIG =====
function getProp(n) {
  return PropertiesService.getScriptProperties().getProperty(n);
}

const COLUMNAS = [
  { header: "FECHA", tipo: "auto", campo: "fecha" },
  { header: "CLIENTE", tipo: "auto", campo: "cliente" },
  { header: "CODIGO DE CLIENTE", tipo: "auto", campo: "cliente_codigo" },
  { header: "N° DE FACTURA DEL CLIENTE", tipo: "auto", campo: "comprobantes" },
  { header: "FLETE", tipo: "manual" },
  { header: "N°PEDIDO", tipo: "auto", campo: "pedidos" },
  { header: "LOCALIDAD", tipo: "auto", campo: "localidad" },
  { header: "PROVINCIA", tipo: "auto", campo: "provincia" },
  { header: "TRANSPORTE", tipo: "auto", campo: "transporte" },
  { header: "TIPO", tipo: "manual" },
  { header: "CANTIDAD", tipo: "auto", campo: "bultos" },
  { header: "VOLUMETRIA DISTRI", tipo: "auto", campo: "volumen" },
  { header: "KG DISTRI", tipo: "auto", campo: "kg" },
  { header: "KG TRANSP", tipo: "manual" },
  { header: "VOLUMEN TRANSPORTE", tipo: "manual" },
  // --- Columnas nuevas ---
  // KG DIGIP (ACTUAL): el KG crudo que informa Digip ahora mismo, sin
  // corrección. La dejamos visible para poder comparar contra KG DISTRI y
  // saber qué filas hace falta corregir.
  { header: "KG DIGIP (ACTUAL)", tipo: "auto", campo: "kg_digip_actual" },
  // COSTO MERCADERIA: costo total de la mercadería involucrada en el envío,
  // el mismo costo_unitario que usa gold.fact_ventas (cruzado por comprobante).
  { header: "COSTO MERCADERIA", tipo: "auto", campo: "costo_mercaderia" },
  { header: "QUO", tipo: "auto", campo: "quo" },
  { header: "QUO PRUEBA", tipo: "auto", campo: "quo_prueba" },
  { header: "NOA", tipo: "auto", campo: "noa" },
  { header: "NOA PRUEBA", tipo: "auto", campo: "noa_prueba" },
  { header: "NETO FACTURADO AL CLIENTE", tipo: "auto", campo: "monto_total" },
  { header: "NETO COBRADO POR EL TRANSPORTE", tipo: "manual" },
  { header: "MARGEN", tipo: "formula" },
  { header: "N° FACTURA SEVILLANITA", tipo: "manual" }
];

const NOMBRE_HOJA = "FLETE";

// ===== TRAER DATOS DE SUPABASE =====
function traerReporteLogistica() {
  const url = getProp("SUPABASE_URL");
  const key = getProp("SUPABASE_KEY");
  let todos = [];
  let desde = 0;
  const bloque = 1000;

  while (true) {
    const endpoint = url + "/rest/v1/reporte_logistica?select=*"
      + "&order=fecha.desc&limit=" + bloque + "&offset=" + desde;
    const resp = UrlFetchApp.fetch(endpoint, {
      headers: { "apikey": key, "Authorization": "Bearer " + key },
      muteHttpExceptions: true
    });
    if (resp.getResponseCode() !== 200) {
      Logger.log("Error: " + resp.getContentText().substring(0, 300));
      break;
    }
    const datos = JSON.parse(resp.getContentText());
    todos = todos.concat(datos);
    if (datos.length < bloque) break;
    desde += bloque;
  }
  Logger.log("Filas traídas: " + todos.length);
  return todos;
}

// ===== ACTUALIZAR HOJA (MERGE BLINDADO por clave_fila) =====
function actualizarReporteLogistica() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let hoja = ss.getSheetByName(NOMBRE_HOJA);
  if (!hoja) hoja = ss.insertSheet(NOMBRE_HOJA);

  // 1) Traer datos PRIMERO. Si falla, no tocamos la hoja.
  const datos = traerReporteLogistica();
  if (!datos || datos.length === 0) {
    SpreadsheetApp.getUi().alert("No se trajeron datos de Supabase.\nLa hoja NO se modificó (tus fletes están a salvo).");
    return;
  }

  // 3 columnas ocultas al final: _preparacion_id, _kg_digip_actual y _clave_fila (en ese orden).
  const ultCol = COLUMNAS.length + 3;
  const colTotal = COLUMNAS.findIndex(c => c.header === "NETO FACTURADO AL CLIENTE");
  const colMargen = COLUMNAS.findIndex(c => c.header === "MARGEN");
  const colCobrado = COLUMNAS.findIndex(c => c.header === "NETO COBRADO POR EL TRANSPORTE");
  const colFleteIdx = COLUMNAS.findIndex(c => c.header === "FLETE");

  // 2) Leer manuales actuales, indexados por clave_fila (última columna oculta).
  // IMPORTANTE: se busca cada columna por SU NOMBRE en el encabezado que hoy
  // tiene la hoja (no por la posición que tiene en el array COLUMNAS de este
  // script) -- si no, el día que se agrega/reordena una columna, esto lee la
  // celda equivocada y pisa datos manuales con basura. Ya pasó una vez.
  const manualesPrevios = {};
  let cantFletesPrevios = 0;
  if (hoja.getLastRow() > 1) {
    const headerActual = hoja.getRange(1, 1, 1, hoja.getLastColumn()).getValues()[0];
    const idxClaveActual = headerActual.indexOf('_clave_fila');
    const columnasManualesConIndice = COLUMNAS
      .filter(function(c) { return c.tipo === 'manual'; })
      .map(function(c) { return { header: c.header, idx: headerActual.indexOf(c.header) }; });

    if (idxClaveActual === -1) {
      Logger.log('No se encontró _clave_fila en el encabezado actual de la hoja; no se preservan manuales (¿primera corrida con este esquema?).');
    } else {
      const numColsActual = hoja.getLastColumn();
      const rango = hoja.getRange(2, 1, hoja.getLastRow() - 1, numColsActual).getValues();
      rango.forEach(function(fila) {
        const clave = fila[idxClaveActual];
        if (!clave) return;
        const manuales = {};
        columnasManualesConIndice.forEach(function(c) {
          if (c.idx === -1) return; // esta columna manual no existía todavía en la hoja vieja
          manuales[c.header] = fila[c.idx];
        });
        manualesPrevios[clave] = manuales;
        if (manuales["FLETE"] !== "" && manuales["FLETE"] != null) cantFletesPrevios++;
      });
    }
  }

  // 3) BACKUP antes de tocar la hoja
  guardarBackupFletes(manualesPrevios, cantFletesPrevios);

  // 4) Armar filas nuevas EN MEMORIA
  const encabezados = COLUMNAS.map(c => c.header);
  encabezados.push("_preparacion_id");
  encabezados.push("_volumen_digip_actual");
  encabezados.push("_clave_fila");
  const filas = [encabezados];

  datos.forEach(function(d) {
    const transp = (d.transporte || "").toUpperCase();
    // Envios que NO tienen costo de transporte: consumo interno y retiro de
    // deposito (lo lleva el cliente). Sin un 0 explicito quedan vacios, y
    // prorratear_flete.py toma "vacio" como "todavia no cargado" y les estima
    // un 5 % de la venta. Ese flete no existe.
    // El indexOf("DEP") en vez de "DEPOSITO" es para que tambien enganche si
    // alguien lo escribe con tilde.
    const sinCostoDeTransporte =
      transp.indexOf("CONSUMO INTERNO") >= 0 ||
      (transp.indexOf("RETIRO") >= 0 && transp.indexOf("DEP") >= 0);
    const prev = manualesPrevios[d.clave_fila];

    const fila = COLUMNAS.map(function(col) {
      if (col.tipo === "auto") {
        let v = d[col.campo];
        if (v === null || v === undefined) return "";
        // Código de cliente: rellenar con ceros a la izquierda hasta 6 dígitos
        if (col.header === "CODIGO DE CLIENTE") {
          v = String(v).trim();
          if (v !== "" && /^\d+$/.test(v)) {
            v = v.padStart(6, "0");
          }
        }
        // DIGIP informa el volumen en mm3 (milimetros cubicos); lo pasamos a m3.
        if (col.header === "VOLUMETRIA DISTRI" && v !== "" && !isNaN(v)) {
          v = Number(v) / 1000000000;
        }
        return v;
      }
      if (col.tipo === "manual") {
        const valorPrevio = (prev && prev[col.header] !== undefined && prev[col.header] !== null) ? prev[col.header] : "";
        // Solo NETO COBRADO en 0, y solo si esta vacio (la columna FLETE queda
        // vacia, y lo que ya se cargo a mano no se pisa nunca).
        const autocompletaCero = (col.header === "NETO COBRADO POR EL TRANSPORTE");
        if (autocompletaCero && sinCostoDeTransporte && (valorPrevio === "" || valorPrevio == null)) {
          return 0;
        }
        return valorPrevio;
      }
      return ""; // formula
    });
    fila.push(d.preparacion_id || "");
    fila.push((d.volumen_digip_actual !== null && d.volumen_digip_actual !== undefined && !isNaN(d.volumen_digip_actual))
      ? Number(d.volumen_digip_actual) / 1000000000
      : "");
    fila.push(d.clave_fila);
    filas.push(fila);
  });

  // 5) VALIDACIÓN anti-pérdida — se fija en TODAS las columnas manuales, no
  // solo en FLETE (antes solo miraba FLETE, y no habría detectado un
  // corrimiento que afecte a otra columna manual, como ya pasó una vez).
  const columnasManualesTodas = COLUMNAS.filter(function(c) { return c.tipo === 'manual'; });
  const conteoAntesPorColumna = {};
  const conteoDespuesPorColumna = {};
  columnasManualesTodas.forEach(function(c) { conteoAntesPorColumna[c.header] = 0; conteoDespuesPorColumna[c.header] = 0; });

  for (const clave in manualesPrevios) {
    const m = manualesPrevios[clave];
    columnasManualesTodas.forEach(function(c) {
      if (m[c.header] !== "" && m[c.header] != null) conteoAntesPorColumna[c.header]++;
    });
  }
  for (let i = 1; i < filas.length; i++) {
    columnasManualesTodas.forEach(function(c) {
      const idx = COLUMNAS.findIndex(function(col) { return col.header === c.header; });
      if (filas[i][idx] !== "" && filas[i][idx] != null) conteoDespuesPorColumna[c.header]++;
    });
  }

  const columnasConPerdida = columnasManualesTodas
    .map(function(c) { return { header: c.header, antes: conteoAntesPorColumna[c.header], despues: conteoDespuesPorColumna[c.header] }; })
    .filter(function(c) { return c.despues < c.antes; });

  const cantFletesNuevos = conteoDespuesPorColumna["FLETE"] || 0;

  if (columnasConPerdida.length > 0) {
    const detalle = columnasConPerdida.map(function(c) { return `  - ${c.header}: ${c.antes} → ${c.despues}`; }).join("\n");
    const ui = SpreadsheetApp.getUi();
    const seguir = ui.alert("⚠️ ATENCIÓN — posible pérdida de datos manuales",
      "Estas columnas tendrían MENOS valores cargados que antes:\n\n" + detalle +
      "\n\nLos manuales previos están respaldados en 'Fletes_backup'.\n\n¿Continuar igual?",
      ui.ButtonSet.YES_NO);
    if (seguir !== ui.Button.YES) {
      ui.alert("Actualización cancelada. La hoja NO se modificó.");
      return;
    }
  }

  // 6) Formato TEXTO en el código de cliente ANTES de escribir (clave para no perder ceros)
  const idxCodCli = COLUMNAS.findIndex(c => c.header === "CODIGO DE CLIENTE");
  const filasActuales = hoja.getLastRow();
  if (idxCodCli >= 0 && filas.length > 1) {
    hoja.getRange(2, idxCodCli + 1, filas.length - 1, 1).setNumberFormat("@");
  }

  // 7) Escribir sin clear() destructivo
  hoja.getRange(1, 1, filas.length, encabezados.length).setValues(filas);
  if (filasActuales > filas.length) {
    hoja.getRange(filas.length + 1, 1, filasActuales - filas.length, ultCol).clearContent();
  }

  // 8) Fórmula MARGEN: NETO COBRADO / NETO FACTURADO * 100
  const letraCobrado = columnaALetra(colCobrado + 1);
  const letraTotal = columnaALetra(colTotal + 1);
  const colMargenHoja = colMargen + 1;
  for (let r = 2; r <= filas.length; r++) {
    const formula = "=IF(N(" + letraTotal + r + ")=0;\"\";IFERROR("
                    + letraCobrado + r + "/" + letraTotal + r + "*100;\"\"))";
    hoja.getRange(r, colMargenHoja).setFormula(formula);
  }

  // 9) Formato moneda
  const columnasMoneda = ["FLETE","QUO","QUO PRUEBA","NOA","NOA PRUEBA",
    "NETO FACTURADO AL CLIENTE","NETO COBRADO POR EL TRANSPORTE","COSTO MERCADERIA"];
  columnasMoneda.forEach(function(header) {
    const idx = COLUMNAS.findIndex(c => c.header === header);
    if (idx >= 0 && filas.length > 1) {
      hoja.getRange(2, idx + 1, filas.length - 1, 1).setNumberFormat("$#,##0.00");
    }
  });

  // Formato numérico plano (NO moneda) para las columnas de volumen/peso —
  // sin esto, a veces heredan el formato de una columna vieja que ocupaba
  // ese mismo lugar antes.
  const columnasNumeroPlano = ["VOLUMETRIA DISTRI", "KG DISTRI", "KG DIGIP (ACTUAL)"];
  columnasNumeroPlano.forEach(function(header) {
    const idx = COLUMNAS.findIndex(c => c.header === header);
    if (idx >= 0 && filas.length > 1) {
      hoja.getRange(2, idx + 1, filas.length - 1, 1).setNumberFormat("0.000");
    }
  });

  // 10) Ocultar las 3 columnas clave (_preparacion_id, _kg_digip_actual, _clave_fila) y congelar encabezado
  hoja.hideColumns(encabezados.length - 2, 3);
  hoja.setFrozenRows(1);

  const marca = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy HH:mm:ss");
  Logger.log("Reporte actualizado: " + (filas.length - 1) + " filas - " + marca);
  SpreadsheetApp.getUi().alert("✅ Reporte actualizado: " + (filas.length - 1) + " filas.\nFletes preservados: " + cantFletesNuevos);
}

// ===== GUARDAR CORRECCIONES DE KG/VOLUMEN (una sola pasada, protegidas) =====
// Compara lo que VOS escribiste en KG DISTRI / VOLUMETRIA DISTRI contra el
// valor CRUDO que informa Digip ahora mismo: para kg, la columna visible
// "KG DIGIP (ACTUAL)" (para que puedas ver qué filas cambiar); para volumen,
// una columna oculta _volumen_digip_actual (no se muestra porque hoy es
// idéntica a VOLUMETRIA DISTRI en todas las filas, dado que todavía no se
// guardó ninguna corrección de volumen). Donde sean distintas, guarda tu
// valor como corrección protegida en Supabase
// (bronze.digip_preparaciones.kg_preparacion_manual / volumen_preparacion_manual):
// de ahí en mas, la vista SIEMPRE usa tu valor, sin importar lo que Digip
// vuelva a informar. Donde sean iguales, no hace nada (no "congela" filas
// que ya estaban bien).
function guardarCorreccionesKgVolumen() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName(NOMBRE_HOJA);
  const ui = SpreadsheetApp.getUi();

  const datos = hoja.getDataRange().getValues();
  const headers = datos[0];
  const idxPrepId = headers.indexOf('_preparacion_id');
  const idxKgDistri = headers.indexOf('KG DISTRI');
  const idxKgDigip = headers.indexOf('KG DIGIP (ACTUAL)'); // visible
  const idxVolDistri = headers.indexOf('VOLUMETRIA DISTRI');
  const idxVolDigip = headers.indexOf('_volumen_digip_actual'); // oculta

  if ([idxPrepId, idxKgDistri, idxKgDigip, idxVolDistri, idxVolDigip].includes(-1)) {
    ui.alert('Faltan columnas esperadas. Corré primero "Actualizar reporte" con el script actualizado.');
    return;
  }

  const TOLERANCIA = 0.001; // diferencias menores a esto se ignoran (redondeo)
  const correcciones = [];

  for (let i = 1; i < datos.length; i++) {
    const fila = datos[i];
    const prepId = fila[idxPrepId];
    if (!prepId) continue;

    const kgDistri = fila[idxKgDistri];
    const kgDigip = fila[idxKgDigip];
    const volDistri = fila[idxVolDistri];
    const volDigip = fila[idxVolDigip];

    const corr = { preparacion_id: String(prepId) };
    let hayCambio = false;

    if (typeof kgDistri === 'number' && typeof kgDigip === 'number' &&
        Math.abs(kgDistri - kgDigip) > TOLERANCIA) {
      corr.kg_preparacion_manual = kgDistri;
      hayCambio = true;
    }
    if (typeof volDistri === 'number' && typeof volDigip === 'number' &&
        Math.abs(volDistri - volDigip) > TOLERANCIA) {
      corr.volumen_preparacion_manual = volDistri * 1000000000; // de vuelta a mm3, misma unidad que Digip
      hayCambio = true;
    }

    if (hayCambio) correcciones.push(corr);
  }

  if (correcciones.length === 0) {
    ui.alert('No encontré diferencias entre KG DISTRI/VOLUMETRIA DISTRI y los valores "(ACTUAL)" de Digip. Nada para guardar.');
    return;
  }

  const confirmar = ui.alert(
    'Confirmar correcciones',
    'Encontré ' + correcciones.length + ' fila(s) con un valor distinto al que informa Digip.\n' +
    'Se van a guardar como corrección PROTEGIDA (Digip no las va a volver a pisar nunca).\n\n¿Continuar?',
    ui.ButtonSet.YES_NO
  );
  if (confirmar !== ui.Button.YES) return;

  const url = getProp("SUPABASE_URL");
  const serviceKey = getProp("SUPABASE_SERVICE_KEY");
  if (!serviceKey) {
    ui.alert("Falta la Script Property SUPABASE_SERVICE_KEY.");
    return;
  }

  let guardadas = 0;
  let errores = 0;

  correcciones.forEach(function(c) {
    const payload = {};
    if (c.kg_preparacion_manual !== undefined) payload.kg_preparacion_manual = c.kg_preparacion_manual;
    if (c.volumen_preparacion_manual !== undefined) payload.volumen_preparacion_manual = c.volumen_preparacion_manual;

    const endpoint = url + "/rest/v1/digip_preparaciones?preparacion_id=eq." + encodeURIComponent(c.preparacion_id);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: "patch",
      contentType: "application/json",
      headers: {
        "apikey": serviceKey,
        "Authorization": "Bearer " + serviceKey,
        "Content-Profile": "bronze",
        "Prefer": "return=minimal"
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) {
      guardadas++;
    } else {
      errores++;
      Logger.log("Error preparacion " + c.preparacion_id + ": " + resp.getContentText().substring(0, 300));
    }
  });

  ui.alert(
    "✅ Listo.\nCorrecciones guardadas: " + guardadas + "\n" +
    (errores > 0 ? "⚠️ Errores: " + errores + " (revisá Ver → Registros)\n" : "") +
    "\nCorré 'Actualizar reporte' para confirmar que quedaron reflejadas."
  );
}

// ===== BACKUP de fletes (usado por actualizarReporteLogistica) =====
function guardarBackupFletes(manualesPrevios, cant) {
  if (cant === 0) return;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let bk = ss.getSheetByName("Fletes_backup");
  if (!bk) bk = ss.insertSheet("Fletes_backup");
  bk.clear();
  const marca = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy HH:mm:ss");
  const filas = [["_clave_fila","FLETE","TIPO","VOLUMETRIA DISTRI","KG DISTRI","KG TRANSP",
                  "NETO COBRADO POR EL TRANSPORTE","N° FACTURA SEVILLANITA","backup_fecha"]];
  for (const clave in manualesPrevios) {
    const m = manualesPrevios[clave];
    filas.push([clave, m["FLETE"]||"", m["TIPO"]||"", m["VOLUMETRIA DISTRI"]||"",
                m["KG DISTRI"]||"", m["KG TRANSP"]||"", m["NETO COBRADO POR EL TRANSPORTE"]||"",
                m["N° FACTURA SEVILLANITA"]||"", marca]);
  }
  bk.getRange(1, 1, filas.length, filas[0].length).setValues(filas);
}

// ===== BACKUP dedicado para el import histórico (copia toda la hoja FLETE tal cual está) =====
function backupAntesDeImport() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName(NOMBRE_HOJA);
  const marca = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyyMMdd_HHmmss");
  const nombreBackup = "FLETE_backup_import_" + marca;
  hoja.copyTo(ss).setName(nombreBackup);
  Logger.log("Backup de FLETE creado: " + nombreBackup);
}

// ===== IMPORTAR FLETES HISTÓRICOS (desde "flete 2026") =====
function importarFletesHistoricos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hojaVieja = ss.getSheetByName('flete 2026');
  const hojaNueva = ss.getSheetByName(NOMBRE_HOJA);

  if (!hojaVieja) {
    SpreadsheetApp.getUi().alert("No encontré la hoja 'flete 2026'.");
    return;
  }

  // Backup dedicado (copia completa de FLETE antes de tocar nada)
  backupAntesDeImport();

  const datosViejos = hojaVieja.getDataRange().getValues();
  const hV = datosViejos[0];
  const idxPedidoV = hV.indexOf('N°PEDIDO');
  const idxCodigoV = hV.indexOf('CODIGO DE CLIENTE');
  const idxFechaV = hV.indexOf('FECHA');
  const idxNetoV = hV.indexOf('NETO COBRADO POR EL TRANSPORTE');

  const fmtFecha = (f) => (f instanceof Date)
    ? Utilities.formatDate(f, Session.getScriptTimeZone(), 'yyyy-MM-dd')
    : String(f).trim();

  const mapaPorPedido = {};       // pedido -> {valor, ambiguo}
  const mapaPorClienteFecha = {}; // codigo_fecha -> {valor, ambiguo}

  for (let i = 1; i < datosViejos.length; i++) {
    const fila = datosViejos[i];
    const neto = fila[idxNetoV];
    if (neto === '' || neto === null) continue; // solo filas con flete real cargado

    const pedido = String(fila[idxPedidoV] || '').trim();
    const codigo = String(fila[idxCodigoV] || '').trim();
    const fecha = fila[idxFechaV];

    if (pedido) {
      if (mapaPorPedido[pedido] === undefined) {
        mapaPorPedido[pedido] = { valor: neto, ambiguo: false };
      } else if (mapaPorPedido[pedido].valor !== neto) {
        mapaPorPedido[pedido].ambiguo = true;
      }
    }
    if (codigo && fecha) {
      const claveCF = `${codigo}_${fmtFecha(fecha)}`;
      if (mapaPorClienteFecha[claveCF] === undefined) {
        mapaPorClienteFecha[claveCF] = { valor: neto, ambiguo: false };
      } else if (mapaPorClienteFecha[claveCF].valor !== neto) {
        mapaPorClienteFecha[claveCF].ambiguo = true;
      }
    }
  }

  // --- Recorrer FLETE y completar donde falte ---
  const datosNuevos = hojaNueva.getDataRange().getValues();
  const hN = datosNuevos[0];
  const idxPedidoN = hN.indexOf('N°PEDIDO');
  const idxCodigoN = hN.indexOf('CODIGO DE CLIENTE');
  const idxFechaN = hN.indexOf('FECHA');
  const idxNetoN = hN.indexOf('NETO COBRADO POR EL TRANSPORTE');
  const idxClienteN = hN.indexOf('CLIENTE');

  let completadosPorPedido = 0;
  let completadosPorClienteFecha = 0;
  const revision = []; // filas ambiguas o dudosas, para revisión manual

  for (let i = 1; i < datosNuevos.length; i++) {
    const fila = datosNuevos[i];
    const netoActual = fila[idxNetoN];
    if (netoActual !== '' && netoActual !== null) continue; // ya cargado a mano, no tocar

    const pedido = String(fila[idxPedidoN] || '').trim();
    const codigo = String(fila[idxCodigoN] || '').trim();
    const fecha = fila[idxFechaN];
    const cliente = fila[idxClienteN];

    let match = pedido ? mapaPorPedido[pedido] : undefined;
    let fuente = 'pedido';

    if (!match && codigo && fecha) {
      const claveCF = `${codigo}_${fmtFecha(fecha)}`;
      match = mapaPorClienteFecha[claveCF];
      fuente = 'cliente+fecha';
    }

    if (match && match.ambiguo) {
      revision.push([cliente, pedido, codigo, fecha, 'AMBIGUO (' + fuente + ')']);
      continue;
    }
    if (match) {
      hojaNueva.getRange(i + 1, idxNetoN + 1).setValue(match.valor);
      if (fuente === 'pedido') completadosPorPedido++; else completadosPorClienteFecha++;
    }
  }

  // --- Volcar revisiones a una pestaña ---
  if (revision.length > 0) {
    let hojaRevision = ss.getSheetByName('Import_Revision');
    if (!hojaRevision) hojaRevision = ss.insertSheet('Import_Revision');
    hojaRevision.clear();
    hojaRevision.appendRow(['CLIENTE', 'N°PEDIDO', 'CODIGO DE CLIENTE', 'FECHA', 'MOTIVO']);
    hojaRevision.getRange(2, 1, revision.length, 5).setValues(revision);
  }

  SpreadsheetApp.getUi().alert(
    `Import terminado.\n` +
    `Completados por N°PEDIDO: ${completadosPorPedido}\n` +
    `Completados por CODIGO+FECHA: ${completadosPorClienteFecha}\n` +
    `Casos ambiguos a revisar: ${revision.length}`
  );
}

// ===== UTILIDAD =====
function columnaALetra(n) {
  let s = "";
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

// ===== SUBIR FLETES CARGADOS A SUPABASE (bronze.fletes) =====
function subirFletesASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName(NOMBRE_HOJA);
  const datos = hoja.getDataRange().getValues();
  const headers = datos[0];
  const idxClave = headers.indexOf('_clave_fila');
  const idxNeto = headers.indexOf('NETO COBRADO POR EL TRANSPORTE');

  const filasParaSubir = [];
  for (let i = 1; i < datos.length; i++) {
    const clave = datos[i][idxClave];
    const neto = datos[i][idxNeto];
    if (!clave) continue;
    if (neto === '' || neto === null) continue; // solo subimos fletes ya cargados

    filasParaSubir.push({
      clave_fila: String(clave),
      neto_cobrado_transporte: Number(neto),
      fecha_carga: new Date().toISOString()
    });
  }

  if (filasParaSubir.length === 0) {
    SpreadsheetApp.getUi().alert("No hay fletes cargados en la hoja para subir.");
    return;
  }

  const url = getProp("SUPABASE_URL");
  const serviceKey = getProp("SUPABASE_SERVICE_KEY"); // service_role, NO la anon
  if (!serviceKey) {
    SpreadsheetApp.getUi().alert("Falta la Script Property SUPABASE_SERVICE_KEY. Agregala antes de continuar.");
    return;
  }
  const endpoint = url + "/rest/v1/fletes?on_conflict=clave_fila";

  const LOTE = 500;
  let subidos = 0;
  for (let i = 0; i < filasParaSubir.length; i += LOTE) {
    const lote = filasParaSubir.slice(i, i + LOTE);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: "post",
      contentType: "application/json",
      headers: {
        "apikey": serviceKey,
        "Authorization": "Bearer " + serviceKey,
        "Content-Profile": "bronze",     // porque la tabla vive en el schema bronze
        "Prefer": "resolution=merge-duplicates,return=minimal"
      },
      payload: JSON.stringify(lote),
      muteHttpExceptions: true
    });

    if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) {
      subidos += lote.length;
    } else {
      Logger.log("Error subiendo lote: " + resp.getContentText().substring(0, 300));
      SpreadsheetApp.getUi().alert(
        "Error al subir (revisá el log de ejecución).\n" +
        "Se subieron " + subidos + " de " + filasParaSubir.length + " antes de fallar."
      );
      return;
    }
  }

  SpreadsheetApp.getUi().alert("✅ Subidos " + subidos + " fletes a bronze.fletes en Supabase.");
}

// ===== SUBIR FLETE_PROVEEDORES A SUPABASE =====
// A diferencia de la hoja "FLETE" (que viene de reporte_logistica y ya trae
// _clave_fila de Supabase), esta hoja es de carga manual, así que si una fila
// no tiene clave propia le generamos un UUID estable y lo guardamos en la
// columna oculta "_clave_fila" (se crea sola la primera vez que corre esto).
// Solo sube filas que ya tengan "NETO COBRADO POR EL TRANSPORTE" cargado.
function subirFleteProveedoresASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const NOMBRE_HOJA_FP = 'FLETE_PROVEEDORES';
  const hoja = ss.getSheetByName(NOMBRE_HOJA_FP);
  if (!hoja) {
    SpreadsheetApp.getUi().alert("No encontré la hoja '" + NOMBRE_HOJA_FP + "'.");
    return;
  }

  const ultimaCol = hoja.getLastColumn();
  const headers = hoja.getRange(1, 1, 1, ultimaCol).getValues()[0];
  const idxFecha          = headers.indexOf('FECHA');
  const idxProveedor      = headers.indexOf('PROVEEDOR');
  const idxNetoFacturado  = headers.indexOf('NETO FACTURADO PROVEEDOR');
  const idxNetoCobrado    = headers.indexOf('NETO COBRADO POR EL TRANSPORTE');
  const idxFactura        = headers.indexOf('N° FACTURA SEVILLANITA');

  if ([idxFecha, idxProveedor, idxNetoFacturado, idxNetoCobrado].includes(-1)) {
    SpreadsheetApp.getUi().alert("Faltan columnas esperadas en '" + NOMBRE_HOJA_FP + "'. Revisá los encabezados.");
    return;
  }

  // Columna oculta de clave estable — se crea al final si no existe todavía
  let idxClave = headers.indexOf('_clave_fila');
  if (idxClave === -1) {
    idxClave = ultimaCol; // 0-based → próxima columna libre
    hoja.getRange(1, idxClave + 1).setValue('_clave_fila');
  }

  const lastRow = hoja.getLastRow();
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("La hoja '" + NOMBRE_HOJA_FP + "' no tiene datos cargados.");
    return;
  }

  const numCols = Math.max(hoja.getLastColumn(), idxClave + 1);
  const datos = hoja.getRange(2, 1, lastRow - 1, numCols).getValues();

  const filasParaSubir = [];
  const clavesNuevas = []; // { rowNum, valor } — para persistir los UUID generados ahora

  datos.forEach(function(fila, i) {
    const fecha = fila[idxFecha];
    const proveedor = String(fila[idxProveedor] || '').trim();
    const netoCobrado = fila[idxNetoCobrado];

    if (!proveedor || !fecha) return; // fila vacía, se ignora
    if (netoCobrado === '' || netoCobrado === null || netoCobrado === undefined) return; // sin flete cargado todavía

    let clave = fila[idxClave];
    if (!clave) {
      clave = Utilities.getUuid();
      clavesNuevas.push({ rowNum: i + 2, valor: clave });
    }

    const fechaObj = (fecha instanceof Date) ? fecha : new Date(fecha);
    const mesComercial = Utilities.formatDate(fechaObj, 'GMT-3', 'yyyy-MM');

    filasParaSubir.push({
      clave_fila: String(clave),
      proveedor: proveedor,
      fecha: Utilities.formatDate(fechaObj, 'GMT-3', 'dd/MM/yyyy'),
      mes_comercial: mesComercial,
      neto_facturado_proveedor: Number(fila[idxNetoFacturado]) || 0,
      neto_cobrado_transporte: Number(netoCobrado) || 0,
      factura_sevillanita: idxFactura >= 0 ? String(fila[idxFactura] || '') : '',
      fecha_carga: new Date().toISOString()
    });
  });

  // Persistir los UUID nuevos ANTES de subir, para que si algo falla en el
  // paso de red no se generen claves distintas la próxima vez que se corra.
  clavesNuevas.forEach(function(c) {
    hoja.getRange(c.rowNum, idxClave + 1).setValue(c.valor);
  });

  if (filasParaSubir.length === 0) {
    SpreadsheetApp.getUi().alert("No hay filas con 'NETO COBRADO POR EL TRANSPORTE' cargado en '" + NOMBRE_HOJA_FP + "' para subir.");
    return;
  }

  const url = getProp("SUPABASE_URL");
  const serviceKey = getProp("SUPABASE_SERVICE_KEY"); // service_role, NO la anon
  if (!serviceKey) {
    SpreadsheetApp.getUi().alert("Falta la Script Property SUPABASE_SERVICE_KEY. Agregala antes de continuar.");
    return;
  }
  const endpoint = url + "/rest/v1/fletes_proveedores?on_conflict=clave_fila";

  const LOTE = 500;
  let subidos = 0;
  for (let i = 0; i < filasParaSubir.length; i += LOTE) {
    const lote = filasParaSubir.slice(i, i + LOTE);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: "post",
      contentType: "application/json",
      headers: {
        "apikey": serviceKey,
        "Authorization": "Bearer " + serviceKey,
        "Content-Profile": "bronze", // la tabla vive en el schema bronze
        "Prefer": "resolution=merge-duplicates,return=minimal"
      },
      payload: JSON.stringify(lote),
      muteHttpExceptions: true
    });

    if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) {
      subidos += lote.length;
    } else {
      Logger.log("Error subiendo lote: " + resp.getContentText().substring(0, 300));
      SpreadsheetApp.getUi().alert(
        "Error al subir (revisá el log de ejecución).\n" +
        "Se subieron " + subidos + " de " + filasParaSubir.length + " antes de fallar."
      );
      return;
    }
  }

  SpreadsheetApp.getUi().alert("✅ Subidos " + subidos + " registros a bronze.fletes_proveedores en Supabase.");
}

// ===== CORREGIR los que se cargaron con el bug anterior (columna TOTAL/M en vez de P) =====
// Solo toca celdas cuyo valor actual coincide EXACTO con lo que el script viejo
// (con el bug) hubiera puesto -- para no arriesgar pisar algo cargado a mano de verdad.
function corregirFletesViejosMalCargados() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hojaVieja = ss.getSheetByName('Flete_viejo');
  const hojaFlete = ss.getSheetByName(NOMBRE_HOJA);

  if (!hojaVieja) {
    SpreadsheetApp.getUi().alert("No encontre la hoja 'Flete_viejo'.");
    return;
  }

  backupAntesDeImport();

  // --- Leer Flete_viejo: necesitamos AMBOS valores (el viejo con bug y el correcto) ---
  const datosViejos = hojaVieja.getDataRange().getValues();
  const hV = datosViejos[0];
  const idxPedidoV = hV.indexOf('N°PEDIDO');
  const idxTotalViejoV = hV.indexOf('TOTAL');               // columna M: valor con el que se cargo mal
  const idxMontoCorrectoV = hV.indexOf('IMPOR FACTURA S/IVA'); // columna P: valor correcto

  const registrosViejos = [];
  for (let i = 1; i < datosViejos.length; i++) {
    const fila = datosViejos[i];
    const valorViejo = fila[idxTotalViejoV];
    const valorCorrecto = fila[idxMontoCorrectoV];
    if (valorCorrecto === '' || valorCorrecto === null || valorCorrecto === undefined) continue;
    if (valorViejo === '' || valorViejo === null || valorViejo === undefined) continue;

    const pedidoRaw = String(fila[idxPedidoV] || '').trim();
    const codigos = pedidoRaw.split('-').map(c => c.trim()).filter(c => c !== '' && /^\d+$/.test(c));
    if (codigos.length === 0) continue;

    registrosViejos.push({ codigos: codigos, valorViejo: valorViejo, valorCorrecto: valorCorrecto });
  }

  // --- Mapa de codigo de pedido -> filas de FLETE (igual que antes) ---
  const datosFlete = hojaFlete.getDataRange().getValues();
  const hF = datosFlete[0];
  const idxPedidoF = hF.indexOf('N°PEDIDO');
  const idxNetoF = hF.indexOf('NETO COBRADO POR EL TRANSPORTE');

  const mapaCodigoAFilas = {};
  for (let i = 1; i < datosFlete.length; i++) {
    const pedidosStr = String(datosFlete[i][idxPedidoF] || '');
    const codigosEnFila = pedidosStr.split(',').map(c => c.trim()).filter(c => /^\d+$/.test(c));
    codigosEnFila.forEach(codigo => {
      if (!mapaCodigoAFilas[codigo]) mapaCodigoAFilas[codigo] = [];
      mapaCodigoAFilas[codigo].push(i);
    });
  }

  // --- Corregir solo donde el valor actual coincide con el valor viejo (con bug) ---
  let corregidos = 0;
  let noCoincidian = 0;

  registrosViejos.forEach(reg => {
    const filasEncontradas = new Set();
    reg.codigos.forEach(codigo => {
      const filas = mapaCodigoAFilas[codigo];
      if (filas) filas.forEach(f => filasEncontradas.add(f));
    });
    if (filasEncontradas.size !== 1) return; // mismo criterio: si es ambiguo, no tocar

    const filaIdx = [...filasEncontradas][0];
    const netoActual = datosFlete[filaIdx][idxNetoF];

    // Comparacion numerica con tolerancia chica por redondeo
    const coincideConElBug = typeof netoActual === 'number' &&
      Math.abs(netoActual - reg.valorViejo) < 0.01;

    if (coincideConElBug) {
      hojaFlete.getRange(filaIdx + 1, idxNetoF + 1).setValue(reg.valorCorrecto);
      corregidos++;
    } else {
      noCoincidian++;
    }
  });

  SpreadsheetApp.getUi().alert(
    `Correccion terminada.\n` +
    `Corregidos (tenian el valor del bug): ${corregidos}\n` +
    `No coincidian con el bug, no se tocaron: ${noCoincidian}`
  );
}

// ===== IMPORTAR FLETES DESDE LA HOJA "Flete_viejo" =====
// Formato de esa hoja: N°PEDIDO puede tener 1 o 2 codigos separados por "-"
// (NO es un rango, son 2 pedidos puntuales que compartieron un mismo flete).
// La columna TOTAL (columna M) es el monto real cobrado por el transporte.
function importarFletesViejos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hojaVieja = ss.getSheetByName('Flete_viejo');
  const hojaFlete = ss.getSheetByName(NOMBRE_HOJA); // "FLETE"

  if (!hojaVieja) {
    SpreadsheetApp.getUi().alert("No encontre la hoja 'Flete_viejo'.");
    return;
  }

  // Backup completo antes de tocar nada
  backupAntesDeImport();

  // --- 1) Leer Flete_viejo ---
  const datosViejos = hojaVieja.getDataRange().getValues();
  const hV = datosViejos[0];
  const idxPedidoV = hV.indexOf('N°PEDIDO');
  const idxMontoV = hV.indexOf('IMPOR FACTURA S/IVA');   // columna P: monto real del flete
  const idxFacturaTranspV = hV.indexOf('N° FACTURA');    // columna S: numero de factura del transporte
  const idxClienteV = hV.indexOf('CLIENTE / PROVEEDOR');
  const idxFechaV = hV.indexOf('Fecha');

  const registrosViejos = [];
  for (let i = 1; i < datosViejos.length; i++) {
    const fila = datosViejos[i];
    const monto = fila[idxMontoV];
    if (monto === '' || monto === null || monto === undefined) continue;

    const pedidoRaw = String(fila[idxPedidoV] || '').trim();
    // Separar por "-" : puede haber 1 o 2 codigos (NO es un rango)
    const codigos = pedidoRaw.split('-').map(c => c.trim()).filter(c => c !== '' && /^\d+$/.test(c));
    if (codigos.length === 0) continue;

    registrosViejos.push({
      codigos: codigos,
      monto: monto,
      facturaTransporte: fila[idxFacturaTranspV],
      cliente: fila[idxClienteV],
      fecha: fila[idxFechaV],
    });
  }

  // --- 2) Leer FLETE actual: para cada fila, que codigos de pedido contiene ---
  const datosFlete = hojaFlete.getDataRange().getValues();
  const hF = datosFlete[0];
  const idxPedidoF = hF.indexOf('N°PEDIDO');
  const idxNetoF = hF.indexOf('NETO COBRADO POR EL TRANSPORTE');
  const idxFacturaSevillanitaF = hF.indexOf('N° FACTURA SEVILLANITA'); // columna V
  const idxClienteF = hF.indexOf('CLIENTE');
  const idxFechaF = hF.indexOf('FECHA');

  // Mapa: codigo de pedido individual -> lista de indices de fila de FLETE donde aparece
  const mapaCodigoAFilas = {};
  for (let i = 1; i < datosFlete.length; i++) {
    const pedidosStr = String(datosFlete[i][idxPedidoF] || '');
    const codigosEnFila = pedidosStr.split(',').map(c => c.trim()).filter(c => /^\d+$/.test(c));
    codigosEnFila.forEach(codigo => {
      if (!mapaCodigoAFilas[codigo]) mapaCodigoAFilas[codigo] = [];
      mapaCodigoAFilas[codigo].push(i); // indice de fila (0-based dentro de datosFlete)
    });
  }

  // --- 3) Procesar cada registro viejo ---
  let completados = 0;
  let facturasCompletadas = 0;
  let yaTenianValor = 0;
  const revision = [];

  registrosViejos.forEach(reg => {
    // Encontrar a que filas de FLETE apuntan sus codigos
    const filasEncontradas = new Set();
    reg.codigos.forEach(codigo => {
      const filas = mapaCodigoAFilas[codigo];
      if (filas) filas.forEach(f => filasEncontradas.add(f));
    });

    if (filasEncontradas.size === 0) {
      revision.push([reg.cliente, reg.codigos.join('-'), reg.fecha, reg.monto, 'SIN MATCH en FLETE (pedido no encontrado, probablemente anterior a mayo)']);
      return;
    }

    if (filasEncontradas.size > 1) {
      revision.push([reg.cliente, reg.codigos.join('-'), reg.fecha, reg.monto, 'AMBIGUO: los codigos apuntan a mas de una preparacion distinta']);
      return;
    }

    // Exactamente 1 fila de FLETE encontrada
    const filaIdx = [...filasEncontradas][0];
    const netoActual = datosFlete[filaIdx][idxNetoF];
    const facturaActual = datosFlete[filaIdx][idxFacturaSevillanitaF];

    let algoSeCompleto = false;

    if (netoActual === '' || netoActual === null || netoActual === undefined) {
      hojaFlete.getRange(filaIdx + 1, idxNetoF + 1).setValue(reg.monto);
      completados++;
      algoSeCompleto = true;
    }

    if ((facturaActual === '' || facturaActual === null || facturaActual === undefined) 
        && reg.facturaTransporte) {
      hojaFlete.getRange(filaIdx + 1, idxFacturaSevillanitaF + 1).setValue(reg.facturaTransporte);
      facturasCompletadas++;
      algoSeCompleto = true;
    }

    if (!algoSeCompleto) yaTenianValor++;
  });

  // --- 4) Volcar revisiones ---
  if (revision.length > 0) {
    let hojaRevision = ss.getSheetByName('Import_Revision_Viejo');
    if (!hojaRevision) hojaRevision = ss.insertSheet('Import_Revision_Viejo');
    hojaRevision.clear();
    hojaRevision.appendRow(['CLIENTE', 'CODIGOS PEDIDO', 'FECHA', 'TOTAL (flete_viejo)', 'MOTIVO']);
    hojaRevision.getRange(2, 1, revision.length, 5).setValues(revision);
  }

  SpreadsheetApp.getUi().alert(
    `Import de Flete_viejo terminado.\n` +
    `Montos de flete completados: ${completados}\n` +
    `Numeros de factura completados: ${facturasCompletadas}\n` +
    `Filas sin nada nuevo para completar: ${yaTenianValor}\n` +
    `Para revisar manualmente: ${revision.length}\n\n` +
    (revision.length > 0 ? `Ver pestana "Import_Revision_Viejo" para el detalle.` : '')
  );
}

// ===== MENU =====
//
// LOS DOS MENUS SE CREAN ACA Y EN NINGUN OTRO ARCHIVO. En Apps Script hay UN
// SOLO onOpen por proyecto: si otro archivo define el suyo, no son dos menus,
// es uno que reemplaza al otro en silencio.
function onOpen() {
  const ui = SpreadsheetApp.getUi();

  // Operación: lo que se hace todos los días con los fletes.
  ui.createMenu("🚚 Logística")
    .addItem("Actualizar reporte", "actualizarReporteLogistica")
    .addItem("Subir fletes a Supabase", "subirFletesASupabase")
    .addItem("Subir flete proveedores a Supabase", "subirFleteProveedoresASupabase")
    .addItem("Registrar pedido corregido", "registrarPedidoCorregido")
    .addItem("Guardar correcciones KG/Volumen", "guardarCorreccionesKgVolumen")
    .addItem("Marcar como sincronizado", "marcarFletesComoSincronizados")
    .addToUi();

  // Análisis: lo que se calcula sobre esos fletes, y la subida del resultado.
  ui.createMenu("📊 Análisis de Logística")
    .addItem("Calcular análisis de tarifa", "calcularAnalisisTarifa")
    .addItem("Calcular análisis de peso", "calcularAnalisisPeso")
    .addItem("Calcular acciones", "calcularAcciones")
    .addItem("Calcular análisis financiero", "calcularAnalisisFinanciero")
    .addItem("Calcular mínimo de compra", "calcularMinimoDeCompra")
    .addSeparator()
    .addItem("Subir auditoría al tablero", "subirAuditoria")
    .addToUi();
}

// ===== REGISTRAR PEDIDO CORREGIDO (facturas de Sigma "por fuera del pedido") =====
// Abre 3 cuadros de dialogo simples (uno por dato) y sube la corrección a
// bronze.pedidos_override en Supabase. La vista gold.reporte_logistica ya
// usa esa tabla automaticamente -- no hace falta tocar nada mas.
function registrarPedidoCorregido() {
  const ui = SpreadsheetApp.getUi();

  const r1 = ui.prompt(
    "Pedido corregido (1/3)",
    "Número de PEDIDO ERRÓNEO en Sigma (el que quedó facturado 'por fuera', ej. 3982):",
    ui.ButtonSet.OK_CANCEL
  );
  if (r1.getSelectedButton() !== ui.Button.OK) return;
  const pedidoErroneo = r1.getResponseText().trim();
  if (!pedidoErroneo) {
    ui.alert("No ingresaste el pedido erróneo. Cancelado.");
    return;
  }

  const r2 = ui.prompt(
    "Pedido corregido (2/3)",
    "Número del PEDIDO CORRECTO (el que sí tiene la preparación/envío real, ej. 3955):",
    ui.ButtonSet.OK_CANCEL
  );
  if (r2.getSelectedButton() !== ui.Button.OK) return;
  const pedidoCorrecto = r2.getResponseText().trim();
  if (!pedidoCorrecto) {
    ui.alert("No ingresaste el pedido correcto. Cancelado.");
    return;
  }

  const r3 = ui.prompt(
    "Pedido corregido (3/3)",
    "Motivo / nota (ej. cliente, N° de factura, por qué se facturó así):",
    ui.ButtonSet.OK_CANCEL
  );
  if (r3.getSelectedButton() !== ui.Button.OK) return;
  const motivo = r3.getResponseText().trim();

  const url = getProp("SUPABASE_URL");
  const serviceKey = getProp("SUPABASE_SERVICE_KEY");
  if (!serviceKey) {
    ui.alert("Falta la Script Property SUPABASE_SERVICE_KEY.");
    return;
  }
  const endpoint = url + "/rest/v1/pedidos_override?on_conflict=pedido_erroneo";

  const payload = [{
    pedido_erroneo: pedidoErroneo,
    pedido_correcto: pedidoCorrecto,
    motivo: motivo || null,
  }];

  const resp = UrlFetchApp.fetch(endpoint, {
    method: "post",
    contentType: "application/json",
    headers: {
      "apikey": serviceKey,
      "Authorization": "Bearer " + serviceKey,
      "Content-Profile": "bronze",
      "Prefer": "resolution=merge-duplicates,return=minimal"
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) {
    ui.alert(
      "✅ Listo. Pedido " + pedidoErroneo + " ahora se reasigna a " + pedidoCorrecto + ".\n\n" +
      "El reporte de logística (Supabase / la planilla FLETE) ya lo va a reflejar en la próxima consulta, sin necesidad de correr nada más."
    );
  } else {
    Logger.log("Error: " + resp.getContentText().substring(0, 300));
    ui.alert("❌ Error al guardar. Revisá el log de ejecución (Ver → Registros).");
  }
}
