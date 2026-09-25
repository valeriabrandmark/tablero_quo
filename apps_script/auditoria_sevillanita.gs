/**
 * Sube la hoja ACCIONES a bronze.auditoria_sevillanita.
 *
 * REEMPLAZA A `subirAuditoriaASupabase`, que pegaba contra /rest/v1 con la
 * SERVICE KEY guardada en las Propiedades del script. Ese camino no podia
 * funcionar --la tabla no tiene permiso para ningun rol de la API, y `bronze`
 * no se sirve por la API-- y habilitarlo habria costado caro: exponer `bronze`
 * deja al alcance de la clave publica cinco tablas de cuentas corrientes que
 * hoy tienen INSERT, UPDATE y DELETE para `anon`.
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
 * De la hoja ACCIONES salen las siete columnas en orden: N° FACTURA, fecha,
 * localidad, veredicto de tarifa, veredicto de peso, accion y monto en juego.
 *
 * Es un upsert por factura: se puede reenviar la auditoria entera todas las
 * veces que haga falta, que cada factura queda una sola vez con su ultima
 * version. Un envio parcial NO borra lo que no vino.
 */

/** A donde se manda. Es publico: lo que protege es el token, no la URL. */
var DESTINO =
  'https://znxhjbkkvkvcszdbczcg.supabase.co/functions/v1/auditoria-sevillanita';

/** La hoja de la que se lee: la que arma "Calcular acciones". */
var HOJA = 'ACCIONES';

/**
 * Cuantas columnas se leen, de la A a la G, EN ESE ORDEN.
 *
 * Va por posicion y no por el texto del encabezado --como lo hacia el script
 * anterior-- y no es descuido: esta hoja no la escribe una persona, la genera
 * "Calcular acciones". El orden lo fija ese codigo, asi que es mas estable que
 * los titulos, que alguien puede renombrar sin saber que algo depende de
 * ellos. Si esa funcion algun dia mueve una columna, se cambia aca.
 */
var COLUMNAS = 7;

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

/** El texto de una celda, o null si esta vacia. */
function texto_(valor) {
  return String(valor === null || valor === undefined ? '' : valor).trim() || null;
}

/**
 * Un numero de una celda que puede venir como '$ 1.234,56'.
 *
 * SI NO SE ENTIENDE, SE MANDA EL TEXTO TAL CUAL y el servidor rechaza el envio
 * diciendo que fila es.
 *
 * El script anterior hacia `Number(celda) || 0`, que para '$ 1.234,56' da 0:
 * la fila subia igual, con el monto en juego en cero y sin que nadie se
 * enterara. Un envio rechazado se ve; un cero inventado, no.
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
    ui.alert('La hoja ' + HOJA + ' no tiene filas debajo del encabezado.');
    return;
  }
  if (valores[0].length < COLUMNAS) {
    ui.alert(
      'La hoja ' + HOJA + ' tiene ' + valores[0].length + ' columnas y hacen ' +
      'falta ' + COLUMNAS + ', de N° FACTURA a MONTO EN JUEGO.\n\n' +
      'Correr "Calcular acciones" primero.'
    );
    return;
  }

  var filas = [];
  for (var f = 1; f < valores.length; f++) {
    var fila = valores[f];
    // Sin factura es una fila vacia del final de la hoja: no se manda.
    if (!String(fila[0] || '').trim()) continue;
    filas.push({
      factura: String(fila[0]).trim(),
      fecha: fecha_(fila[1]),
      localidad: texto_(fila[2]),
      veredicto_tarifa: texto_(fila[3]),
      veredicto_peso: texto_(fila[4]),
      accion: texto_(fila[5]),
      monto_en_juego: numero_(fila[6])
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
