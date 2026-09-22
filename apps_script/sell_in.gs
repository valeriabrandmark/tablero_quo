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
 *  5. Elegir `instalarDisparador` y ejecutarla. Listo: corre cada hora.
 *
 * Si algun dia falla, Google te manda un mail: el disparador avisa solo.
 *
 * ===========================================================================
 *  Y PARA QUE EL TABLERO PUEDA PEDIRLA CUANDO QUIERA (opcional, una vez)
 * ===========================================================================
 *
 * Con el disparador solo, la planilla se manda una vez por dia: un descuento
 * editado a las 10 de la maniana entra maniana. `doPost` deja que el
 * orquestador pida la foto en el momento, asi el boton "Actualizar ahora" del
 * panel de Compras trae el descuento de hace diez segundos.
 *
 *  6. Implementar -> Nueva implementacion -> tipo "Aplicacion web".
 *         Ejecutar como:   Yo (tu cuenta)
 *         Quien tiene acceso: Cualquier usuario
 *  7. Copiar la URL que queda (termina en /exec) y cargarla en GitHub como el
 *     secreto SELL_IN_WEBAPP_URL del repo tablero_quo.
 *
 * "Cualquier usuario" NO significa que cualquiera pueda leer la planilla: lo
 * unico que hace `doPost` es pedirle a esta misma funcion que mande la hoja al
 * tablero, y SOLO si el pedido trae el token correcto. Sin token contesta 401
 * y no lee nada. Es la misma proteccion que ya tiene el envio diario.
 *
 * CADA VEZ QUE SE EDITA ESTE ARCHIVO hay que volver a implementar (Implementar
 * -> Administrar implementaciones -> editar -> Version: nueva). Si no, la URL
 * sigue sirviendo la version vieja.
 */

/** La hoja resumen. Si algun dia cambia de nombre, se cambia aca. */
const HOJA = 'Tablero';

/** A donde se manda. Es publico: lo que protege es el token, no la URL. */
const DESTINO = 'https://znxhjbkkvkvcszdbczcg.supabase.co/functions/v1/sell-in';

/**
 * Cada cuantas horas se manda la planilla.
 *
 * ERA UNA VEZ POR DIA, A LAS 6, y eso es lo que hacia que un descuento
 * editado el lunes a las 10 recien apareciera en el panel de Compras el
 * martes: la foto de las 06:00 ya se habia mandado.
 *
 * Con 1, el descuento entra en la corrida siguiente del orquestador -- una
 * hora en el peor caso.
 *
 * Google solo acepta 1, 2, 4, 6, 8 o 12. Si se sube este numero, subir tambien
 * el aviso de `leer_crudo` en sell_in.py, que hoy avisa a los 3 dias.
 *
 * NO SE ACUMULAN: el tablero se queda con las ultimas `FOTOS_QUE_SE_GUARDAN`
 * de sell_in.py y borra el resto. Cada foto pesa ~475 kB, asi que una por hora
 * sin limpiar serian 11 MB por dia sobre una base de 383 MB.
 */
const CADA_HORAS = 1;

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

/**
 * El tablero pide la foto AHORA. Contesta lo mismo que `enviarSellIn`.
 *
 * SE VALIDA EL TOKEN ANTES DE TOCAR LA PLANILLA. La URL de una aplicacion web
 * de Apps Script es publica --hay que publicarla asi para que el orquestador,
 * que no tiene sesion de Google, pueda llamarla-- asi que lo unico que separa
 * a un curioso de disparar envios es este token. Es el mismo que ya usa el
 * envio diario: un secreto menos que rotar.
 *
 * VA EN EL CUERPO Y NO EN LA URL a proposito. Un token en el query string
 * queda en los registros de Google, en el historial y en cualquier proxy del
 * camino; en el cuerpo de un POST, no.
 */
function doPost(e) {
  var pedido = {};
  try {
    pedido = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    pedido = {};
  }

  if (pedido.token !== token_()) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: 'token invalido' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  // Si la planilla esta vacia o la hoja no existe, `enviarSellIn` tira el
  // error. Se devuelve como JSON en vez de dejar que Apps Script conteste una
  // pagina de error en HTML, que del otro lado se lee como "anduvo".
  try {
    var cuerpo = enviarSellIn();
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, respuesta: cuerpo }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
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
 * Deja el envio corriendo cada `CADA_HORAS` horas.
 *
 * Borra primero los disparadores de esta misma funcion: correrla dos veces
 * dejaria dos, y la planilla se mandaria el doble de veces para siempre.
 *
 * CORRERLA DE NUEVO ES LO QUE APLICA UN CAMBIO DE `CADA_HORAS`. El disparador
 * ya instalado se queda con la frecuencia que tenia cuando se creo: cambiar la
 * constante y no volver a ejecutar esto no hace nada.
 */
function instalarDisparador() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'enviarSellIn') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('enviarSellIn').timeBased().everyHours(CADA_HORAS).create();
  Logger.log('Listo: la planilla se manda cada ' + CADA_HORAS + ' hora(s).');
}
