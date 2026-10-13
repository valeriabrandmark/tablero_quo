const CFG = {
  baseUrl:    'https://secure.sig2k.com/sigmasaas/quomkt@sigma/sigma/api/v10/',
  // EL TOKEN YA NO ESTA ACA. Vive en Configuración del proyecto →
  // Propiedades del script, con el nombre SIGMA_TOKEN, igual que
  // SUPABASE_URL y SUPABASE_SERVICE_KEY. Se lee con sigmaToken_().
  //
  // Estaba escrito en esta línea en texto plano, y este archivo se respalda
  // en un repo PUBLICO. Hay que rotar ese token: salió de donde vivía.
  empresas:   ['0001', '0002', '0003', '0004'],
  vendedores: ['004', '006', '007'],
  fechaDesde: '2022-01-01',
  pageSize:   500
};

// BUENO es ahora el mejor estado: no debe nada. AL DÍA es el que debe pero
// dentro de los 15 dias de tolerancia. Este orden lo usa la tendencia.
const PESO = { 'BUENO': 0, 'AL DÍA': 1, 'OBSERVACIÓN': 2, 'RIESGOSO': 3, 'CRÍTICO': 4 };

// ═══════════════════════════════════════════════════════════
// AVISOS — reemplaza a ui.alert() en TODO el proyecto
//
// POR QUÉ EXISTE: SpreadsheetApp.getUi() TIRA EXCEPCIÓN cuando no hay
// pantalla, que es exactamente el caso de un trigger horario. Antes, el
// trigger de las 6 AM moría en el primer alert y dejaba de subir las tablas
// que venían después. Con esto, si hay pantalla avisa, y si no, loguea.
//
// REGLA: en este proyecto NUNCA se llama a SpreadsheetApp.getUi() directo.
// ═══════════════════════════════════════════════════════════
function avisar(mensaje) {
  Logger.log(mensaje);
  try {
    SpreadsheetApp.getUi().alert(mensaje);
  } catch (e) {
    // Sin pantalla (trigger): ya quedó en el log, seguimos de largo.
  }
}

/**
 * El token de Sigma, desde las Propiedades del script.
 *
 * El guion bajo final lo hace privado para Apps Script: no aparece en el menú
 * de ejecución, que es donde no tiene nada que hacer.
 *
 * Y revienta con un mensaje claro si falta, en vez de llamar a la API con
 * `null` y recibir un 401 que no dice por qué.
 */
function sigmaToken_() {
  const t = PropertiesService.getScriptProperties().getProperty('SIGMA_TOKEN');
  if (!t) {
    throw new Error(
      'Falta la Script Property SIGMA_TOKEN.\n' +
      'Agregala en: Configuración del proyecto > Propiedades del script.');
  }
  return t;
}

/**
 * El CUIT como clave para cruzar hojas, sin guiones, espacios ni puntos.
 *
 * POR QUE EL CUIT Y NO EL ClienteId. El CUIT es el mismo número en las cuatro
 * empresas; el clienteId no tiene por qué serlo, y además falta: de las 211
 * filas de Historial_Scoring que llegaron a Supabase, 47 no tienen ninguno.
 * Cruzando por CUIT, los 118 clientes del scoring encuentran su historial
 * (verificado: 118 de 118).
 */
function claveCuit(valor) {
  return String(valor || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase();
}

function normVendedor(val) {
  return String(parseInt(String(val).replace(/[^0-9]/g, '')) || 0).padStart(3, '0');
}
function normEmpresa(val) {
  return String(parseInt(String(val).replace(/[^0-9]/g, '')) || 0).padStart(4, '0');
}

// ═══════════════════════════════════════════════════════════
// PROBAR CONEXIÓN
// ═══════════════════════════════════════════════════════════
function probarConexion() {
  try {
    const resp = llamarAPI('ExportVendedores', {});
    if (Array.isArray(resp)) {
      avisar('✅ Conexión OK\nVendedores encontrados: ' + resp.length + '\n\n' +
             resp.slice(0, 5).map(v => v.id + ' — ' + v.nombre).join('\n') +
             (resp.length > 5 ? '\n...y ' + (resp.length - 5) + ' más' : ''));
    } else {
      avisar('⚠️ Respuesta inesperada\n' + JSON.stringify(resp).substring(0, 500));
    }
  } catch (e) {
    avisar('❌ Error de conexión\n' + e.message);
  }
}

// ═══════════════════════════════════════════════════════════
// FUNCIÓN A — ACTUALIZACIÓN DIARIA
// ═══════════════════════════════════════════════════════════
function actualizarDiario() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  try {
    const hoyStr = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM-dd');

    Logger.log('→ [1/4] Descargando facturas impagas...');
    const { facturas, recibos } = descargarDatos(hoyStr);

    Logger.log('→ [2/4] Escribiendo hoja de aging...');
    escribirHoja(ss, construirFilas(facturas, recibos));

    Logger.log('→ [3/4] Leyendo historial y generando scoring...');
    const historial    = leerHistorial(ss);
    const filasScoring = generarScoringClientes(facturas, historial);
    escribirHojaScoring(ss, filasScoring);

    Logger.log('→ [4/4] Detectando cancelaciones de mora...');
    const cancelaciones = detectarCancelaciones(ss, filasScoring);

    avisar('✅ Datos actualizados\n' +
      'Facturas impagas: '           + facturas.length  +
      '\nRecibos a cuenta: '         + recibos.length   +
      '\nClientes en scoring: '      + filasScoring.length +
      '\nCancelaciones detectadas: ' + cancelaciones    +
      '\n\n📌 La foto mensual se guarda automáticamente el 1° de cada mes.');

  } catch (e) {
    Logger.log('✖ ERROR: ' + e.message + '\n' + e.stack);
    avisar('❌ Error en actualización diaria\n' + e.message);
    throw e;   // que el trigger lo registre como fallo, no como éxito silencioso
  }
}

// ═══════════════════════════════════════════════════════════
// FUNCIÓN B — CIERRE MENSUAL
// ═══════════════════════════════════════════════════════════
function guardarFotoMensual() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  try {
    const hoyStr  = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM-dd');
    const periodo = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM');

    Logger.log('→ [CIERRE MENSUAL] Período: ' + periodo);

    Logger.log('→ [1/3] Descargando datos frescos de Sigma...');
    const { facturas } = descargarDatos(hoyStr);

    Logger.log('→ [2/3] Generando scoring para el cierre...');
    const historial    = leerHistorial(ss);
    const filasScoring = generarScoringClientes(facturas, historial);

    Logger.log('→ [3/3] Guardando foto del período ' + periodo + '...');
    const guardado = guardarSnapshot(ss, filasScoring);

    if (guardado) {
      avisar('📅 Cierre mensual guardado\n' +
        'Período: ' + periodo +
        '\nClientes registrados: ' + filasScoring.length +
        '\n\nEsta foto queda disponible en Historial_Scoring para informes financieros.');
    } else {
      avisar('⏭ Foto ya existente\n' +
        'El período ' + periodo + ' ya tenía una foto guardada. No se duplicó.');
    }

  } catch (e) {
    Logger.log('✖ ERROR EN CIERRE MENSUAL: ' + e.message + '\n' + e.stack);
    avisar('❌ Error en cierre mensual\n' + e.message);
    throw e;
  }
}

// ═══════════════════════════════════════════════════════════
// DESCARGA DE DATOS DESDE SIGMA (compartida por ambas funciones)
// ═══════════════════════════════════════════════════════════
function descargarDatos(hoyStr) {
  const todasFacturas = llamarAPIPaginada('ExportFacturas', {
    estado: 'impagas', dde: CFG.fechaDesde, hta: hoyStr
  });
  const facturas = todasFacturas.filter(f =>
    CFG.empresas.includes(f.empresa) && CFG.vendedores.includes(f.vendedor)
  );
  Logger.log('← Facturas filtradas: ' + facturas.length);

  const todosRecibos = llamarAPIPaginada('ExportRecibos', {});
  const clientesVendedores = new Set(facturas.map(f => f.clienteId));
  const recibos = todosRecibos.filter(r =>
    CFG.empresas.includes(r.empresa) && clientesVendedores.has(r.clienteId)
  );
  Logger.log('← Recibos filtrados: ' + recibos.length);

  return { facturas, recibos };
}

