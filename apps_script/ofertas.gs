/**
 * Manda la hoja del Consolidador de Ofertas al tablero.
 *
 * ===========================================================================
 *  QUE HACE
 * ===========================================================================
 *
 * Lee la hoja "TABLERO" TAL CUAL SE VE y la manda. No interpreta nada: no
 * convierte "7,69%" en un numero, no decide que columna es cual. Eso vive del
 * lado del orquestador, que es el unico lugar con esa logica y con pruebas.
 *
 * Es el gemelo de sell_in.gs y a proposito: lo que ya anda se copia, no se
 * reinventa. Lo unico que cambia es QUE planilla y A DONDE va.
 *
 * `getDisplayValues()` y no `getValues()` por lo mismo que alla: devuelve los
 * textos como se ven --"7,69%"-- en vez de 0.0769. Las dos rutas entregan lo
 * mismo y hay UN parser.
 *
 * ===========================================================================
 *  PARA QUE SIRVE
 * ===========================================================================
 *
 * Esta planilla es la mitad de los costos. Hoy esa mitad viaja a mano: se
 * copia a la pestaña "Ofertas" de un Excel, se sube el Excel al repositorio y
 * una corrida lo levanta. Dos dias atras esa cadena se corto porque la OTRA
 * pestaña del Excel se guardo vacia, y el pipeline de costos quedo frenado
 * hasta que alguien lo noto.
 *
 * Con esto, la hoja llega sola y el Excel deja de existir.
 *
 * ===========================================================================
 *  COMO SE INSTALA (una sola vez)
 * ===========================================================================
 *
 *  1. En la planilla: Extensiones -> Apps Script.
 *  2. Pegar este archivo, reemplazando lo que haya. Guardar.
 *  3. Configuracion del proyecto -> Propiedades del script -> Agregar:
 *         OFERTAS_TOKEN = (la clave; NO la escribas en el codigo)
 *
 *     LA CLAVE LA GENERAS VOS Y NO SALE DE TUS MANOS. Tiene que ser la misma
 *     en dos lados: aca, y en el secreto OFERTAS_TOKEN de la edge function
 *     `ofertas` del proyecto de Supabase. No hace falta que se la pases a
 *     nadie, ni que viaje por ningun chat: la pegas en los dos lugares y
 *     listo.
 *
 *  4. Elegir la funcion `probar` y ejecutarla. Google pide permiso una vez:
 *     es para leer esta planilla y para salir a internet. Aceptar.
 *  5. Elegir `instalarDisparador` y ejecutarla.
 *  6. Recargar la planilla: arriba aparece el menu "Tablero", con
 *     "Mandar las ofertas ahora".
 *
 * Si algun dia falla, Google le manda un mail al dueño del script: el
 * disparador avisa solo.
 *
 * ===========================================================================
 *  Y PARA QUE EL TABLERO PUEDA PEDIRLA CUANDO QUIERA (opcional, una vez)
 * ===========================================================================
 *
 * Con el disparador solo, la foto es de hasta `CADA_HORAS` horas atras.
 * `doPost` deja pedirla en el momento, que es lo que necesita el boton de
 * actualizar costos: sacar la foto de las ofertas DE AHORA y no la de anoche.
 *
 *  7. Implementar -> Nueva implementacion -> tipo "Aplicacion web".
 *         Ejecutar como:   Yo (tu cuenta)
 *         Quien tiene acceso: Cualquier usuario
 *  8. Copiar la URL que queda (termina en /exec) y cargarla como el secreto
 *     OFERTAS_WEBAPP_URL.
 *
 * "Cualquier usuario" NO significa que cualquiera pueda leer la planilla: lo
 * unico que hace `doPost` es pedirle a esta misma funcion que la mande al
 * tablero, y antes compara el token.
 *
 * CADA VEZ QUE SE EDITA EL SCRIPT HAY QUE PUBLICAR UNA VERSION NUEVA
 * (Implementar -> Administrar implementaciones -> editar -> Version: nueva).
 * Si no, la URL sigue sirviendo la version vieja.
 */

/** La hoja. Si algun dia cambia de nombre, se cambia aca. */
const HOJA = 'TABLERO';

/** A donde se manda. Es publico: lo que protege es el token, no la URL. */
const DESTINO = 'https://znxhjbkkvkvcszdbczcg.supabase.co/functions/v1/ofertas';

/**
 * Cada cuantas horas se manda la planilla.
 *
 * ES MAS ESPACIADO QUE EL DEL SELL IN, Y NO ES DESCUIDO. Aquella hoja se
 * edita todo el tiempo y el panel de Compras la mira todo el tiempo, asi que
 * va cada una hora. Esta se toca cuando cambia una lista --en octubre, una
 * vez-- y lo que la lee es un boton que ANTES DE LEER pide la foto del
 * momento con `doPost`. Mandarla cada hora serian 24 copias por dia de medio
 * mega de una hoja que no cambio.
 *
 * Google solo acepta 1, 2, 4, 6, 8 o 12.
 */
const CADA_HORAS = 12;

/**
 * El token vive en las Propiedades del script y NO en el codigo.
 *
 * Cualquiera con permiso de edicion en la planilla puede abrir este editor. En
 * el codigo, el token se copiaria junto con el archivo cada vez que alguien
 * duplica la planilla; en las propiedades, se queda en este proyecto.
 */
