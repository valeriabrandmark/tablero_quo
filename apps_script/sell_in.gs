/**
 * Manda la hoja del sell in al tablero, una vez por dia.
 *
 * ===========================================================================
 *  QUE HACE
 * ===========================================================================
 *
 * Lee la hoja "Tablero" TAL CUAL SE VE en la pantalla y la manda al tablero.
 * No interpreta nada: no decide que columna es un mes, no convierte "7,69%" en
 * un numero. Eso lo hace sell_in.py del lado del orquestador, que es el unico
 * lugar donde vive esa logica y el que tiene las pruebas.
 *
 * Escribirla dos veces --una aca en JavaScript y otra en Python-- serian dos
 * interpretaciones que se van separando sin que nadie lo note. Ya nos costo
 * 4.368 valores desalineados una vez.
 *
 * `getDisplayValues()` y no `getValues()` es justamente por eso: devuelve los
 * textos como se ven ("1/8/2026", "7,69%"), que es exactamente lo mismo que
 * devuelve la API de Google. Con `getValues()` las fechas llegarian como
 * objetos Date y los porcentajes como 0.0769, y el parser tendria que
 * adivinar de cual de los dos caminos vino cada dato.
 *
 * ===========================================================================
 *  COMO SE INSTALA (una sola vez)
 * ===========================================================================
 *
 *  1. En la planilla: Extensiones -> Apps Script.
 *  2. Pegar este archivo, reemplazando lo que haya. Guardar.
 *  3. Configuracion del proyecto -> Propiedades del script -> Agregar:
 *         SELL_IN_TOKEN = (la clave que te pasaron; NO la escribas en el codigo)
 *  4. Elegir la funcion `probar` y ejecutarla. Google va a pedir permiso una
 *     vez: es para leer esta planilla y para salir a internet. Aceptar.
 *  5. Elegir `instalarDisparador` y ejecutarla. Listo: corre todos los dias.
 *
 * Si algun dia falla, Google te manda un mail: el disparador avisa solo.
 */

/** La hoja resumen. Si algun dia cambia de nombre, se cambia aca. */
const HOJA = 'Tablero';

/** A donde se manda. Es publico: lo que protege es el token, no la URL. */
const DESTINO = 'https://znxhjbkkvkvcszdbczcg.supabase.co/functions/v1/sell-in';

/** Hora del dia (0-23) a la que sale. 6 = de madrugada, antes de que nadie mire. */
const HORA = 6;

/**
 * El token vive en las Propiedades del script y NO en el codigo.
 *
 * Cualquiera con permiso de edicion en la planilla puede abrir este editor. En
 * el codigo, el token se copiaria junto con el archivo cada vez que alguien
 * duplica la planilla; en las propiedades, se queda en este proyecto.
 */
function token_() {
  const t = PropertiesService.getScriptProperties().getProperty('SELL_IN_TOKEN');
  if (!t) {
    throw new Error(
      'Falta SELL_IN_TOKEN en Configuracion del proyecto -> Propiedades del script.'
    );
  }
  return t;
}

/** Las filas de la hoja, sin las vacias del final. */
function filas_() {
  const hoja = SpreadsheetApp.getActive().getSheetByName(HOJA);
  if (!hoja) {
    throw new Error('No existe la hoja "' + HOJA + '" en esta planilla.');
  }

  const valores = hoja.getDataRange().getDisplayValues();

  // Se sacan las filas totalmente vacias: una planilla que alguien uso tiene
  // cientos abajo del ultimo articulo, y viajarian en cada envio.
  const utiles = valores.filter(function (fila) {
    return fila.some(function (celda) { return String(celda).trim() !== ''; });
  });

  if (utiles.length < 2) {
    // Mandar una hoja vacia haria que el tablero se quede sin sell in del mes.
    // Mejor cortar acá y que el mail de error diga por que.
    throw new Error('La hoja "' + HOJA + '" no tiene datos: se corta sin mandar nada.');
  }
  return utiles;
}

/** Lee la hoja y la manda. Es lo que corre el disparador todos los dias. */
function enviarSellIn() {
  const valores = filas_();

  const respuesta = UrlFetchApp.fetch(DESTINO, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'x-sell-in-token': token_() },
    payload: JSON.stringify({ hoja: HOJA, valores: valores, origen: 'apps-script' }),
    muteHttpExceptions: true,
  });

  const codigo = respuesta.getResponseCode();
  const cuerpo = respuesta.getContentText().slice(0, 300);

  // SE TIRA EL ERROR a proposito en vez de anotarlo y seguir: cuando una
  // ejecucion falla, Google le manda un mail al dueño del script. Tragarse el
  // error dejaria el sell in congelado en silencio, que es la peor version.
  if (codigo < 200 || codigo >= 300) {
    throw new Error(
      'El tablero rechazo el envio (HTTP ' + codigo + '): ' + cuerpo +
      '\n  401 -> el SELL_IN_TOKEN de este script no coincide con el del servidor.' +
      '\n  503 -> falta cargar el token del lado del tablero.'
    );
  }

  Logger.log('Enviadas ' + valores.length + ' filas. Respuesta: ' + cuerpo);
  return cuerpo;
}

/** Lo mismo, pero para correr a mano y ver que contesta. */
function probar() {
  const valores = filas_();
  Logger.log('Hoja "' + HOJA + '": ' + valores.length + ' filas, ' +
             valores[0].length + ' columnas.');
  Logger.log('Encabezado: ' + valores[0].slice(0, 12).join(' | '));
  Logger.log(enviarSellIn());
}

/**
 * Deja el envio corriendo todos los dias.
 *
 * Borra primero los disparadores de esta misma funcion: correrla dos veces
 * dejaria dos, y la planilla se mandaria dos veces por dia para siempre.
 */
function instalarDisparador() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'enviarSellIn') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('enviarSellIn').timeBased().everyDays(1).atHour(HORA).create();
  Logger.log('Listo: se manda todos los dias alrededor de las ' + HORA + ':00.');
}