// ═══════════════════════════════════════════════════════════
// HISTORIAL — leer fotos acumuladas
// ═══════════════════════════════════════════════════════════
function leerHistorial(ss) {
  const hoja = ss.getSheetByName('Historial_Scoring');
  if (!hoja || hoja.getLastRow() <= 1) return {};

  const ultimaFila = hoja.getLastRow();
  const datos = hoja.getRange(2, 1, ultimaFila - 1, 14).getValues();

  const hoy = new Date();
  const hace6Meses = new Date(hoy.getFullYear(), hoy.getMonth() - 6, 1);

  const porCliente = {};
  datos.forEach(function(fila) {
    // ⬇️ ARREGLADO. Antes era String(fila[1]). La columna Periodo es una celda
    // de FECHA, y String() sobre un Date da "Wed Jul 01 2026 00:00:00
    // GMT-0300 (...)". Ese texto pasa el filtro de longitud, y despues
    // .split('-') lo parte por el guion del GMT-0300, no por el del periodo:
    // parseInt("Wed Jul 01 2026 00:00:00 GMT") es NaN, y fechaPeriodo queda
    // Invalid Date PARA TODAS LAS FILAS.
    //
    // Consecuencia: el filtro de los ultimos 6 meses no deja pasar ninguna
    // (NaN >= fecha es false), asi que mesesEnMora daba 0 y peorCategoria
    // 'BUENO' para todo el mundo. Verificado en node.
    const periodo   = formatearCampoPeriodo(fila[1]);
    const cuit      = claveCuit(fila[4]);
    const categoria = String(fila[13]);
    const vendedor  = normVendedor(fila[5]);
    const empresa   = normEmpresa(fila[6]);

    // La clave pasa a ser el CUIT: ver claveCuit() arriba.
    if (!periodo || !cuit || periodo.length < 7) return;
    if (!CFG.vendedores.includes(vendedor)) return;
    if (!CFG.empresas.includes(empresa))    return;

    const partes = periodo.split('-');
    const fechaPeriodo = new Date(parseInt(partes[0]), parseInt(partes[1]) - 1, 1);

    if (!porCliente[cuit]) porCliente[cuit] = [];
    porCliente[cuit].push({ periodo, fechaPeriodo, categoria });
  });

  const resultado = {};
  Object.keys(porCliente).forEach(function(clienteId) {
    const todos = porCliente[clienteId]
      .sort(function(a, b) { return a.fechaPeriodo - b.fechaPeriodo; });
    const ult6 = todos.filter(function(s) { return s.fechaPeriodo >= hace6Meses; });
    // Con el criterio nuevo hay DOS estados sanos, no uno: BUENO (no debe
    // nada) y AL DÍA (debe pero dentro de la tolerancia). Los dos salen del
    // conteo de meses en mora.
    const mesesEnMora = ult6.filter(function(s) {
      return s.categoria !== 'AL DÍA' && s.categoria !== 'BUENO';
    }).length;
    const peorCategoria = ult6.reduce(function(peor, s) {
      return (PESO[s.categoria] || 0) > (PESO[peor] || 0) ? s.categoria : peor;
    }, 'BUENO');
    const categoriaAnterior = todos.length > 0 ? todos[todos.length - 1].categoria : null;
    resultado[clienteId] = { mesesEnMora, peorCategoria, categoriaAnterior };
  });

  return resultado;
}

