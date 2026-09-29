// ============================================================================
//  Codigo.gs — planilla "2026 - LOGISTICA BRANDMARK - UNIBRAND"
// ============================================================================
//
//  COPIA DE RESPALDO. El original vive adentro del Google Sheet. Ver el README
//  de esta carpeta.
//
//  La integracion con la API de Sigma: clientes, notas de credito y
//  proveedores. No declara nada a nivel superior mas que sus funciones, asi
//  que no participa de los choques de nombres que rompieron el proyecto.
//
//  OJO: `sincronizarNotasDeCredito` ESTA DECLARADA TRES VECES (lineas ~113,
//  ~195 y ~280 de este archivo). No es un error de sintaxis --JavaScript lo
//  permite-- y por eso nunca aviso nada: GANA LA ULTIMA, las dos primeras son
//  codigo muerto que igual se lee y se mantiene sin darse cuenta.
//
//  Y no son iguales. La tercera --la que corre-- es la unica que fuerza
//  formato de texto en las columnas de codigo (para no perder los ceros a la
//  izquierda del CUIT, el CAE y el codigo de cliente) y la unica que corta con
//  un throw cuando la API falla, en vez de seguir como si nada. Las dos de
//  arriba son versiones viejas de lo mismo.
//
//  Borrar las dos primeras es seguro y deja el archivo diciendo lo que hace.
// ============================================================================

function sincronizarClientes() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente + '/sigma/api/v10/ExportClientes';
  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };
  const respuesta = UrlFetchApp.fetch(url, opciones);
  const codigo = respuesta.getResponseCode();

  if (codigo !== 200) {
    avisar('Hubo un problema al conectar. Código: ' + codigo + '\n\n' + respuesta.getContentText().substring(0, 300));
    return;
  }

  const clientes = JSON.parse(respuesta.getContentText());
  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('clientes');
  if (!hoja) {
    avisar('No encontré la hoja clientes. Revisá que el nombre esté escrito exactamente así.');
    return;
  }

  const ahora = new Date();
  const filas = clientes.map(function (c) {
    const activo = (c.desactivado === true || c.suspendido === true) ? 'NO' : 'SI';
    return [
      c.id || '', c.nombre || '', c.tipoDocumento || '', c.numeroDocumento || '',
      c.condicionIva || '', c.localidad || '', c.provincia || '', c.telefono || '',
      c.eMail || '', c.vendedorPredeterminado || '', activo,
      c.fechaAlta || '', c.ultimaModificacion || '', ahora
    ];
  });

  if (hoja.getLastRow() > 1) {
    hoja.getRange(2, 1, hoja.getLastRow() - 1, 14).clearContent();
  }
  hoja.getRange(2, 1, filas.length, 14).setValues(filas);

  avisar('¡Listo! Se escribieron ' + filas.length + ' clientes en clientes.');
}

// Muestra un cartelito SOLO si hay alguien con la pantalla abierta.
// Si se ejecuta sola (activador automático), no hay pantalla -
// en ese caso guardamos el mensaje en el registro en vez de fallar.
function avisar(mensaje) {
  try {
    SpreadsheetApp.getUi().alert(mensaje);
  } catch (e) {
    console.log(mensaje);
  }
}

function probarExportFacturas() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  // La N/C conocida: CB93-00000076, cliente 005022, 26/02/2026.
  // Dejo un día de margen de cada lado por las dudas de huso horario.
  const dde = '2026-02-25';
  const hta = '2026-02-27';
  const cta = '005022';

  const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente +
    '/sigma/api/v10/ExportFacturas?dde=' + dde + '&hta=' + hta + '&cta=' + cta;

  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };

  const respuesta = UrlFetchApp.fetch(url, opciones);
  const codigo = respuesta.getResponseCode();
  const texto = respuesta.getContentText();

  if (codigo !== 200) {
    avisar('Hubo un problema al conectar. Código: ' + codigo + '\n\n' + texto.substring(0, 300));
    return;
  }

  try {
    console.log(JSON.stringify(JSON.parse(texto), null, 2));
  } catch (e) {
    console.log(texto);
  }
  avisar('Listo. Andá a Ver > Registros (o "Registro de ejecución") y copiame todo lo que aparece ahí.');
}