function token_() {
  const t = PropertiesService.getScriptProperties().getProperty('OFERTAS_TOKEN');
  if (!t) {
    throw new Error(
      'Falta OFERTAS_TOKEN en Configuracion del proyecto -> Propiedades del script.'
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
    // UNA HOJA VACIA ES PEOR QUE NINGUNA FOTO. Si entrara, la proxima
    // actualizacion de costos vendria con cero ofertas para todo el catalogo
    // -- o sea, todos los articulos a precio de lista. Mejor cortar aca y que
    // el mail de error diga por que.
    throw new Error('La hoja "' + HOJA + '" no tiene datos: se corta sin mandar nada.');
  }
  return utiles;
}

/** Lee la hoja y la manda. Es lo que corre el disparador. */
function enviarOfertas() {
  const valores = filas_();

  const respuesta = UrlFetchApp.fetch(DESTINO, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'x-ofertas-token': token_() },
    payload: JSON.stringify({ hoja: HOJA, valores: valores, origen: 'apps-script' }),
    muteHttpExceptions: true,
  });

  const codigo = respuesta.getResponseCode();
  const cuerpo = respuesta.getContentText().slice(0, 300);

  // SE TIRA EL ERROR a proposito en vez de anotarlo y seguir: cuando una
  // ejecucion falla, Google le manda un mail al dueño del script. Tragarselo
  // dejaria las ofertas congeladas en silencio, que es la peor version.
  if (codigo < 200 || codigo >= 300) {
    throw new Error(
      'El tablero rechazo el envio (HTTP ' + codigo + '): ' + cuerpo +
      '\n  401 -> el OFERTAS_TOKEN de este script no coincide con el del servidor.' +
      '\n  503 -> falta cargar el token del lado del tablero.'
    );
  }

  Logger.log('Enviadas ' + valores.length + ' filas. Respuesta: ' + cuerpo);
  return cuerpo;
}

/**
 * El tablero pide la foto AHORA. Contesta lo mismo que `enviarOfertas`.
 *
 * ES LO QUE HACE QUE EL BOTON SEA HONESTO. Sin esto, "actualizar los costos"
 * usaria la foto de hasta doce horas atras: alguien edita un descuento, toca
 * el boton, y el tablero carga el descuento viejo sin que nada lo diga.
 *
 * SE VALIDA EL TOKEN ANTES DE TOCAR LA PLANILLA. La URL de una aplicacion web
 * de Apps Script es publica --hay que publicarla asi para que el tablero, que
 * no tiene sesion de Google, pueda llamarla-- asi que lo unico que separa a un
 * curioso de disparar envios es este token.
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

  // Si la planilla esta vacia o la hoja no existe, `enviarOfertas` tira el
  // error. Se devuelve como JSON en vez de dejar que Apps Script conteste una
  // pagina de error en HTML, que del otro lado se lee como "anduvo".
  try {
    var cuerpo = enviarOfertas();
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, respuesta: cuerpo }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}


/**
 * EL MENU DE LA PLANILLA: "Tablero -> Mandar las ofertas ahora".
 *
 * `onOpen` es un DISPARADOR SIMPLE: no se implementa, no se autoriza, no
 * depende de ningun permiso especial. Por eso existe incluso donde la politica
 * de Google Workspace bloquea publicar la aplicacion web.
 *
 * Un disparador simple NO puede llamar a servicios que pidan autorizacion
 * --no podria mandar la planilla por su cuenta-- pero SI puede agregar un
 * menu. Y el item, cuando alguien lo clickea, corre como una funcion normal.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tablero')
    .addItem('Mandar las ofertas ahora', 'mandarAhora')
    .addToUi();
}


/**
 * Manda la planilla en el momento y avisa como salio.
 *
 * EL AVISO DICE QUE ESTO NO CAMBIA NINGUN COSTO, y es lo mas importante que
 * dice. Mandar la foto deja las ofertas disponibles para que alguien las
 * aplique desde el tablero; no reescribe nada por su cuenta. Sin esa
 * aclaracion, quien lo usa se queda pensando que ya actualizo los costos.
 *
 * Los errores van en un `alert` y no en un `toast`: un toast se va solo a los
 * segundos, y un error que nadie llego a leer es un error que no existio.
 */
function mandarAhora() {
  const hoja = SpreadsheetApp.getActiveSpreadsheet();
  hoja.toast('Mandando las ofertas al tablero...', 'Tablero', 30);

  let cuerpo;
  try {
    cuerpo = enviarOfertas();
  } catch (err) {
    SpreadsheetApp.getUi().alert(
      'No se pudieron mandar las ofertas.\n\n' + err + '\n\n' +
      'Si dice 401, el OFERTAS_TOKEN de este script no coincide con el del ' +
      'servidor. Si dice 503, falta cargarlo del lado del tablero.'
    );
    return;
  }

  hoja.toast(
    'Listo, la foto llego. OJO: esto NO cambia ningun costo todavia. ' +
    'Los costos se actualizan desde el tablero, que te muestra que cambia ' +
    'antes de aplicarlo.',
    'Tablero', 20
  );
  Logger.log(cuerpo);
}


/** Lo mismo, pero para correr a mano y ver que contesta. */
function probar() {
  const valores = filas_();
  Logger.log('Hoja "' + HOJA + '": ' + valores.length + ' filas, ' +
             valores[0].length + ' columnas.');
  Logger.log('Encabezado: ' + valores[0].slice(0, 12).join(' | '));
  Logger.log(enviarOfertas());
}

/**
 * Deja el envio corriendo cada `CADA_HORAS` horas.
 *
 * Borra primero los disparadores de esta misma funcion: correrla dos veces
 * dejaria dos, y la planilla se mandaria el doble de veces para siempre.
 *
 * CORRERLA DE NUEVO ES LO QUE APLICA UN CAMBIO DE `CADA_HORAS`. El disparador
 * ya instalado se queda con la frecuencia que tenia cuando se creo.
 */
function instalarDisparador() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'enviarOfertas') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('enviarOfertas').timeBased().everyHours(CADA_HORAS).create();
  Logger.log('Listo: la planilla se manda cada ' + CADA_HORAS + ' hora(s).');
}