// ═══════════════════════════════════════════════════════════
// GUARDAR FOTO MENSUAL EN HISTORIAL_SCORING
// ═══════════════════════════════════════════════════════════
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
    hoja.setColumnWidth(1, 95);  hoja.setColumnWidth(2, 80);
    hoja.setColumnWidth(3, 90);  hoja.setColumnWidth(4, 250);
    hoja.setColumnWidth(5, 140); hoja.setColumnWidth(15, 140);
    hoja.getRange(1, 6, 1000, 1).setNumberFormat('@');
    hoja.getRange(1, 7, 1000, 1).setNumberFormat('@');
  } else {
    if (hoja.getLastColumn() < 15) {
      hoja.getRange(1, 15).setValue('Saldo Vencido')
          .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold');
      hoja.setColumnWidth(15, 140);
    }
  }

  // El periodo, el ClienteId y el CUIT como TEXTO. Si la columna queda en
  // formato fecha o numero, la proxima lectura vuelve a traer un Date --o un
  // codigo sin los ceros de la izquierda-- y el bug vuelve por la ventana.
  hoja.getRange(1, 2, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 3, hoja.getMaxRows(), 1).setNumberFormat('@');
  hoja.getRange(1, 5, hoja.getMaxRows(), 1).setNumberFormat('@');

  const ultimaFila = hoja.getLastRow();
  if (ultimaFila > 1) {
    // ⬇️ ARREGLADO. Antes era .map(function(r){ return r[0]; }), que devuelve
    // la celda CRUDA: un Date nunca es igual al texto "2026-10", asi que este
    // chequeo no frenaba nada y cada corrida agregaba otra foto del mismo mes.
    // Por eso existe limpiarHistorialScoring, para borrar a mano duplicados
    // que no deberian generarse.
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
      f.clienteId             || '',
      f.razonSocial           || '',
      f.cuit                  || '',
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

// ═══════════════════════════════════════════════════════════
// DETECTAR CANCELACIONES DE MORA
// Evita registrar la misma cancelación (mismo cliente + mismo período
// con mora) más de una vez, aunque el trigger diario la vuelva a detectar.
// ═══════════════════════════════════════════════════════════
function detectarCancelaciones(ss, filasScoring) {
  const hojaHist = ss.getSheetByName('Historial_Scoring');
  if (!hojaHist || hojaHist.getLastRow() <= 1) {
    Logger.log('Sin historial previo — no se pueden detectar cancelaciones.');
    return 0;
  }

  const ultimaFila = hojaHist.getLastRow();
  const numCols    = Math.min(hojaHist.getLastColumn(), 15);
  const datos      = hojaHist.getRange(2, 1, ultimaFila - 1, numCols).getValues();

  // ⬇️ ARREGLADO. Antes era String(fila[1]), que de la celda de fecha devuelve
  // "Wed Jul 01 2026 00:00:00 GMT-0300 (...)". Comparando ESOS textos con >,
  // "Sat Aug 01..." NO es mayor que "Wed Jul 01...": la S va antes que la W.
  // Julio ganaba para siempre, sin importar cuántos meses pasaran.
  // formatearCampoPeriodo ya existe en este archivo y devuelve "yyyy-MM".
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

  // `vistosAnt` cuenta TODOS los clientes de la foto anterior, no sólo los que
  // tenían mora. Es el denominador del freno de seguridad de más abajo.
  const estadoAnterior = {};
  const vistosAnt = {};
  datos.forEach(function(fila) {
    if (formatearCampoPeriodo(fila[1]) !== ultimoPeriodo) return;
    const vendedor = normVendedor(fila[5]);
    const empresa  = normEmpresa(fila[6]);
    if (!CFG.vendedores.includes(vendedor)) return;
    if (!CFG.empresas.includes(empresa))    return;

    // Por CUIT y no por clienteId: mismo criterio que leerHistorial.
    const cuit = claveCuit(fila[4]);
    if (!cuit) return;
    vistosAnt[cuit] = true;

    if ((Number(fila[9]) || 0) === 0) return;   // no tenía facturas vencidas

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

  // EL FRENO DE SEGURIDAD.
  //
  // "Canceló" se deduce de una AUSENCIA: el cliente ya no aparece con facturas
  // vencidas. Si la descarga de hoy vino corta --se cayó Sigma, cambió un
  // filtro, se agotó la cuota-- esa ausencia es falsa para TODOS a la vez, y se
  // registrarían cientos de cancelaciones inventadas, imposibles de distinguir
  // después de las verdaderas. Ante esa señal se aborta y se avisa.
  const clientesAntes = Object.keys(vistosAnt).length;
  if (filasScoring.length < clientesAntes * 0.5) {
    const msg = '⚠️ Hoy hay ' + filasScoring.length + ' clientes contra ' +
                clientesAntes + ' de la foto anterior. Se aborta la detección ' +
                'de cancelaciones por seguridad.';
    Logger.log(msg);
    avisar(msg);
    return -1;
  }

  const yaRegistrado = {};
  const hojaCanExistente = ss.getSheetByName('Historial_Cancelaciones');
  if (hojaCanExistente && hojaCanExistente.getLastRow() > 1) {
    // 5 columnas y no 3: el CUIT está en la quinta, y ahora es la clave.
    const datosCan = hojaCanExistente
      .getRange(2, 1, hojaCanExistente.getLastRow() - 1, 5).getValues();
    datosCan.forEach(function(fila) {
      yaRegistrado[claveCuit(fila[4]) + '|' + formatearCampoPeriodo(fila[1])] = true;
    });
  }

  const estadoActual = {};
  filasScoring.forEach(function(f) {
    const cuit = claveCuit(f.cuit);
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
    hojaCan.setColumnWidth(1, 105); hojaCan.setColumnWidth(2,  95);
    hojaCan.setColumnWidth(3,  90); hojaCan.setColumnWidth(4, 260);
    hojaCan.setColumnWidth(5, 140); hojaCan.setColumnWidth(6,  75);
    hojaCan.setColumnWidth(7,  70); hojaCan.setColumnWidth(8, 165);
    hojaCan.setColumnWidth(9, 120); hojaCan.setColumnWidth(10,140);
    hojaCan.setColumnWidth(11,155); hojaCan.setColumnWidth(12,310);
  }

  // El período, el ClienteId y el CUIT como TEXTO, por la misma razón que en
  // guardarSnapshot: si la columna queda en formato fecha, la próxima corrida
  // vuelve a leer un Date y el bug vuelve.
  hojaCan.getRange(1, 2, hojaCan.getMaxRows(), 1).setNumberFormat('@');
  hojaCan.getRange(1, 3, hojaCan.getMaxRows(), 1).setNumberFormat('@');
  hojaCan.getRange(1, 5, hojaCan.getMaxRows(), 1).setNumberFormat('@');

  const primeraFilaLibre = hojaCan.getLastRow() + 1;
  hojaCan.getRange(primeraFilaLibre, 1, cancelaciones.length, 12).setValues(cancelaciones);
  hojaCan.getRange(primeraFilaLibre, 8, cancelaciones.length, 1).setNumberFormat('$ #,##0.00');

  Logger.log('✓ Cancelaciones NUEVAS registradas: ' + cancelaciones.length);
  return cancelaciones.length;
}

// ═══════════════════════════════════════════════════════════
// SCORING POR CLIENTE
// ═══════════════════════════════════════════════════════════
function generarScoringClientes(facturas, historial) {
  const hoy      = new Date();
  const clientes = {};
  historial      = historial || {};

  facturas.forEach(function(f) {
    const key = f.clienteId || f.clienteNombre;
    if (!clientes[key]) {
      clientes[key] = {
        clienteId:        f.clienteId                  || '',
        razonSocial:     (f.clienteNombre               || '').trim(),
        cuit:            (f.clienteNumeroDocumento      || '').trim(),
        vendedor:        (f.vendedor                    || '').trim(),
        empresa:          f.empresa                     || '',
        saldoTotal: 0, saldoVencido: 0, saldoPorVencer: 0,
        cantFacturas: 0, cantVencidas: 0, cantMoraReal: 0,
        cantSobre60: 0, maxAtraso: 0, sumaAtrasosTotal: 0
      };
    }

    const c         = clientes[key];
    const fechaFact = parsearFecha(f.fecha);
    const plazo     = parseInt(f.plazo) || 0;
    const venc      = new Date(fechaFact.getTime());
    venc.setDate(venc.getDate() + plazo);
    const atraso    = Math.floor((hoy.getTime() - venc.getTime()) / 86400000);
    const pendiente = Number(f.pendientePago) || 0;

    c.saldoTotal       += pendiente;
    c.cantFacturas++;
    c.sumaAtrasosTotal += Math.max(0, atraso);
    if (atraso > c.maxAtraso) c.maxAtraso = atraso;
    if (atraso > 0) { c.saldoVencido += pendiente; c.cantVencidas++; }
    else              c.saldoPorVencer += pendiente;
    if (atraso > 30) c.cantMoraReal++;
    if (atraso > 60) c.cantSobre60++;
  });

  const filas = Object.values(clientes).map(function(c) {
    const promedioAtrasoGeneral = c.cantFacturas > 0
      ? Math.round(c.sumaAtrasosTotal / c.cantFacturas) : 0;
    const pctExposicion = c.saldoTotal > 0 ? c.saldoVencido / c.saldoTotal : 0;

    // CLASIFICACIÓN — cortes en 0 / 15 / 30 días de atraso, cruzados con el
    // 60 % de exposición. Es el criterio unificado con la planilla original.
    //
    //   BUENO        -> 0 facturas vencidas
    //   AL DÍA       -> atraso <= 15 días (tolerancia interna)
    //   OBSERVACIÓN  -> atraso 16-30 días, exposición <= 60 %
    //   RIESGOSO     -> atraso 16-30 días, exposición > 60 %
    //                   O atraso > 30 días, exposición <= 60 %
    //   CRÍTICO      -> atraso > 30 días, exposición > 60 %
    //
    // OJO: la tolerancia se evalúa ANTES que la exposición, así que un cliente
    // con 3 días de atraso queda AL DÍA aunque deba el 100 % del saldo.
    var categoria;
    if (c.cantVencidas === 0) {
      categoria = 'BUENO';
    } else if (c.maxAtraso <= 15) {
      categoria = 'AL DÍA';
    } else if (c.maxAtraso <= 30) {
      categoria = (pctExposicion > 0.60) ? 'RIESGOSO' : 'OBSERVACIÓN';
    } else {
      categoria = (pctExposicion > 0.60) ? 'CRÍTICO' : 'RIESGOSO';
    }

    // Por CUIT y no por clienteId: es la clave con la que leerHistorial arma
    // el indice. Antes buscaba por clienteId y no encontraba nada --los 118
    // clientes decian "Sin historial" teniendo cuatro meses de fotos.
    const hist = historial[claveCuit(c.cuit)] || null;
    const mesesEnMoraHist   = hist ? hist.mesesEnMora   : '-';
    const peorCategoriaHist = hist ? hist.peorCategoria : 'Sin historial';
    var tendencia = 'Sin historial';
    if (hist && hist.categoriaAnterior) {
      const pa = PESO[categoria] || 0;
      const pp = PESO[hist.categoriaAnterior] || 0;
      if      (pa > pp) tendencia = '↓ Empeora';
      else if (pa < pp) tendencia = '↑ Mejora';
      else              tendencia = '→ Estable';
    }

    return {
      categoria, vendedor: c.vendedor, razonSocial: c.razonSocial,
      cuit: c.cuit, empresa: c.empresa, clienteId: c.clienteId,
      saldoTotal: c.saldoTotal, saldoVencido: c.saldoVencido,
      saldoPorVencer: c.saldoPorVencer, pctExposicion,
      cantFacturas: c.cantFacturas, cantVencidas: c.cantVencidas,
      cantMoraReal: c.cantMoraReal, maxAtraso: c.maxAtraso,
      promedioAtrasoGeneral, mesesEnMoraHist, peorCategoriaHist, tendencia
    };
  });

  const ORDEN = { 'CRÍTICO': 0, 'RIESGOSO': 1, 'OBSERVACIÓN': 2, 'AL DÍA': 3, 'BUENO': 4 };
  filas.sort(function(a, b) {
    const diff = (ORDEN[a.categoria] || 0) - (ORDEN[b.categoria] || 0);
    if (diff !== 0) return diff;
    return b.saldoVencido - a.saldoVencido;
  });
  return filas;
}

// ═══════════════════════════════════════════════════════════
// ESCRIBIR HOJA SCORING
// ═══════════════════════════════════════════════════════════
function escribirHojaScoring(ss, filas) {
  const NOMBRE = 'Scoring_Clientes';
  var hoja = ss.getSheetByName(NOMBRE);
  if (!hoja) { hoja = ss.insertSheet(NOMBRE); }
  else { hoja.clearContents(); hoja.clearConditionalFormatRules(); hoja.clearFormats(); }

  const ahora = Utilities.formatDate(new Date(), 'GMT-3', "dd/MM/yyyy 'a las' HH:mm");
  hoja.getRange(1, 1, 1, 17).merge();
  hoja.getRange(1, 1)
      .setValue('Scoring de riesgo | Actualizado: ' + ahora + ' | Empresas: ' + CFG.empresas.join(', ') + ' | Vendedores: ' + CFG.vendedores.join(', '))
      .setFontStyle('italic').setFontColor('#555555').setFontSize(9);

  const HEADERS = [
    'Categoría', 'Vendedor', 'Razón Social', 'CUIT', 'Empresa',
    'Saldo Total ($)', 'Saldo Vencido ($)', 'Saldo por Vencer ($)', '% Exposición',
    'Fact. Pendientes', 'Fact. Vencidas', 'Fact. > 30 días',
    'Atraso Máx (días)', 'Atraso Prom General (días)',
    'Meses en mora (últ. 6)', 'Peor categoría (últ. 6)', 'Tendencia'
  ];
  hoja.getRange(2, 1, 1, HEADERS.length)
      .setValues([HEADERS])
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(10);
  hoja.setFrozenRows(2);

  if (filas.length === 0) { hoja.getRange(3, 1).setValue('Sin clientes.'); return; }

  const datos = filas.map(function(f) {
    return [
      f.categoria, f.vendedor, f.razonSocial, f.cuit, f.empresa,
      f.saldoTotal, f.saldoVencido, f.saldoPorVencer, f.pctExposicion,
      f.cantFacturas, f.cantVencidas, f.cantMoraReal,
      f.maxAtraso, f.promedioAtrasoGeneral,
      f.mesesEnMoraHist, f.peorCategoriaHist, f.tendencia
    ];
  });

  // Vendedor (col 2) y Empresa (col 5) como TEXTO: si no, Sheets convierte
  // '006' en 6 y '0001' en 1, y después no cruzan con nada.
  hoja.getRange(3, 2, datos.length, 1).setNumberFormat('@');
  hoja.getRange(3, 5, datos.length, 1).setNumberFormat('@');
  hoja.getRange(3, 1, datos.length, HEADERS.length).setValues(datos);
  hoja.getRange(3, 6, datos.length, 3).setNumberFormat('$ #,##0.00');
  hoja.getRange(3, 9, datos.length, 1).setNumberFormat('0.00%');

  const rC = hoja.getRange(3, 1, datos.length, 1);
  const rE = hoja.getRange(3, 9, datos.length, 1);
  const rT = hoja.getRange(3, 17, datos.length, 1);
  const reglas = [];
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('CRÍTICO').setBackground('#F5C6C6').setFontColor('#B03A3A').setBold(true).setRanges([rC]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('RIESGOSO').setBackground('#FFDEC8').setFontColor('#8B4513').setBold(true).setRanges([rC]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('OBSERVACIÓN').setBackground('#FCE4B0').setFontColor('#7D5A00').setRanges([rC]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('BUENO').setBackground('#D4EDDA').setFontColor('#1E6B3C').setRanges([rC]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('AL DÍA').setBackground('#D5E8F0').setFontColor('#1F3A5F').setRanges([rC]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThan(0.60).setBackground('#F5C6C6').setFontColor('#B03A3A').setBold(true).setRanges([rE]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThan(0.30).setBackground('#FCE4B0').setFontColor('#7D5A00').setRanges([rE]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextContains('Empeora').setFontColor('#B03A3A').setBold(true).setRanges([rT]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextContains('Mejora').setFontColor('#1E6B3C').setBold(true).setRanges([rT]).build());
  hoja.setConditionalFormatRules(reglas);

  hoja.setColumnWidth(1,  120); hoja.setColumnWidth(2,   80);
  hoja.setColumnWidth(3,  260); hoja.setColumnWidth(4,  140);
  hoja.setColumnWidth(5,   70); hoja.setColumnWidth(6,  140);
  hoja.setColumnWidth(7,  140); hoja.setColumnWidth(8,  140);
  hoja.setColumnWidth(9,  100); hoja.setColumnWidth(10,  85);
  hoja.setColumnWidth(11,  85); hoja.setColumnWidth(12,  85);
  hoja.setColumnWidth(13, 105); hoja.setColumnWidth(14, 160);
  hoja.setColumnWidth(15, 135); hoja.setColumnWidth(16, 165);
  hoja.setColumnWidth(17, 115);

  Logger.log('✓ Scoring escrito: ' + filas.length + ' clientes.');
}

// ═══════════════════════════════════════════════════════════
// AGING DETALLADO
// ═══════════════════════════════════════════════════════════
function construirFilas(facturas, recibos) {
  const hoy = new Date();
  const filas = [];

  facturas.forEach(function(f) {
    const fechaFact   = parsearFecha(f.fecha);
    const plazo       = parseInt(f.plazo) || 0;
    const vencimiento = new Date(fechaFact.getTime());
    vencimiento.setDate(vencimiento.getDate() + plazo);
    const atraso = Math.floor((hoy.getTime() - vencimiento.getTime()) / 86400000);
    filas.push({
      vendedor:    (f.vendedor              || '').trim(),
      razonSocial: (f.clienteNombre          || '').trim(),
      cuit:        (f.clienteNumeroDocumento || '').trim(),
      direccion:   (f.clienteDireccion       || '').trim(),
      comprobante: [(f.comprobanteCodigo || ''), (f.comprobanteNumero || '')].filter(Boolean).join('-'),
      fecha:       formatearFecha(fechaFact),
      vencimiento: formatearFecha(vencimiento),
      atraso:      atraso,
      total:       Number(f.totalComprobante) || 0,
      pendiente:   Number(f.pendientePago)    || 0,
      empresa:     f.empresa || '',
      observacion: ''
    });
  });

  recibos.forEach(function(r) {
    const pendiente = Number(r.pendienteAplicacion) || 0;
    if (pendiente === 0) return;
    filas.push({
      vendedor: '', razonSocial: (r.clienteNombre || '').trim(),
      cuit: '', direccion: '',
      comprobante: [(r.comprobanteCodigo || ''), (r.comprobanteNumero || '')].filter(Boolean).join('-'),
      fecha:     formatearFecha(parsearFecha(r.fecha)),
      vencimiento: '', atraso: '',
      total:    -(Number(r.totalAcuenta)        || 0),
      pendiente: -(Number(r.pendienteAplicacion) || 0),
      empresa: r.empresa || '', observacion: r.observacion || ''
    });
  });

  filas.sort(function(a, b) {
    const byVend = a.vendedor.localeCompare(b.vendedor, 'es');
    if (byVend !== 0) return byVend;
    const byEmp = a.empresa.localeCompare(b.empresa);
    if (byEmp !== 0) return byEmp;
    const aA = typeof a.atraso === 'number' ? a.atraso : -9999;
    const aB = typeof b.atraso === 'number' ? b.atraso : -9999;
    return aB - aA;
  });
  return filas;
}

function escribirHoja(ss, filas) {
  const NOMBRE_HOJA = 'Aging_CC_0001_0003';
  var hoja = ss.getSheetByName(NOMBRE_HOJA);
  if (!hoja) { hoja = ss.insertSheet(NOMBRE_HOJA); }
  else { hoja.clearContents(); hoja.clearConditionalFormatRules(); hoja.clearFormats(); }

  const ahora = Utilities.formatDate(new Date(), 'GMT-3', "dd/MM/yyyy 'a las' HH:mm");
  hoja.getRange(1, 1, 1, 12).merge();
  hoja.getRange(1, 1)
      .setValue('Última actualización: ' + ahora + ' | Empresas: ' + CFG.empresas.join(', ') + ' | Vendedores: ' + CFG.vendedores.join(', '))
      .setFontStyle('italic').setFontColor('#555555').setFontSize(9);

  const HEADERS = [
    'Vendedor', 'Razón Social', 'CUIT', 'Dirección',
    'Comprobante', 'Fecha', 'Vencimiento', 'Atraso (días)',
    'Total', 'Pendiente', 'Empresa', 'Observaciones'
  ];
  hoja.getRange(2, 1, 1, HEADERS.length)
      .setValues([HEADERS])
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(10);
  hoja.setFrozenRows(2);

  if (filas.length === 0) { hoja.getRange(3, 1).setValue('Sin comprobantes pendientes.'); return; }

  const datos = [];
  const filasSubtotal = [];
  let vendedorActual = null, sumaTotal = 0, sumaPendiente = 0, filaActual = 3;

  filas.forEach(function(f) {
    if (vendedorActual !== null && f.vendedor !== vendedorActual) {
      datos.push(['↳ ' + (vendedorActual || '(sin vendedor)'), 'SUBTOTAL', '', '', '', '', '', '', sumaTotal, sumaPendiente, '', '']);
      filasSubtotal.push(filaActual++);
      sumaTotal = 0; sumaPendiente = 0;
    }
    vendedorActual  = f.vendedor;
    sumaTotal      += typeof f.total     === 'number' ? f.total     : 0;
    sumaPendiente  += typeof f.pendiente === 'number' ? f.pendiente : 0;
    datos.push([f.vendedor, f.razonSocial, f.cuit, f.direccion,
                f.comprobante, f.fecha, f.vencimiento, f.atraso,
                f.total, f.pendiente, f.empresa, f.observacion]);
    filaActual++;
  });

  if (vendedorActual !== null) {
    datos.push(['↳ ' + (vendedorActual || '(sin vendedor)'), 'SUBTOTAL', '', '', '', '', '', '', sumaTotal, sumaPendiente, '', '']);
    filasSubtotal.push(filaActual);
  }

  // Vendedor (col 1) y Empresa (col 11) como TEXTO, mismo motivo que en Scoring.
  hoja.getRange(3, 1,  datos.length, 1).setNumberFormat('@');
  hoja.getRange(3, 11, datos.length, 1).setNumberFormat('@');
  hoja.getRange(3, 1, datos.length, HEADERS.length).setValues(datos);
  hoja.getRange(3, 9, datos.length, 2).setNumberFormat('#,##0.00');

  const rangoAtraso = hoja.getRange(3, 8, datos.length, 1);
  const reglas = [];
  // Semáforo alineado a los cortes de la clasificación (0 / 15 / 30). Los dos
  // verdes/azules van al revés que antes a propósito: sin atraso es BUENO
  // (verde) y hasta 15 días es AL DÍA (azul), igual que las categorías.
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThan(30).setBackground('#F5C6C6').setFontColor('#B03A3A').setBold(true).setRanges([rangoAtraso]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberBetween(16, 30).setBackground('#FCE4B0').setFontColor('#7D5A00').setRanges([rangoAtraso]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberBetween(1, 15).setBackground('#D5E8F0').setFontColor('#1F3A5F').setRanges([rangoAtraso]).build());
  reglas.push(SpreadsheetApp.newConditionalFormatRule().whenNumberLessThanOrEqualTo(0).setBackground('#D4EDDA').setFontColor('#1E6B3C').setRanges([rangoAtraso]).build());
  hoja.setConditionalFormatRules(reglas);

  filasSubtotal.forEach(function(fila) {
    hoja.getRange(fila, 1, 1, HEADERS.length).setBackground('#2E75B6').setFontColor('#FFFFFF').setFontWeight('bold');
  });

  hoja.setColumnWidth(1,  90); hoja.setColumnWidth(2,  260);
  hoja.setColumnWidth(3, 140); hoja.setColumnWidth(4,  210);
  hoja.setColumnWidth(5, 165); hoja.setColumnWidth(6,   90);
  hoja.setColumnWidth(7,  95); hoja.setColumnWidth(8,   80);
  hoja.setColumnWidth(9, 120); hoja.setColumnWidth(10, 120);
  hoja.setColumnWidth(11, 70); hoja.setColumnWidth(12, 200);

  Logger.log('✓ Aging escrito: ' + filas.length + ' comprobantes.');
}

// ═══════════════════════════════════════════════════════════
// VENTAS DEL MES POR VENDEDOR — datos crudos, sin fórmulas
// ═══════════════════════════════════════════════════════════
function generarVentasVendedores() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  try {
    const hoy = new Date();
    const primerDiaMesAnterior = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
    const ultimoDiaMesAnterior = new Date(hoy.getFullYear(), hoy.getMonth(), 0);

    const dde = Utilities.formatDate(primerDiaMesAnterior, 'GMT-3', 'yyyy-MM-dd');
    const hta = Utilities.formatDate(ultimoDiaMesAnterior,  'GMT-3', 'yyyy-MM-dd');

    Logger.log('→ Descargando ventas del período ' + dde + ' a ' + hta + ' (todos los estados)...');

    const todasFacturas = llamarAPIPaginada('ExportFacturas', { dde: dde, hta: hta });
    const facturas = todasFacturas.filter(f =>
      CFG.empresas.includes(f.empresa) && CFG.vendedores.includes(f.vendedor)
    );
    Logger.log('← Facturas del período filtradas: ' + facturas.length);

    facturas.sort(function(a, b) {
      const byVend = String(a.vendedor).localeCompare(String(b.vendedor), 'es');
      if (byVend !== 0) return byVend;
      return String(a.fecha).localeCompare(String(b.fecha));
    });

    escribirHojaVentas(ss, facturas, dde, hta);

    avisar('✅ Ventas del período generadas\n' +
      'Período: ' + dde + ' a ' + hta +
      '\nComprobantes descargados: ' + facturas.length +
      '\n\n📌 Revisá la columna "Ajusta a" (resaltada en amarillo): son Notas de Crédito/Débito.');

  } catch (e) {
    Logger.log('✖ ERROR: ' + e.message + '\n' + e.stack);
    avisar('❌ Error\n' + e.message);
    throw e;
  }
}

function escribirHojaVentas(ss, facturas, dde, hta) {
  const NOMBRE = 'Ventas_Vendedores_0001_0003';
  var hoja = ss.getSheetByName(NOMBRE);
  if (!hoja) {
    hoja = ss.insertSheet(NOMBRE);
  } else {
    hoja.clearContents();
    hoja.clearConditionalFormatRules();
    hoja.clearFormats();
  }

  const ahora = Utilities.formatDate(new Date(), 'GMT-3', "dd/MM/yyyy 'a las' HH:mm");
  hoja.getRange(1, 1, 1, 13).merge();
  hoja.getRange(1, 1)
      .setValue('Ventas del período ' + dde + ' a ' + hta + ' | Generado: ' + ahora + ' | Vendedores: ' + CFG.vendedores.join(', ') + ' | Empresas: ' + CFG.empresas.join(', '))
      .setFontStyle('italic').setFontColor('#555555').setFontSize(9);

  const HEADERS = [
    'Vendedor', 'Empresa', 'Fecha', 'Comprobante', 'Tipo',
    'Ajusta a (comprobante)', 'Motivo NC', 'Cliente', 'CUIT',
    'Subtotal (neto imp.)', 'Total (bruto)', 'Estado', 'Pendiente ($)'
  ];
  hoja.getRange(2, 1, 1, HEADERS.length)
      .setValues([HEADERS])
      .setBackground('#1F3A5F').setFontColor('#FFFFFF')
      .setFontWeight('bold').setFontSize(10);
  hoja.setFrozenRows(2);

  if (facturas.length === 0) {
    hoja.getRange(3, 1).setValue('Sin facturas en el período seleccionado.');
    return;
  }

  const datos = facturas.map(function(f) {
    return [
      f.vendedor || '',
      f.empresa  || '',
      formatearFecha(parsearFecha(f.fecha)),
      [(f.comprobanteCodigo||''), (f.comprobanteNumero||'')].filter(Boolean).join('-'),
      f.comprobanteTipo || '',
      f.ajustaComprobanteId || '',
      f.motivoNc || '',
      (f.clienteNombre || '').trim(),
      (f.clienteNumeroDocumento || '').trim(),
      Number(f.subtotal) || 0,
      Number(f.totalComprobante) || 0,
      f.estado || '',
      Number(f.pendientePago) || 0
    ];
  });

  hoja.getRange(3, 1, datos.length, 2).setNumberFormat('@');
  hoja.getRange(3, 1, datos.length, HEADERS.length).setValues(datos);
  hoja.getRange(3, 10, datos.length, 2).setNumberFormat('$ #,##0.00');
  hoja.getRange(3, 13, datos.length, 1).setNumberFormat('$ #,##0.00');

  const rangoAjusta = hoja.getRange(3, 6, datos.length, 1);
  const regla = SpreadsheetApp.newConditionalFormatRule()
    .whenCellNotEmpty()
    .setBackground('#FFF3CD').setFontColor('#7D5A00').setBold(true)
    .setRanges([rangoAjusta]).build();
  hoja.setConditionalFormatRules([regla]);

  hoja.setColumnWidth(1, 80);  hoja.setColumnWidth(2, 70);
  hoja.setColumnWidth(3, 90);  hoja.setColumnWidth(4, 130);
  hoja.setColumnWidth(5, 90);  hoja.setColumnWidth(6, 130);
  hoja.setColumnWidth(7, 160); hoja.setColumnWidth(8, 240);
  hoja.setColumnWidth(9, 140); hoja.setColumnWidth(10, 120);
  hoja.setColumnWidth(11, 120);hoja.setColumnWidth(12, 90);
  hoja.setColumnWidth(13, 110);

  Logger.log('✓ Hoja de ventas escrita: ' + datos.length + ' comprobantes.');
}

// ═══════════════════════════════════════════════════════════
// LLAMADAS A LA API
// ═══════════════════════════════════════════════════════════
function llamarAPIPaginada(endpoint, params) {
  const resultados = [];
  var offset = 0;
  while (true) {
    var paramsPag = Object.assign({}, params, { offset: offset, limit: CFG.pageSize });
    var respuesta = llamarAPI(endpoint, paramsPag);
    if (!Array.isArray(respuesta) || respuesta.length === 0) break;
    resultados.push.apply(resultados, respuesta);
    if (respuesta.length < CFG.pageSize) break;
    offset += respuesta.length;
    Utilities.sleep(400);
  }
  return resultados;
}

function llamarAPI(endpoint, params) {
  var url = CFG.baseUrl + endpoint;
  var queryParts = [];
  Object.keys(params).forEach(function(k) {
    var v = params[k];
    if (v !== undefined && v !== null && v !== '') {
      queryParts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    }
  });
  if (queryParts.length > 0) url += '?' + queryParts.join('&');
  var opciones = { method: 'GET', headers: { 'X-Auth-Token': sigmaToken_() }, muteHttpExceptions: true };
  var intentos = 0;
  while (intentos < 4) {
    var resp   = UrlFetchApp.fetch(url, opciones);
    var codigo = resp.getResponseCode();
    if (codigo === 200) {
      try { return JSON.parse(resp.getContentText('UTF-8')); }
      catch (e) { throw new Error('JSON inválido de ' + endpoint); }
    }
    if (codigo === 429) {
      var espera = parseInt((resp.getHeaders()['X-Retry-After-ms'] || '3000'), 10);
      Utilities.sleep(espera + 500);
      intentos++;
      continue;
    }
    throw new Error('HTTP ' + codigo + ' en ' + endpoint + ': ' + resp.getContentText('UTF-8').substring(0, 300));
  }
  throw new Error('Máximo de reintentos agotado para: ' + endpoint);
}

// ═══════════════════════════════════════════════════════════
// UTILIDADES
// ═══════════════════════════════════════════════════════════
function parsearFecha(str) {
  if (!str) return new Date();
  return new Date(str.substring(0, 10));
}

function formatearFecha(fecha) {
  if (!fecha || !(fecha instanceof Date) || isNaN(fecha.getTime())) return '';
  return Utilities.formatDate(fecha, 'GMT-3', 'dd/MM/yyyy');
}

// Sheets a veces autoconvierte texto tipo "29/04/2025" a un objeto Date real,
// aunque el script lo haya escrito como string. Este helper maneja ambos casos.
function formatearCampoFecha(valor) {
  if (valor instanceof Date) {
    return Utilities.formatDate(valor, 'GMT-3', 'dd/MM/yyyy');
  }
  return String(valor || '');
}

// Mismo problema, pero para "Periodo" (yyyy-MM): Sheets autoconvierte "2026-08"
// a un Date real, y String() sobre ese Date da un formato en inglés con timezone
// que puede variar entre lecturas, generando 2 strings distintos para el MISMO
// período (bug visto en el gráfico "saldo_vencido por periodo").
function formatearCampoPeriodo(valor) {
  if (valor instanceof Date) {
    return Utilities.formatDate(valor, 'GMT-3', 'yyyy-MM');
  }
  return String(valor || '').trim();
}

// ═══════════════════════════════════════════════════════════
// TRIGGER DIARIO COMBINADO
//
// Cada paso va en su propio try/catch para que uno que falle no se lleve
// puestos a los que siguen. Al final resume qué anduvo y qué no.
// ═══════════════════════════════════════════════════════════
function procesoDiarioCompleto() {
  Logger.log('=== INICIO proceso diario completo ===');
  const errores = [];

  try { actualizarDiario(); }
  catch (e) { errores.push('actualizarDiario: ' + e.message); }

  try { guardarFotoMensual(); }   // no hace nada si ya se guardó este mes
  catch (e) { errores.push('guardarFotoMensual: ' + e.message); }

  try { guardarFotoDiaria(); }    // esta sí guarda todos los días
  catch (e) { errores.push('guardarFotoDiaria: ' + e.message); }

  try { subirTodoCtaCteASupabase(); }
  catch (e) { errores.push('subirTodoCtaCteASupabase: ' + e.message); }

  if (errores.length > 0) {
    Logger.log('=== FIN con ' + errores.length + ' error(es) ===\n' + errores.join('\n'));
    throw new Error('Proceso diario con errores:\n' + errores.join('\n'));
  }
  Logger.log('=== FIN proceso diario completo — sin errores ===');
}

// ═══════════════════════════════════════════════════════════
// FOTO DIARIA (separada del cierre mensual) — solo para el gráfico de
// tendencia día a día. NO afecta meses_en_mora_hist, que se sigue
// calculando exclusivamente desde el cierre mensual.
// ═══════════════════════════════════════════════════════════
function guardarFotoDiaria() {
  const hoyStr = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM-dd');
  Logger.log('→ [FOTO DIARIA] Fecha: ' + hoyStr);
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const { facturas } = descargarDatos(hoyStr);
  const historial = leerHistorial(ss);
  const filasScoring = generarScoringClientes(facturas, historial);
  const guardado = guardarSnapshotDiario(ss, filasScoring);
  Logger.log(guardado
    ? '✓ Foto diaria guardada: ' + filasScoring.length + ' clientes.'
    : 'Foto diaria de hoy ya existía, no se duplicó.');
}

function guardarSnapshotDiario(ss, filasScoring) {
  const fechaActual = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM-dd');
  const NOMBRE = 'Historial_Diario';

  var hoja = ss.getSheetByName(NOMBRE);
  if (!hoja) {
    hoja = ss.insertSheet(NOMBRE);
    const HEADERS_D = [
      'Fecha', 'ClienteId', 'Razón Social', 'CUIT',
      'Vendedor', 'Empresa', 'Saldo Total', 'Fact. Pendientes',
      'Fact. Vencidas', 'Fact. > 30 días', 'Atraso Máx',
      'Atraso Prom General', 'Categoría', 'Saldo Vencido'
    ];
    hoja.getRange(1, 1, 1, HEADERS_D.length)
        .setValues([HEADERS_D])
        .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold');
    hoja.setFrozenRows(1);
    hoja.getRange(1, 5, 5000, 2).setNumberFormat('@');  // Vendedor y Empresa como texto
  }

  const ultimaFila = hoja.getLastRow();
  if (ultimaFila > 1) {
    const fechas = hoja.getRange(2, 1, ultimaFila - 1, 1).getValues().map(function(r) { return r[0]; });
    const fechasStr = fechas.map(function(f) {
      return (f instanceof Date) ? Utilities.formatDate(f, 'GMT-3', 'yyyy-MM-dd') : String(f);
    });
    if (fechasStr.indexOf(fechaActual) !== -1) {
      return false; // ya se guardó hoy, no duplicar
    }
  }

  const filasNuevas = filasScoring.map(function(f) {
    return [
      fechaActual,
      f.clienteId             || '',
      f.razonSocial           || '',
      f.cuit                  || '',
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
  hoja.getRange(primeraFilaLibre, 1, filasNuevas.length, 14).setValues(filasNuevas);

  return true;
}

// ═══════════════════════════════════════════════════════════
// MENÚ
// ═══════════════════════════════════════════════════════════
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📊 Sigma CC')
    .addItem('🔌 Probar conexión', 'probarConexion')
    .addSeparator()
    .addItem('🔄 Actualizar datos del día (Aging + Scoring)', 'actualizarDiario')
    .addItem('📅 Guardar cierre mensual (Historial)', 'guardarFotoMensual')
    .addSeparator()
    .addItem('💰 Generar Ventas del Mes (Rendimiento Vendedores)', 'generarVentasVendedores')
    .addSeparator()
    .addItem('📊 Crear/Recrear Índices Financieros', 'crearHojaIndices')
    .addSeparator()
    .addItem('☁️ Subir TODO a Supabase (Scoring+Historial+Cancelaciones+Aging)', 'subirTodoCtaCteASupabase')
    .addSeparator()
    .addItem('🧪 Simular corrida del trigger (sin pantalla)', 'procesoDiarioCompleto')
    .addToUi();
}

// ═══════════════════════════════════════════════════════════
// HELPER COMPARTIDO: sube un lote de filas a una tabla de Supabase
//
// DOS COSAS IMPORTANTES:
//
// 1) Todas las filas del lote llevan la MISMA fecha_carga. Antes cada fila
//    tenía su propio new Date(), y por eso aging terminaba con 250 timestamps
//    distintos para 252 filas: imposible saber de un vistazo cuándo fue la
//    última corrida.
//
// 2) Al final borra las filas que quedaron de corridas anteriores. El upsert
//    por on_conflict actualiza lo que vuelve a venir, pero NUNCA borra lo que
//    dejó de venir: por eso quedaban clientes que ya habían cancelado sumando
//    deuda fantasma. El borrado solo corre si TODOS los lotes subieron bien,
//    para no borrar datos buenos por una subida a medias.
// ═══════════════════════════════════════════════════════════
function subirLoteASupabase(filas, nombreTabla, onConflict, nombreOrigen) {
  if (filas.length === 0) {
    avisar('No hay filas válidas para subir desde ' + nombreOrigen + '.');
    return false;
  }

  const url = PropertiesService.getScriptProperties().getProperty('SUPABASE_URL');
  const serviceKey = PropertiesService.getScriptProperties().getProperty('SUPABASE_SERVICE_KEY');
  if (!url || !serviceKey) {
    avisar('Faltan las Script Properties SUPABASE_URL y/o SUPABASE_SERVICE_KEY.\n' +
           'Agregalas en: Configuración del proyecto > Propiedades del script.\n' +
           '(Usá la key LEGACY tipo JWT, no la nueva sb_secret_...)');
    return false;
  }

  // Una sola marca de tiempo para toda la corrida.
  const marcaCorrida = new Date().toISOString();
  filas.forEach(function(f) { f.fecha_carga = marcaCorrida; });

  const cabeceras = {
    'apikey': serviceKey,
    'Authorization': 'Bearer ' + serviceKey,
    'Content-Profile': 'bronze',
    'Prefer': 'resolution=merge-duplicates,return=minimal'
  };

  const endpoint = url + '/rest/v1/' + nombreTabla + '?on_conflict=' + onConflict;
  const LOTE = 500;
  let subidos = 0;

  for (let i = 0; i < filas.length; i += LOTE) {
    const lote = filas.slice(i, i + LOTE);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: 'post',
      contentType: 'application/json',
      headers: cabeceras,
      payload: JSON.stringify(lote),
      muteHttpExceptions: true
    });

    if (resp.getResponseCode() >= 200 && resp.getResponseCode() < 300) {
      subidos += lote.length;
    } else {
      const detalle = resp.getContentText().substring(0, 300);
      Logger.log('Error subiendo lote de ' + nombreOrigen + ': ' + detalle);
      avisar('❌ Error al subir ' + nombreOrigen + '.\n' +
             'Se subieron ' + subidos + ' de ' + filas.length + ' antes de fallar.\n' + detalle);
      return false;   // sin borrado: la tabla queda como estaba + lo que entró
    }
  }

  // Todo subió bien: ahora sí, borramos lo que no vino en esta corrida.
  const filtroViejas = '?or=(fecha_carga.lt.' + encodeURIComponent(marcaCorrida) + ',fecha_carga.is.null)';
  const respBorrado = UrlFetchApp.fetch(url + '/rest/v1/' + nombreTabla + filtroViejas, {
    method: 'delete',
    headers: {
      'apikey': serviceKey,
      'Authorization': 'Bearer ' + serviceKey,
      'Content-Profile': 'bronze',
      'Prefer': 'return=minimal'
    },
    muteHttpExceptions: true
  });

  if (respBorrado.getResponseCode() >= 200 && respBorrado.getResponseCode() < 300) {
    Logger.log('✓ ' + nombreOrigen + ': ' + subidos + ' filas subidas, sobrantes borradas.');
  } else {
    // No es fatal: los datos nuevos ya están. Solo quedaron filas viejas.
    Logger.log('⚠ ' + nombreOrigen + ': subieron ' + subidos + ' filas, pero falló el borrado de sobrantes: ' +
               respBorrado.getContentText().substring(0, 300));
  }

  avisar('✅ ' + nombreOrigen + ': subidas ' + subidos + ' filas a Supabase.');
  return true;
}

// ═══════════════════════════════════════════════════════════
// SUBIDAS A SUPABASE
// ═══════════════════════════════════════════════════════════
function subirTodoCtaCteASupabase() {
  const resultados = [];
  const pasos = [
    ['Scoring',                subirScoringASupabase],
    ['Historial_Scoring',      subirHistorialScoringASupabase],
    ['Historial_Diario',       subirHistorialDiarioASupabase],
    ['Historial_Cancelaciones', subirCancelacionesASupabase],
    ['Aging',                  subirAgingASupabase]
  ];

  // Cada paso en su propio try/catch: que uno falle no puede impedir los otros.
  // Antes, el alert de éxito del primero tiraba excepción bajo el trigger y los
  // cuatro siguientes no se ejecutaban nunca.
  pasos.forEach(function(paso) {
    const nombre = paso[0], fn = paso[1];
    try {
      fn();
      resultados.push('✓ ' + nombre);
    } catch (e) {
      Logger.log('✖ ' + nombre + ': ' + e.message + '\n' + e.stack);
      resultados.push('✖ ' + nombre + ': ' + e.message);
    }
  });

  Logger.log('Resumen de subida a Supabase:\n' + resultados.join('\n'));
}

// ===== SCORING =====
function subirScoringASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName('Scoring_Clientes');
  if (!hoja || hoja.getLastRow() <= 2) {
    avisar("No encontré datos en la hoja 'Scoring_Clientes'.");
    return;
  }

  const datos = hoja.getDataRange().getValues();
  // Fila 1 = título (merged), fila 2 = encabezados, datos desde fila 3 (índice 2)
  const HEADERS = datos[1];
  const idx = {
    categoria:         HEADERS.indexOf('Categoría'),
    vendedor:          HEADERS.indexOf('Vendedor'),
    razonSocial:       HEADERS.indexOf('Razón Social'),
    cuit:              HEADERS.indexOf('CUIT'),
    empresa:           HEADERS.indexOf('Empresa'),
    saldoTotal:        HEADERS.indexOf('Saldo Total ($)'),
    saldoVencido:      HEADERS.indexOf('Saldo Vencido ($)'),
    saldoPorVencer:    HEADERS.indexOf('Saldo por Vencer ($)'),
    pctExposicion:     HEADERS.indexOf('% Exposición'),
    factPendientes:    HEADERS.indexOf('Fact. Pendientes'),
    factVencidas:      HEADERS.indexOf('Fact. Vencidas'),
    factMoraReal:      HEADERS.indexOf('Fact. > 30 días'),
    atrasoMax:         HEADERS.indexOf('Atraso Máx (días)'),
    atrasoPromGeneral: HEADERS.indexOf('Atraso Prom General (días)'),
    mesesEnMoraHist:   HEADERS.indexOf('Meses en mora (últ. 6)'),
    peorCategoriaHist: HEADERS.indexOf('Peor categoría (últ. 6)'),
    tendencia:         HEADERS.indexOf('Tendencia'),
  };
  verificarEncabezados(idx, 'Scoring_Clientes');

  const filasPorCuit = {}; // dedup por CUIT: se queda con la última fila
  for (let i = 2; i < datos.length; i++) {
    const fila = datos[i];
    const cuit = String(fila[idx.cuit] || '').trim();
    if (!cuit) continue; // sin CUIT no se puede identificar al cliente

    filasPorCuit[cuit] = {
      cuit: cuit,
      categoria: fila[idx.categoria] || null,
      vendedor: normalizarCodigo(fila[idx.vendedor], 3),
      razon_social: fila[idx.razonSocial] || null,
      empresa: String(fila[idx.empresa] || ''),
      saldo_total: Number(fila[idx.saldoTotal]) || 0,
      saldo_vencido: Number(fila[idx.saldoVencido]) || 0,
      saldo_por_vencer: Number(fila[idx.saldoPorVencer]) || 0,
      pct_exposicion: Number(fila[idx.pctExposicion]) || 0,
      fact_pendientes: Number(fila[idx.factPendientes]) || 0,
      fact_vencidas: Number(fila[idx.factVencidas]) || 0,
      // La columna de Supabase sigue llamándose fact_mayor_45 por
      // compatibilidad, pero desde el cambio de criterio cuenta las
      // facturas con MÁS DE 30 días de atraso. Si se la renombra a
      // fact_mayor_30 hay que cambiar también esta línea.
      fact_mayor_45: Number(fila[idx.factMoraReal]) || 0,
      atraso_max: Number(fila[idx.atrasoMax]) || 0,
      atraso_prom_general: Number(fila[idx.atrasoPromGeneral]) || 0,
      meses_en_mora_hist: String(fila[idx.mesesEnMoraHist] || ''),
      peor_categoria_hist: String(fila[idx.peorCategoriaHist] || ''),
      tendencia: String(fila[idx.tendencia] || ''),
    };
  }
  const filas = Object.values(filasPorCuit);
  const dup = (datos.length - 2) - filas.length;
  if (dup > 0) Logger.log('Descartadas ' + dup + ' filas duplicadas (mismo CUIT) en Scoring_Clientes.');

  subirLoteASupabase(filas, 'cuentas_corrientes_scoring', 'cuit', 'Scoring_Clientes');
}

// ===== HISTORIAL_SCORING =====
function subirHistorialScoringASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName('Historial_Scoring');
  if (!hoja || hoja.getLastRow() <= 1) {
    avisar('No hay datos en Historial_Scoring todavía.');
    return;
  }

  const datos = hoja.getDataRange().getValues();
  const HEADERS = datos[0]; // sin fila de título: los encabezados van en la fila 1
  const idx = {
    fecha:             HEADERS.indexOf('Fecha'),
    periodo:           HEADERS.indexOf('Periodo'),
    clienteId:         HEADERS.indexOf('ClienteId'),
    razonSocial:       HEADERS.indexOf('Razón Social'),
    cuit:              HEADERS.indexOf('CUIT'),
    vendedor:          HEADERS.indexOf('Vendedor'),
    empresa:           HEADERS.indexOf('Empresa'),
    saldoTotal:        HEADERS.indexOf('Saldo Total'),
    factPendientes:    HEADERS.indexOf('Fact. Pendientes'),
    factVencidas:      HEADERS.indexOf('Fact. Vencidas'),
    factMoraReal:      HEADERS.indexOf('Fact. > 30 días'),
    atrasoMax:         HEADERS.indexOf('Atraso Máx'),
    atrasoPromGeneral: HEADERS.indexOf('Atraso Prom General'),
    categoria:         HEADERS.indexOf('Categoría'),
    saldoVencido:      HEADERS.indexOf('Saldo Vencido'),
  };
  verificarEncabezados(idx, 'Historial_Scoring');

  const filasPorClave = {}; // dedup: cuit|periodo
  for (let i = 1; i < datos.length; i++) {
    const fila = datos[i];
    const cuit = String(fila[idx.cuit] || '').trim();
    const periodo = formatearCampoPeriodo(fila[idx.periodo]);
    if (!cuit || !periodo) continue;

    filasPorClave[cuit + '|' + periodo] = {
      cuit: cuit,
      periodo: periodo,
      fecha: formatearCampoFecha(fila[idx.fecha]),
      cliente_id: String(fila[idx.clienteId] || ''),
      razon_social: fila[idx.razonSocial] || null,
      vendedor: normalizarCodigo(fila[idx.vendedor], 3),
      empresa: String(fila[idx.empresa] || ''),
      saldo_total: Number(fila[idx.saldoTotal]) || 0,
      fact_pendientes: Number(fila[idx.factPendientes]) || 0,
      fact_vencidas: Number(fila[idx.factVencidas]) || 0,
      // La columna de Supabase sigue llamándose fact_mayor_45 por
      // compatibilidad, pero desde el cambio de criterio cuenta las
      // facturas con MÁS DE 30 días de atraso. Si se la renombra a
      // fact_mayor_30 hay que cambiar también esta línea.
      fact_mayor_45: Number(fila[idx.factMoraReal]) || 0,
      atraso_max: Number(fila[idx.atrasoMax]) || 0,
      atraso_prom_general: Number(fila[idx.atrasoPromGeneral]) || 0,
      categoria: fila[idx.categoria] || null,
      saldo_vencido: Number(fila[idx.saldoVencido]) || 0,
    };
  }
  const filas = Object.values(filasPorClave);
  const dup = (datos.length - 1) - filas.length;
  if (dup > 0) Logger.log('Descartadas ' + dup + ' filas duplicadas (mismo CUIT+Periodo) en Historial_Scoring.');

  subirLoteASupabase(filas, 'cuentas_corrientes_historial_scoring', 'cuit,periodo', 'Historial_Scoring');
}

// ===== HISTORIAL_DIARIO =====
function subirHistorialDiarioASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName('Historial_Diario');
  if (!hoja || hoja.getLastRow() <= 1) {
    avisar('No hay datos en Historial_Diario todavía.');
    return;
  }

  const datos = hoja.getDataRange().getValues();
  const HEADERS = datos[0];
  const idx = {
    fecha:             HEADERS.indexOf('Fecha'),
    clienteId:         HEADERS.indexOf('ClienteId'),
    razonSocial:       HEADERS.indexOf('Razón Social'),
    cuit:              HEADERS.indexOf('CUIT'),
    vendedor:          HEADERS.indexOf('Vendedor'),
    empresa:           HEADERS.indexOf('Empresa'),
    saldoTotal:        HEADERS.indexOf('Saldo Total'),
    factPendientes:    HEADERS.indexOf('Fact. Pendientes'),
    factVencidas:      HEADERS.indexOf('Fact. Vencidas'),
    factMoraReal:      HEADERS.indexOf('Fact. > 30 días'),
    atrasoMax:         HEADERS.indexOf('Atraso Máx'),
    atrasoPromGeneral: HEADERS.indexOf('Atraso Prom General'),
    categoria:         HEADERS.indexOf('Categoría'),
    saldoVencido:      HEADERS.indexOf('Saldo Vencido'),
  };
  verificarEncabezados(idx, 'Historial_Diario');

  const filasPorClave = {};
  for (let i = 1; i < datos.length; i++) {
    const fila = datos[i];
    const cuit = String(fila[idx.cuit] || '').trim();
    const fecha = formatearCampoFecha(fila[idx.fecha]);
    if (!cuit || !fecha) continue;

    filasPorClave[cuit + '|' + fecha] = {
      cuit: cuit,
      fecha: fecha,
      cliente_id: String(fila[idx.clienteId] || ''),
      razon_social: fila[idx.razonSocial] || null,
      vendedor: normalizarCodigo(fila[idx.vendedor], 3),
      empresa: String(fila[idx.empresa] || ''),
      saldo_total: Number(fila[idx.saldoTotal]) || 0,
      fact_pendientes: Number(fila[idx.factPendientes]) || 0,
      fact_vencidas: Number(fila[idx.factVencidas]) || 0,
      // La columna de Supabase sigue llamándose fact_mayor_45 por
      // compatibilidad, pero desde el cambio de criterio cuenta las
      // facturas con MÁS DE 30 días de atraso. Si se la renombra a
      // fact_mayor_30 hay que cambiar también esta línea.
      fact_mayor_45: Number(fila[idx.factMoraReal]) || 0,
      atraso_max: Number(fila[idx.atrasoMax]) || 0,
      atraso_prom_general: Number(fila[idx.atrasoPromGeneral]) || 0,
      categoria: fila[idx.categoria] || null,
      saldo_vencido: Number(fila[idx.saldoVencido]) || 0,
    };
  }
  const filas = Object.values(filasPorClave);

  subirLoteASupabase(filas, 'cuentas_corrientes_historial_diario', 'cuit,fecha', 'Historial_Diario');
}

// ===== HISTORIAL_CANCELACIONES =====
function subirCancelacionesASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName('Historial_Cancelaciones');
  if (!hoja || hoja.getLastRow() <= 1) {
    avisar('No hay datos en Historial_Cancelaciones todavía.');
    return;
  }

  const datos = hoja.getDataRange().getValues();
  const HEADERS = datos[0];
  const idx = {
    fechaDeteccion:        HEADERS.indexOf('Fecha Detección'),
    periodoConMora:        HEADERS.indexOf('Período con Mora'),
    clienteId:             HEADERS.indexOf('ClienteId'),
    razonSocial:           HEADERS.indexOf('Razón Social'),
    cuit:                  HEADERS.indexOf('CUIT'),
    vendedor:              HEADERS.indexOf('Vendedor'),
    empresa:               HEADERS.indexOf('Empresa'),
    saldoVencidoCancelado: HEADERS.indexOf('Saldo Vencido Cancelado ($)'),
    categoriaQueTenia:     HEADERS.indexOf('Categoría que Tenía'),
    diasAtrasoMax:         HEADERS.indexOf('Días Atraso Máx al Cancelar'),
    diasAtrasoProm:        HEADERS.indexOf('Días Atraso Prom al Cancelar'),
    tipoCancelacion:       HEADERS.indexOf('Tipo de Cancelación'),
  };
  verificarEncabezados(idx, 'Historial_Cancelaciones');

  const filasPorClave = {};
  for (let i = 1; i < datos.length; i++) {
    const fila = datos[i];
    const cuit = String(fila[idx.cuit] || '').trim();
    const periodo = formatearCampoPeriodo(fila[idx.periodoConMora]);
    if (!cuit || !periodo) continue;

    filasPorClave[cuit + '|' + periodo] = {
      cuit: cuit,
      periodo_con_mora: periodo,
      fecha_deteccion: formatearCampoFecha(fila[idx.fechaDeteccion]),
      cliente_id: String(fila[idx.clienteId] || ''),
      razon_social: fila[idx.razonSocial] || null,
      vendedor: normalizarCodigo(fila[idx.vendedor], 3),
      empresa: String(fila[idx.empresa] || ''),
      saldo_vencido_cancelado: Number(fila[idx.saldoVencidoCancelado]) || 0,
      categoria_que_tenia: fila[idx.categoriaQueTenia] || null,
      dias_atraso_max: Number(fila[idx.diasAtrasoMax]) || 0,
      dias_atraso_prom: Number(fila[idx.diasAtrasoProm]) || 0,
      tipo_cancelacion: fila[idx.tipoCancelacion] || null,
    };
  }
  const filas = Object.values(filasPorClave);

  subirLoteASupabase(filas, 'cuentas_corrientes_cancelaciones', 'cuit,periodo_con_mora', 'Historial_Cancelaciones');
}

// ===== AGING (detalle por comprobante) =====
function subirAgingASupabase() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = ss.getSheetByName('Aging_CC_0001_0003');
  if (!hoja || hoja.getLastRow() <= 2) {
    avisar('No hay datos en Aging_CC_0001_0003 todavía.');
    return;
  }

  const datos = hoja.getDataRange().getValues();
  // Fila 1 = título (merged), fila 2 = encabezados, datos desde fila 3 (índice 2)
  const HEADERS = datos[1];
  const idx = {
    vendedor:      HEADERS.indexOf('Vendedor'),
    razonSocial:   HEADERS.indexOf('Razón Social'),
    cuit:          HEADERS.indexOf('CUIT'),
    direccion:     HEADERS.indexOf('Dirección'),
    comprobante:   HEADERS.indexOf('Comprobante'),
    fecha:         HEADERS.indexOf('Fecha'),
    vencimiento:   HEADERS.indexOf('Vencimiento'),
    atraso:        HEADERS.indexOf('Atraso (días)'),
    total:         HEADERS.indexOf('Total'),
    pendiente:     HEADERS.indexOf('Pendiente'),
    empresa:       HEADERS.indexOf('Empresa'),
    observaciones: HEADERS.indexOf('Observaciones'),
  };
  verificarEncabezados(idx, 'Aging_CC_0001_0003');

  const filasPorClave = {};
  let saltadas = 0;
  for (let i = 2; i < datos.length; i++) {
    const fila = datos[i];
    const razonSocial = String(fila[idx.razonSocial] || '').trim();
    // Saltar filas de subtotal y vacías
    if (razonSocial === 'SUBTOTAL' || razonSocial === '') continue;

    const comprobante = String(fila[idx.comprobante] || '').trim();
    const empresa = String(fila[idx.empresa] || '').trim();
    if (!comprobante || !empresa) { saltadas++; continue; }

    filasPorClave[empresa + '|' + comprobante] = {
      comprobante: comprobante,
      empresa: empresa,
      cuit: String(fila[idx.cuit] || '').trim(),
      razon_social: razonSocial,
      vendedor: normalizarCodigo(fila[idx.vendedor], 3),
      direccion: String(fila[idx.direccion] || ''),
      fecha: formatearCampoFecha(fila[idx.fecha]),
      vencimiento: formatearCampoFecha(fila[idx.vencimiento]),
      atraso: (fila[idx.atraso] === '' || fila[idx.atraso] === null) ? null : Number(fila[idx.atraso]),
      total: Number(fila[idx.total]) || 0,
      pendiente: Number(fila[idx.pendiente]) || 0,
      observaciones: String(fila[idx.observaciones] || ''),
    };
  }
  const filas = Object.values(filasPorClave);
  if (saltadas > 0) Logger.log('Aging: ' + saltadas + ' filas salteadas por falta de comprobante o empresa.');
  Logger.log('Aging: ' + filas.length + ' filas listas para subir.');

  subirLoteASupabase(filas, 'cuentas_corrientes_aging', 'empresa,comprobante', 'Aging_CC_0001_0003');
}

// ═══════════════════════════════════════════════════════════
// GUARDAS
// ═══════════════════════════════════════════════════════════

// Si a alguien se le ocurre renombrar una columna en la planilla, indexOf()
// devuelve -1 y la subida sale "bien" con todas las filas vacías o salteadas,
// sin un solo error. Esto lo convierte en un error ruidoso, que es lo que
// queremos.
function verificarEncabezados(idx, nombreHoja) {
  const faltantes = Object.keys(idx).filter(function(k) { return idx[k] === -1; });
  if (faltantes.length > 0) {
    throw new Error('En la hoja "' + nombreHoja + '" no encontré estas columnas: ' +
                    faltantes.join(', ') + '. ¿Le cambiaron el nombre a algún encabezado?');
  }
}

// Sheets convierte '006' en el número 6 y '0001' en 1 apenas puede. Los códigos
// de vendedor y empresa se usan para cruzar con el resto del tablero, así que
// se vuelven a rellenar con ceros antes de subirlos.
function normalizarCodigo(valor, largo) {
  const limpio = String(valor === null || valor === undefined ? '' : valor).trim();
  if (limpio === '') return '';
  if (!/^[0-9]+$/.test(limpio)) return limpio;   // 'WEB' y similares quedan igual
  return limpio.padStart(largo, '0');
}

// ═══════════════════════════════════════════════════════════
// ÍNDICES FINANCIEROS — reescribe las fórmulas de la hoja
// (sin cambios de lógica; solo el alert pasa por avisar())
// ═══════════════════════════════════════════════════════════
function arreglarIndices() {
  const ws = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Indices_Financieros');
  if (!ws) { avisar('No se encontró Indices_Financieros'); return; }

  const SC    = "'Scoring_Clientes'";
  const AG    = "'Aging_CC_0001_0003'";
  const SCF   = `${SC}!F3:F5000`;
  const SCA   = `${SC}!A3:A5000`;
  const SCJ   = `${SC}!J3:J5000`;
  const SCK   = `${SC}!K3:K5000`;
  const SCN   = `${SC}!N3:N5000`;
  const AGH   = `${AG}!H3:H5000`;
  const AGJ   = `${AG}!J3:J5000`;
  const AGB   = `${AG}!B3:B5000`;
  const BRUTA = `SUMAPRODUCTO((${SCF}>0)*${SCF})`;

  function top(n) {
    let s = '';
    for (let k=1; k<=n; k++) s += (k>1?'+':'') + `K.ESIMO.MAYOR(${SCF};${k})`;
    return s;
  }

  const lastRow = ws.getLastRow();
  const vals    = ws.getRange(1,1,lastRow,1).getValues();
  let n = 0;

  function w(r, c, f) { ws.getRange(r,c).setValue(f); n++; }

  for (let i = 0; i < vals.length; i++) {
    const r = i + 1;
    const a = String(vals[i][0]||'').replace(/\n/g,' ').trim().toLowerCase();

    if (/mora total/.test(a) && /cartera bruta/.test(a))
      w(r,2,`=SUMAPRODUCTO((${AGH}<>"")*(${AGH}>0)*(${AGJ}>0)*(${AGB}<>"SUBTOTAL")*${AGJ})/(${BRUTA})`);
    else if (/mora real/.test(a))
      w(r,2,`=SUMAPRODUCTO((${AGH}<>"")*(${AGH}>45)*(${AGJ}>0)*(${AGB}<>"SUBTOTAL")*${AGJ})/(${BRUTA})`);
    else if (/cr.tico/.test(a) && /cartera/.test(a) && !/riesgo/.test(a))
      w(r,2,`=SUMAPRODUCTO((${SCA}="CRÍTICO")*${SCF})/(${BRUTA})`);
    else if (/cartera en riesgo/.test(a))
      w(r,2,`=SUMAPRODUCTO(((${SCA}="CRÍTICO")+(${SCA}="RIESGOSO"))*${SCF})/(${BRUTA})`);
    else if (/tasa de vencimiento/.test(a))
      w(r,2,`=SUMA(${SCK})/SUMA(${SCJ})`);
    else if (/atraso promedio/.test(a) && /ponderado/.test(a))
      w(r,2,`=SUMAPRODUCTO((${SCF}>0)*${SCN}*${SCF})/SUMAPRODUCTO((${SCF}>0)*${SCF})`);
    else if (/5 mayores/.test(a) && /saldo/.test(a))
      w(r,2,`=${top(5)}`);
    else if (/top 5/.test(a) && /%/.test(a))
      w(r,2,`=(${top(5)})/(${BRUTA})`);
    else if (/top 10/.test(a))
      w(r,2,`=(${top(10)})/(${BRUTA})`);
    else if (/herfindahl|hhi/.test(a))
      w(r,2,`=SUMAPRODUCTO((${SCF}>0)*${SCF}*(${SCF}>0)*${SCF})/(${BRUTA})^2`);
    else if (a === 'crítico') {
      w(r,2,`=SUMAPRODUCTO((${SCA}="CRÍTICO")*1)`);
      w(r,3,`=SUMAPRODUCTO((${SCA}="CRÍTICO")*${SCF})`);
      w(r,4,`=C${r}/(${BRUTA})`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (a === 'riesgoso') {
      w(r,2,`=SUMAPRODUCTO((${SCA}="RIESGOSO")*1)`);
      w(r,3,`=SUMAPRODUCTO((${SCA}="RIESGOSO")*${SCF})`);
      w(r,4,`=C${r}/(${BRUTA})`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (a === 'observación') {
      w(r,2,`=SUMAPRODUCTO((${SCA}="OBSERVACIÓN")*1)`);
      w(r,3,`=SUMAPRODUCTO((${SCA}="OBSERVACIÓN")*${SCF})`);
      w(r,4,`=C${r}/(${BRUTA})`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (a === 'bueno') {
      w(r,2,`=SUMAPRODUCTO((${SCA}="BUENO")*1)`);
      w(r,3,`=SUMAPRODUCTO((${SCA}="BUENO")*${SCF})`);
      w(r,4,`=C${r}/(${BRUTA})`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (a === 'al día') {
      w(r,2,`=SUMAPRODUCTO((${SCA}="AL DÍA")*1)`);
      w(r,3,`=SUMAPRODUCTO((${SCA}="AL DÍA")*${SCF})`);
      w(r,4,`=C${r}/(${BRUTA})`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (/total general/.test(a)) {
      w(r,2,`=SUMA(B${r-5}:B${r-1})`);
      w(r,3,`=${BRUTA}`);
      w(r,5,`=C${r}/B${r}`);
    }
    else if (/por vencer|mora temprana|mora moderada|mora relevante|mora grave|mora cr/.test(a))
      w(r,3,`=B${r}/(${BRUTA})`);
  }

  avisar('✅ ' + n + ' celdas corregidas.\n\n' +
    'Solo se usaron:\n' +
    '• SUMAPRODUCTO → reemplaza SUMAR.SI y CONTAR.SI\n' +
    '• SUMA y MAX → para sumas simples\n' +
    '• K.ESIMO.MAYOR → para Top 5 y Top 10\n' +
    'Sin SUMAR.SI, sin CONTAR.SI, sin SIERROR.');
}