function sincronizarNotasDeCredito() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  // Desde cuándo traer datos. Se puede cambiar esta fecha sin tocar nada más.
  const dde = '2026-01-01';
  const hta = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

  const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente +
    '/sigma/api/v10/ExportFacturas?dde=' + dde + '&hta=' + hta;

  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };

  const respuesta = UrlFetchApp.fetch(url, opciones);
  const codigo = respuesta.getResponseCode();
  if (codigo !== 200) {
    avisar('Hubo un problema al conectar. Código: ' + codigo + '\n\n' + respuesta.getContentText().substring(0, 300));
    return;
  }

  const comprobantes = JSON.parse(respuesta.getContentText());

  // ExportFacturas trae facturas y notas de crédito mezcladas.
  // Nos quedamos solo con comprobanteTipo "C" (confirmado con la prueba real).
  const notasDeCredito = comprobantes.filter(function (c) {
    return c.comprobanteTipo === 'C';
  });

  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('notas de credito');
  if (!hoja) {
    avisar('No encontré la hoja "notas de credito". Creala primero con ese nombre exacto.');
    return;
  }

  const ahora = new Date();
  const filas = notasDeCredito.map(function (c) {
    return [
      c.fecha || '',
      (c.comprobanteCodigo || '') + '-' + (c.comprobanteNumero || ''),
      c.clienteNombre || '',
      c.clienteId || '',
      c.totalComprobante || 0,
      c.motivoNc || '',
      c.empresa || '',
      c.sucursal || '',
      c.vendedor || '',
      c.estado || '',
      c.moneda || '',
      c.observacion || '',
      c.ajustaComprobanteId || '',
      c.caeNumero || '',
      c.caeVencimiento || '',
      c.clienteNumeroDocumento || '',
      c.clienteDireccion || '',
      c.subtotal || 0,
      c.totalIva || 0,
      c.totalImpuestosInternos || 0,
      c.percepcionIva || 0,
      c.percepcionOtras || 0,
      c.letra || '',
      c.id || '',
      ahora
    ];
  });

  const columnas = 25;
  if (hoja.getLastRow() > 1) {
    hoja.getRange(2, 1, hoja.getLastRow() - 1, columnas).clearContent();
  }
  if (filas.length > 0) {
    hoja.getRange(2, 1, filas.length, columnas).setValues(filas);
  }
  avisar('¡Listo! Se escribieron ' + filas.length + ' notas de crédito en "notas de credito".');
}

function sincronizarNotasDeCredito() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  const dde = '2026-01-01';
  const hta = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

  const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente +
    '/sigma/api/v10/ExportFacturas?dde=' + dde + '&hta=' + hta;

  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };

  const respuesta = UrlFetchApp.fetch(url, opciones);
  const codigo = respuesta.getResponseCode();
  if (codigo !== 200) {
    avisar('Hubo un problema al conectar. Código: ' + codigo + '\n\n' + respuesta.getContentText().substring(0, 300));
    return;
  }

  const comprobantes = JSON.parse(respuesta.getContentText());
  const notasDeCredito = comprobantes.filter(function (c) {
    return c.comprobanteTipo === 'C';
  });

  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('notas de credito');
  if (!hoja) {
    avisar('No encontré la hoja "notas de credito". Creala primero con ese nombre exacto.');
    return;
  }

  const ahora = new Date();
  const filas = notasDeCredito.map(function (c) {
    return [
      c.fecha || '',
      (c.comprobanteCodigo || '') + '-' + (c.comprobanteNumero || ''),
      c.clienteNombre || '',
      c.clienteId || '',
      c.totalComprobante || 0,
      c.motivoNc || '',
      c.empresa || '',
      c.sucursal || '',
      c.vendedor || '',
      c.estado || '',
      c.moneda || '',
      c.observacion || '',
      c.ajustaComprobanteId || '',
      c.caeNumero || '',
      c.caeVencimiento || '',
      c.clienteNumeroDocumento || '',
      c.clienteDireccion || '',
      c.subtotal || 0,
      c.totalIva || 0,
      c.totalImpuestosInternos || 0,
      c.percepcionIva || 0,
      c.percepcionOtras || 0,
      c.letra || '',
      c.id || '',
      ahora
    ];
  });

  const columnas = 25;
  if (hoja.getLastRow() > 1) {
    hoja.getRange(2, 1, hoja.getLastRow() - 1, columnas).clearContent();
  }

  if (filas.length > 0) {
    // Texto forzado en las columnas "código", para que no pierdan los ceros a la izquierda.
    hoja.getRange(2, 4, filas.length, 1).setNumberFormat('@');   // Código Cliente
    hoja.getRange(2, 7, filas.length, 3).setNumberFormat('@');   // Empresa, Sucursal, Vendedor
    hoja.getRange(2, 14, filas.length, 1).setNumberFormat('@');  // Numero CAE
    hoja.getRange(2, 16, filas.length, 1).setNumberFormat('@');  // CUIT

    hoja.getRange(2, 1, filas.length, columnas).setValues(filas);
  }
  avisar('¡Listo! Se escribieron ' + filas.length + ' notas de crédito en "notas de credito".');
}

