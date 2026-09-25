/**
 * Sube la auditoria de fletes de esta planilla a bronze.auditoria_sevillanita.
 *
 * ============================================================================
 *  COMO SE CONFIGURA (una sola vez)
 * ============================================================================
 *
 *  1. Extensiones -> Apps Script, y pegar este archivo.
 *  2. Configuracion del proyecto -> Propiedades del script -> agregar:
 *
 *         AUDITORIA_TOKEN = (la misma clave que se cargo en Supabase; NO la
 *                            escribas en el codigo)
 *
 *  3. Recargar la planilla: aparece el menu "Tablero".
 *
 * EL TOKEN VIVE EN LAS PROPIEDADES DEL SCRIPT Y NO EN EL CODIGO. No es por
 * prolijidad: si estuviera en el codigo, se copiaria junto con el archivo cada
 * vez que alguien duplique la planilla, y quedaria en el historial para
 * siempre.
 *
 * Del lado del servidor, la clave de la base NO esta aca: la funcion
 * `auditoria-sevillanita` es la que escribe, y lo unico que este script puede
 * hacer con su token es mandar filas de auditoria. Si el token se filtra, se
 * rota y listo.
 *
 * ============================================================================
 *  QUE MANDA
 * ============================================================================
 *
 * Las columnas se buscan POR EL TEXTO DEL ENCABEZADO, no por posicion: mover
 * una columna de lugar en la planilla no rompe nada. Si cambia el texto de un
 * encabezado, se cambia aca abajo y solo aca.
 *
 * Es un upsert por factura: se puede reenviar la auditoria entera todas las
 * veces que haga falta, que cada factura queda una sola vez con su ultima
 * version. Un envio parcial NO borra lo que no vino.
 */

/** A donde se manda. Es publico: lo que protege es el token, no la URL. */
var DESTINO =
  'https://znxhjbkkvkvcszdbczcg.supabase.co/functions/v1/auditoria-sevillanita';

/** Que encabezado de la planilla va a cada columna de la tabla. */
var COLUMNAS = {
  factura: 'Factura',
  fecha: 'Fecha',
  localidad: 'Localidad',
  veredicto_tarifa: 'Veredicto tarifa',
  veredicto_peso: 'Veredicto peso',
  accion: 'Accion',
  monto_en_juego: 'Monto en juego'
};

/** La hoja de la que se lee. Vacio = la que este activa. */
var HOJA = 'Auditoria';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tablero')
    .addItem('Subir auditoria al tablero', 'subirAuditoria')
    .addToUi();
}

function token_() {
  var t = PropertiesService.getScriptProperties().getProperty('AUDITORIA_TOKEN');
  if (!t) {
    throw new Error(
      'Falta AUDITORIA_TOKEN en Configuracion del proyecto -> Propiedades del script.'
    );
  }
  return t;
}

/** Una fecha como '2026-09-25', que es lo que espera una columna `date`. */
function fecha_(valor) {
  if (!valor) return null;
  if (Object.prototype.toString.call(valor) === '[object Date]') {
    return Utilities.formatDate(valor, 'America/Argentina/Buenos_Aires', 'yyyy-MM-dd');
  }
  return String(valor).trim() || null;
}

/**
 * Un numero de una celda que puede venir como '$ 1.234,56'.
 *
 * SI NO SE ENTIENDE, SE MANDA EL TEXTO TAL CUAL y el servidor rechaza el envio
 * diciendo que fila es. Convertirlo a 0 seria peor: el monto en juego de esa
 * factura desapareceria sin que nadie lo note.
 */
function numero_(valor) {
  if (valor === '' || valor === null || valor === undefined) return null;
  if (typeof valor === 'number') return valor;
  var limpio = String(valor).replace(/[^0-9,.-]/g, '').replace(/\./g, '').replace(',', '.');
  var n = Number(limpio);
  return isNaN(n) ? String(valor) : n;
}

function subirAuditoria() {
  var ui = SpreadsheetApp.getUi();
  var libro = SpreadsheetApp.getActiveSpreadsheet();
  var hoja = HOJA ? libro.getSheetByName(HOJA) : libro.getActiveSheet();
  if (!hoja) {
    ui.alert('No encuentro la hoja "' + HOJA + '".');
    return;
  }

  var valores = hoja.getDataRange().getValues();
  if (valores.length < 2) {
    ui.alert('La hoja no tiene filas debajo del encabezado.');
    return;
  }

  // El encabezado se compara sin mayusculas ni espacios de mas: una celda con
  // un espacio al final no tiene por que romper el envio.
  var encabezado = valores[0].map(function (c) {
    return String(c).trim().toLowerCase();
  });
  var donde = {};
  var faltan = [];
  Object.keys(COLUMNAS).forEach(function (campo) {
    var i = encabezado.indexOf(COLUMNAS[campo].trim().toLowerCase());
    if (i === -1) faltan.push(COLUMNAS[campo]);
    donde[campo] = i;
  });
  if (faltan.length) {
    ui.alert(
      'No encuentro estas columnas en el encabezado:\n\n  ' + faltan.join('\n  ') +
      '\n\nSi en la planilla se llaman distinto, cambiar COLUMNAS arriba del script.'
    );
    return;
  }

  var filas = [];
  for (var f = 1; f < valores.length; f++) {
    var fila = valores[f];
    // Una fila sin factura es una fila vacia del final de la hoja: se saltea
    // aca y no se manda. Las que tengan datos pero no factura las rechaza el
    // servidor, que es lo que queremos ver.
    if (!String(fila[donde.factura] || '').trim()) continue;
    filas.push({
      factura: String(fila[donde.factura]).trim(),
      fecha: fecha_(fila[donde.fecha]),
      localidad: String(fila[donde.localidad] || '').trim() || null,
      veredicto_tarifa: String(fila[donde.veredicto_tarifa] || '').trim() || null,
      veredicto_peso: String(fila[donde.veredicto_peso] || '').trim() || null,
      accion: String(fila[donde.accion] || '').trim() || null,
      monto_en_juego: numero_(fila[donde.monto_en_juego])
    });
  }

  if (!filas.length) {
    ui.alert('No hay ninguna fila con factura para subir.');
    return;
  }

  var respuesta = UrlFetchApp.fetch(DESTINO, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'x-auditoria-token': token_() },
    payload: JSON.stringify({ filas: filas }),
    muteHttpExceptions: true
  });

  var codigo = respuesta.getResponseCode();
  var cuerpo = respuesta.getContentText();
  if (codigo === 200) {
    ui.alert('Listo: ' + filas.length + ' filas subidas al tablero.');
    return;
  }
  ui.alert(
    'No se pudo subir (codigo ' + codigo + ').\n\n' + cuerpo +
    '\n\n  400 -> el cuerpo dice que fila esta mal.' +
    '\n  401 -> el AUDITORIA_TOKEN de este script no coincide con el del servidor.' +
    '\n  503 -> falta cargar el token del lado del tablero.'
  );
}