function sincronizarNotasDeCredito() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  const dde = '2026-01-01';
  const hta = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

  const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente +
    '/sigma/api/v10/ExportFacturas?dde=' + dde + '&hta=' + hta;

  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };

  const respuesta = UrlFetchApp.fetch(url, opciones);
  const codigo = respuesta.getResponseCode();
  if (codigo !== 200) {
    const mensaje = 'Hubo un problema al conectar. Código: ' + codigo + '\n\n' + respuesta.getContentText().substring(0, 300);
    avisar(mensaje);
    throw new Error(mensaje);
  }

  const comprobantes = JSON.parse(respuesta.getContentText());
  const notasDeCredito = comprobantes.filter(function (c) {
    return c.comprobanteTipo === 'C';
  });

  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('notas de credito');
  if (!hoja) {
    const mensaje = 'No encontré la hoja "notas de credito". Creala primero con ese nombre exacto.';
    avisar(mensaje);
    throw new Error(mensaje);
  }

  const ahora = new Date();
  const filas = notasDeCredito.map(function (c) {
    return [
      c.fecha || '', (c.comprobanteCodigo || '') + '-' + (c.comprobanteNumero || ''),
      c.clienteNombre || '', c.clienteId || '', c.totalComprobante || 0, c.motivoNc || '',
      c.empresa || '', c.sucursal || '', c.vendedor || '', c.estado || '', c.moneda || '',
      c.observacion || '', c.ajustaComprobanteId || '', c.caeNumero || '', c.caeVencimiento || '',
      c.clienteNumeroDocumento || '', c.clienteDireccion || '', c.subtotal || 0, c.totalIva || 0,
      c.totalImpuestosInternos || 0, c.percepcionIva || 0, c.percepcionOtras || 0,
      c.letra || '', c.id || '', ahora
    ];
  });

  const columnas = 25;
  if (hoja.getLastRow() > 1) {
    hoja.getRange(2, 1, hoja.getLastRow() - 1, columnas).clearContent();
  }
  if (filas.length > 0) {
    hoja.getRange(2, 4, filas.length, 1).setNumberFormat('@');
    hoja.getRange(2, 7, filas.length, 3).setNumberFormat('@');
    hoja.getRange(2, 14, filas.length, 1).setNumberFormat('@');
    hoja.getRange(2, 16, filas.length, 1).setNumberFormat('@');
    hoja.getRange(2, 1, filas.length, columnas).setValues(filas);
  }
  avisar('¡Listo! Se escribieron ' + filas.length + ' notas de crédito en "notas de credito".');
}

/**
 * ============================================================
 * SINCRONIZAR PROVEEDORES
 * ============================================================
 * Sigma NO tiene un endpoint de lista maestra de proveedores
 * (no existe "ExportProveedores", se confirmo revisando el
 * manual completo de la API). Lo que si existe es el campo
 * proveedorId/proveedorNombre/proveedorCuit dentro de cada
 * factura de compra (ExportFacturasCompra).
 *
 * Esta funcion arma la lista de proveedores "por la vuelta":
 * recorre las facturas de compra en tramos mensuales (no hay
 * paginacion en este endpoint, mejor no pedir años enteros de
 * una), y arma una lista UNICA de proveedores (uno por
 * proveedorId), quedandose con el nombre/CUIT mas reciente
 * que haya visto para cada uno.
 *
 * No incluye direccion ni telefono: esos datos no estan
 * disponibles en ningun endpoint de la API de Sigma.
 * ============================================================
 */
function sincronizarProveedores() {
  const props = PropertiesService.getScriptProperties();
  const urlCliente = props.getProperty('SIGMA_URL_CLIENTE');
  const baseAlias = props.getProperty('SIGMA_BASEALIAS');
  const idCliente = props.getProperty('SIGMA_ID_CLIENTE');
  const token = props.getProperty('SIGMA_TOKEN');

  // Rango amplio para capturar proveedores historicos.
  // Se puede achicar esta fecha de inicio si tarda demasiado.
  const inicio = new Date(2020, 0, 1);
  const hoy = new Date();

  const proveedoresPorId = {}; // dedup: proveedorId -> {id, nombre, cuit}
  let mesActual = new Date(inicio);
  let totalFacturas = 0;

  while (mesActual <= hoy) {
    const primerDia = new Date(mesActual.getFullYear(), mesActual.getMonth(), 1);
    const ultimoDiaMes = new Date(mesActual.getFullYear(), mesActual.getMonth() + 1, 0);
    const ultimoDia = ultimoDiaMes > hoy ? hoy : ultimoDiaMes;

    const dde = Utilities.formatDate(primerDia, 'GMT-3', 'yyyy-MM-dd');
    const hta = Utilities.formatDate(ultimoDia, 'GMT-3', 'yyyy-MM-dd');

    const url = 'https://' + urlCliente + '/' + baseAlias + '/' + idCliente +
      '/sigma/api/v10/ExportFacturasCompra?dde=' + dde + '&hta=' + hta;

    const facturas = llamarConReintentos_(url, token);
    if (facturas === null) {
      // Tramo con error persistente: seguimos con el resto, no cortamos todo.
      console.log('Tramo ' + dde + ' a ' + hta + ': fallo, se salta.');
    } else {
      totalFacturas += facturas.length;
      facturas.forEach(function (f) {
        if (!f.proveedorId) return;
        proveedoresPorId[f.proveedorId] = {
          id: f.proveedorId,
          nombre: f.proveedorNombre || '',
          cuit: f.proveedorCuit || ''
        };
      });
    }

    // Avanzar al mes siguiente
    mesActual = new Date(mesActual.getFullYear(), mesActual.getMonth() + 1, 1);
    Utilities.sleep(300); // pausa chica entre tramos, ademas del manejo de 429
  }

  const listaProveedores = Object.values(proveedoresPorId);
  listaProveedores.sort(function (a, b) { return a.nombre.localeCompare(b.nombre); });

  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('proveedores');
  if (!hoja) {
    avisar('No encontré la hoja "proveedores". Creala primero con ese nombre exacto (columnas: Codigo, Nombre, CUIT, Actualizado).');
    return;
  }

  const ahora = new Date();
  const filas = listaProveedores.map(function (p) {
    return [p.id, p.nombre, p.cuit, ahora];
  });

  if (hoja.getLastRow() > 1) {
    hoja.getRange(2, 1, hoja.getLastRow() - 1, 4).clearContent();
  }
  if (filas.length > 0) {
    hoja.getRange(2, 1, filas.length, 4).setValues(filas);
  }

  avisar('¡Listo! ' + listaProveedores.length + ' proveedores únicos encontrados, ' +
    'a partir de ' + totalFacturas + ' facturas de compra revisadas.');
}

/** Llama a la API con reintento automático si da 429 (rate limit) o si
 *  falla la conexion misma (ej. "Address unavailable"). Devuelve null si
 *  falla de forma persistente, para no cortar todo el proceso. */
function llamarConReintentos_(url, token, intento) {
  intento = intento || 1;
  const opciones = {
    method: 'get',
    headers: { 'X-Auth-Token': token },
    muteHttpExceptions: true
  };

  let respuesta;
  try {
    respuesta = UrlFetchApp.fetch(url, opciones);
  } catch (e) {
    // Error de red/DNS (ej. "Address unavailable"), no de la API en si.
    console.log('Error de conexion en intento ' + intento + ': ' + e.message);
    if (intento < 4) {
      Utilities.sleep(3000 * intento); // espera creciente: 3s, 6s, 9s
      return llamarConReintentos_(url, token, intento + 1);
    }
    console.log('Error de conexion persistente, se salta este tramo: ' + url);
    return null;
  }

  const codigo = respuesta.getResponseCode();

  if (codigo === 429) {
    const esperaMs = parseInt(respuesta.getHeaders()['X-Retry-After-ms'] || '2000', 10);
    Utilities.sleep(esperaMs);
    return llamarConReintentos_(url, token, intento);
  }

  if (codigo !== 200) {
    if (intento < 3) {
      Utilities.sleep(2000);
      return llamarConReintentos_(url, token, intento + 1);
    }
    console.log('Error persistente en ' + url + ' - Código: ' + codigo);
    return null;
  }

  try {
    return JSON.parse(respuesta.getContentText());
  } catch (e) {
    console.log('Error parseando respuesta de ' + url);
    return null;
  }
}