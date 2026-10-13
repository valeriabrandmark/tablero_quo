/**
 * CUENTAS CORRIENTES -> Supabase
 *
 * Sube las tres hojas de la planilla de cuentas corrientes a `bronze`:
 *
 *   cuentas_corrientes_scoring        una fila por CUIT, como esta HOY
 *   cuentas_corrientes_aging          una fila por comprobante pendiente
 *   cuentas_corrientes_cancelaciones  una fila por mora que se cancelo
 *
 * De `scoring` sale ademas, del lado del orquestador, la foto diaria
 * (`foto_cuentas.py` escribe `cuentas_corrientes_historial_diario` y
 * `_historial_scoring`). Esas DOS no se tocan desde aca.
 *
 * =========================================================================
 *  SE MAPEA POR NOMBRE DE ENCABEZADO, NO POR POSICION
 * =========================================================================
 *
 * Es la decision que hace que este archivo sirva cuando cambia la planilla
 * --que es justo lo que paso en octubre de 2026: la fuente paso a ser otro
 * archivo, parecido pero no igual--. Mapeando por posicion, una columna
 * insertada en el medio corre todas las de la derecha y el tablero empieza a
 * mostrar el CUIT en la columna del vendedor sin que nada falle.
 *
 * Cada columna acepta VARIOS nombres posibles (`alias`). Si la planilla nueva
 * le dice "Razon Social" a lo que la vieja llamaba "Cliente", se agrega el
 * nombre a la lista y listo: no hay que tocar una sola linea de codigo.
 *
 * ANTES DE SUBIR, CORRER `ccRevisar()`. No escribe nada: lee la planilla y
 * dice que encontro, que columna no pudo mapear y como quedaria la primera
 * fila. Es mucho mas barato leer eso que descubrir en el tablero que una
 * columna entro corrida.
 *
 * =========================================================================
 *  PREFIJO `cc` / `CC_` EN TODO
 * =========================================================================
 *
 * En Apps Script todos los archivos de un proyecto comparten un unico espacio
 * de nombres global. Una `const` repetida entre dos archivos es un
 * SyntaxError que tumba el proyecto ENTERO --no corre `onOpen`, no aparece
 * ningun menu, y Ejecuciones no muestra nada--. Ya paso dos veces en la
 * planilla de logistica (ver apps_script/README.md). Por eso aca no hay un
 * solo nombre de nivel superior sin prefijo.
 *
 * Y NO SE DEFINE `onOpen`: si el proyecto ya tiene uno, el segundo lo
 * reemplaza en silencio y se pierden los menus que ya habia. Para tener menu,
 * llamar a `ccAgregarMenu(ui)` desde el `onOpen` que ya exista. Igual el
 * diagnostico se puede correr derecho desde el editor.
 */

// ===========================================================================
//  LO QUE SE EDITA CUANDO CAMBIA LA PLANILLA
// ===========================================================================
//
// `hoja`     nombres posibles de la pestania. Se compara sin acentos, sin
//            mayusculas y sin espacios de mas.
// `tabla`    la tabla en el esquema `bronze`.
// `clave`    las columnas de su clave primaria. De ahi sale el `on_conflict`
//            del upsert, asi que tienen que ser EXACTAMENTE las de Postgres.
// `columnas` por cada columna de Supabase: con que encabezados puede venir,
//            de que tipo es y si es obligatoria.
//
// Tipos: "texto" | "numero" | "fecha".
//
// OJO CON `texto` EN LAS COLUMNAS DE CODIGO. El CUIT y el codigo de cliente
// van como texto a proposito: tratados como numero pierden los ceros a la
// izquierda, y un CUIT sin su cero adelante no cruza contra nada. Es el mismo
// problema que ya habia aparecido en `Codigo.gs` con el CAE.

const CC_DESTINO = [
  {
    clave_interna: "scoring",
    hoja: ["Scoring", "Scoring Clientes", "Resumen"],
    tabla: "cuentas_corrientes_scoring",
    clave: ["cuit"],
    columnas: {
      cuit:                { alias: ["CUIT", "Cuit", "CUIT Cliente"], tipo: "texto", requerida: true },
      categoria:           { alias: ["Categoria", "Categoría", "Scoring"], tipo: "texto" },
      vendedor:            { alias: ["Vendedor"], tipo: "texto" },
      razon_social:        { alias: ["Razon Social", "Razón Social", "Cliente"], tipo: "texto" },
      empresa:             { alias: ["Empresa"], tipo: "texto" },
      saldo_total:         { alias: ["Saldo Total", "Saldo"], tipo: "numero" },
      saldo_vencido:       { alias: ["Saldo Vencido", "Vencido"], tipo: "numero" },
      saldo_por_vencer:    { alias: ["Saldo por Vencer", "Por Vencer", "A Vencer"], tipo: "numero" },
      pct_exposicion:      { alias: ["% Exposicion", "% Exposición", "Exposicion", "Exposición"], tipo: "numero" },
      fact_pendientes:     { alias: ["Fact Pendientes", "Facturas Pendientes", "Pendientes"], tipo: "numero" },
      fact_vencidas:       { alias: ["Fact Vencidas", "Facturas Vencidas", "Vencidas"], tipo: "numero" },
      fact_mayor_45:       { alias: ["Fact Mayor 45", "Facturas > 45", "Mayor 45", "+45"], tipo: "numero" },
      atraso_max:          { alias: ["Atraso Max", "Atraso Máximo", "Dias Atraso Max"], tipo: "numero" },
      atraso_prom_general: { alias: ["Atraso Prom General", "Atraso Promedio", "Atraso Prom"], tipo: "numero" },
      meses_en_mora_hist:  { alias: ["Meses en Mora Hist", "Meses en Mora", "Meses Mora"], tipo: "texto" },
      peor_categoria_hist: { alias: ["Peor Categoria Hist", "Peor Categoría", "Peor Categoria"], tipo: "texto" },
      tendencia:           { alias: ["Tendencia"], tipo: "texto" },
    },
  },
  {
    clave_interna: "aging",
    hoja: ["Aging", "Antiguedad", "Antigüedad", "Comprobantes"],
    tabla: "cuentas_corrientes_aging",
    clave: ["empresa", "comprobante"],
    columnas: {
      comprobante:   { alias: ["Comprobante", "Nro Comprobante", "Factura"], tipo: "texto", requerida: true },
      empresa:       { alias: ["Empresa"], tipo: "texto", requerida: true },
      cuit:          { alias: ["CUIT", "Cuit"], tipo: "texto" },
      razon_social:  { alias: ["Razon Social", "Razón Social", "Cliente"], tipo: "texto" },
      vendedor:      { alias: ["Vendedor"], tipo: "texto" },
      direccion:     { alias: ["Direccion", "Dirección", "Domicilio"], tipo: "texto" },
      fecha:         { alias: ["Fecha", "Fecha Emision", "Fecha Emisión"], tipo: "fecha" },
      vencimiento:   { alias: ["Vencimiento", "Fecha Vencimiento", "Vto"], tipo: "fecha" },
      atraso:        { alias: ["Atraso", "Dias Atraso", "Días de Atraso"], tipo: "numero" },
      total:         { alias: ["Total", "Importe", "Importe Total"], tipo: "numero" },
      pendiente:     { alias: ["Pendiente", "Saldo Pendiente", "Saldo"], tipo: "numero" },
      observaciones: { alias: ["Observaciones", "Obs", "Comentarios"], tipo: "texto" },
    },
  },
  {
    clave_interna: "cancelaciones",
    hoja: ["Cancelaciones", "Moras Canceladas"],
    tabla: "cuentas_corrientes_cancelaciones",
    clave: ["cuit", "periodo_con_mora"],
    columnas: {
      cuit:                    { alias: ["CUIT", "Cuit"], tipo: "texto", requerida: true },
      periodo_con_mora:        { alias: ["Periodo con Mora", "Período con Mora", "Periodo"], tipo: "texto", requerida: true },
      fecha_deteccion:         { alias: ["Fecha Deteccion", "Fecha Detección", "Detectado"], tipo: "fecha" },
      cliente_id:              { alias: ["Cliente ID", "Codigo Cliente", "Código Cliente", "Cod Cliente"], tipo: "texto" },
      razon_social:            { alias: ["Razon Social", "Razón Social", "Cliente"], tipo: "texto" },
      vendedor:                { alias: ["Vendedor"], tipo: "texto" },
      empresa:                 { alias: ["Empresa"], tipo: "texto" },
      saldo_vencido_cancelado: { alias: ["Saldo Vencido Cancelado", "Saldo Cancelado", "Cancelado"], tipo: "numero" },
      categoria_que_tenia:     { alias: ["Categoria que Tenia", "Categoría que Tenía", "Categoria Previa"], tipo: "texto" },
      dias_atraso_max:         { alias: ["Dias Atraso Max", "Días Atraso Máx", "Atraso Max"], tipo: "numero" },
      dias_atraso_prom:        { alias: ["Dias Atraso Prom", "Días Atraso Prom", "Atraso Prom"], tipo: "numero" },
      tipo_cancelacion:        { alias: ["Tipo Cancelacion", "Tipo Cancelación", "Tipo"], tipo: "texto" },
    },
  },
];

const CC_LOTE = 500;

// ===========================================================================
//  MENU (opcional: ver el comentario del encabezado sobre `onOpen`)
// ===========================================================================

function ccAgregarMenu(ui) {
  ui.createMenu("Cuentas Corrientes")
    .addItem("1. Revisar la planilla (no sube nada)", "ccRevisar")
    .addSeparator()
    .addItem("2. Subir todo a Supabase", "ccSubir")
    .addToUi();
}

// ===========================================================================
//  LECTURA DE LA PLANILLA
// ===========================================================================

/** Para comparar encabezados: sin acentos, sin mayusculas, sin espacios de mas. */
function ccNormalizar_(texto) {
  return String(texto == null ? "" : texto)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** La hoja cuyo nombre coincida con alguno de los alias. */
function ccBuscarHoja_(libro, nombres) {
  const buscados = nombres.map(ccNormalizar_);
  const hojas = libro.getSheets();
  for (let i = 0; i < hojas.length; i++) {
    if (buscados.indexOf(ccNormalizar_(hojas[i].getName())) !== -1) return hojas[i];
  }
  return null;
}

/**
 * En que indice de los encabezados cae cada columna de Supabase.
 *
 * Devuelve { indices: {columna: i}, sinMapear: [...] }. Una columna que no
 * aparece NO es un error por si sola --la planilla puede no tener
 * observaciones-- pero si es `requerida` no se puede subir nada: sin clave
 * primaria el upsert no sabe contra que comparar.
 */
function ccMapearColumnas_(encabezados, columnas) {
  const normalizados = encabezados.map(ccNormalizar_);
  const indices = {};
  const sinMapear = [];

  Object.keys(columnas).forEach(function (columna) {
    const alias = columnas[columna].alias.concat([columna]);
    let donde = -1;
    for (let i = 0; i < alias.length && donde === -1; i++) {
      donde = normalizados.indexOf(ccNormalizar_(alias[i]));
    }
    if (donde === -1) sinMapear.push(columna);
    else indices[columna] = donde;
  });

  return { indices: indices, sinMapear: sinMapear };
}

function ccTexto_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  return String(valor).trim() || null;
}

/**
 * Un numero de la planilla. Acepta "$ 1.234,50" y "1,234.50".
 *
 * Lo que NO hace es devolver 0 cuando no entiende: devuelve null. Un cero
 * inventado se suma en el tablero y no se nota; un null se ve.
 */
function ccNumero_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  if (typeof valor === "number") return isFinite(valor) ? valor : null;

  let texto = String(valor).replace(/[^0-9,.\-]/g, "");
  if (!texto) return null;

  const coma = texto.lastIndexOf(",");
  const punto = texto.lastIndexOf(".");
  if (coma > punto) {
    // Formato argentino: el ultimo separador es la coma decimal.
    texto = texto.replace(/\./g, "").replace(",", ".");
  } else {
    texto = texto.replace(/,/g, "");
  }

  const numero = parseFloat(texto);
  return isFinite(numero) ? numero : null;
}

/**
 * Una fecha como "yyyy-MM-dd".
 *
 * Las columnas de fecha en Supabase son TEXTO, no `date`. Se respeta eso: el
 * tablero ya las castea con `::date` donde las necesita, y cambiar el tipo
 * ahora obligaria a migrar las cinco tablas y las consultas que las leen.
 */
function ccFecha_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  if (Object.prototype.toString.call(valor) === "[object Date]") {
    if (isNaN(valor.getTime())) return null;
    return Utilities.formatDate(valor, "America/Argentina/Buenos_Aires", "yyyy-MM-dd");
  }
  const texto = String(valor).trim();
  // dd/MM/yyyy, que es como lo escribe la planilla cuando la celda es texto.
  const partes = /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/.exec(texto);
  if (partes) {
    const anio = partes[3].length === 2 ? "20" + partes[3] : partes[3];
    return anio + "-" + ("0" + partes[2]).slice(-2) + "-" + ("0" + partes[1]).slice(-2);
  }
  // Ya viene ISO.
  if (/^\d{4}-\d{2}-\d{2}/.test(texto)) return texto.slice(0, 10);
  return texto || null;
}

function ccConvertir_(valor, tipo) {
  if (tipo === "numero") return ccNumero_(valor);
  if (tipo === "fecha") return ccFecha_(valor);
  return ccTexto_(valor);
}

/**
 * Lee una hoja y devuelve { filas, sinMapear, descartadas, error }.
 *
 * `sello` es la marca de esta corrida: va en `fecha_carga` de cada fila y es
 * lo que despues permite borrar lo viejo SIN dejar la tabla vacia en el medio.
 */
function ccLeerHoja_(libro, destino, sello) {
  const hoja = ccBuscarHoja_(libro, destino.hoja);
  if (!hoja) {
    return { error: "No se encontro ninguna hoja con estos nombres: " + destino.hoja.join(" / ") };
  }

  const datos = hoja.getDataRange().getValues();
  if (datos.length < 2) {
    return { error: "La hoja '" + hoja.getName() + "' no tiene filas debajo del encabezado." };
  }

  const mapeo = ccMapearColumnas_(datos[0], destino.columnas);

  const faltanRequeridas = mapeo.sinMapear.filter(function (c) {
    return destino.columnas[c].requerida;
  });
  if (faltanRequeridas.length) {
    return {
      error: "Faltan columnas obligatorias en '" + hoja.getName() + "': " +
        faltanRequeridas.join(", ") +
        ".\nEncabezados encontrados: " + datos[0].join(" | "),
      sinMapear: mapeo.sinMapear,
    };
  }

  const filas = [];
  let descartadas = 0;
  for (let i = 1; i < datos.length; i++) {
    const cruda = datos[i];
    const fila = { fecha_carga: sello };
    Object.keys(mapeo.indices).forEach(function (columna) {
      fila[columna] = ccConvertir_(cruda[mapeo.indices[columna]], destino.columnas[columna].tipo);
    });

    // UNA FILA SIN CLAVE NO SE SUBE. El upsert la mandaria con la clave en
    // null y Postgres la rechazaria entera, llevandose el lote de 500. Suele
    // ser la fila de totales del final de la hoja.
    const sinClave = destino.clave.some(function (c) { return !fila[c]; });
    if (sinClave) { descartadas++; continue; }

    filas.push(fila);
  }

  return {
    hoja: hoja.getName(),
    filas: filas,
    sinMapear: mapeo.sinMapear,
    descartadas: descartadas,
    encabezados: datos[0],
  };
}

// ===========================================================================
//  REVISAR: lee todo y no escribe nada
// ===========================================================================

function ccRevisar() {
  const libro = SpreadsheetApp.getActiveSpreadsheet();
  const sello = new Date().toISOString();
  const partes = [];

  CC_DESTINO.forEach(function (destino) {
    const r = ccLeerHoja_(libro, destino, sello);
    partes.push("=== " + destino.tabla + " ===");
    if (r.error) {
      partes.push("  NO SE PUEDE SUBIR: " + r.error);
      partes.push("");
      return;
    }
    partes.push("  hoja: " + r.hoja);
    partes.push("  filas listas: " + r.filas.length +
      (r.descartadas ? "  (descartadas sin clave: " + r.descartadas + ")" : ""));
    if (r.sinMapear.length) {
      partes.push("  COLUMNAS QUE NO SE ENCONTRARON: " + r.sinMapear.join(", "));
      partes.push("  -> van a quedar vacias. Si la planilla las tiene con otro");
      partes.push("     nombre, agregalo al `alias` de esa columna.");
    } else {
      partes.push("  todas las columnas mapeadas");
    }
    partes.push("  encabezados: " + r.encabezados.join(" | "));
    if (r.filas.length) {
      partes.push("  primera fila: " + JSON.stringify(r.filas[0]));
    }
    partes.push("");
  });

  const texto = partes.join("\n");
  Logger.log(texto);
  try {
    SpreadsheetApp.getUi().alert(
      "Revision de la planilla\n\n" + texto.substring(0, 1400) +
      "\n\n(Completo en Ejecuciones -> Registro)"
    );
  } catch (e) {
    // Corrido desde el editor no hay UI: el Logger alcanza.
  }
  return texto;
}

// ===========================================================================
//  SUBIR
// ===========================================================================

function ccProp_(nombre) {
  return PropertiesService.getScriptProperties().getProperty(nombre);
}

/**
 * Sube una tabla: primero escribe lo nuevo, despues borra lo viejo.
 *
 * ESE ORDEN NO ES CAPRICHO, Y ES LO MAS IMPORTANTE DE ESTE ARCHIVO.
 *
 * Lo intuitivo seria borrar todo y despues insertar, porque estas tablas son
 * una FOTO: dicen como esta cada cliente hoy. Pero entre el borrado y la
 * insercion la tabla queda VACIA, y si la insercion falla --se corta la red,
 * expira el token, una fila rompe una restriccion-- queda vacia y punto. El
 * tablero de cuentas corrientes mostraria cero deuda, que no se lee como "se
 * rompio la carga" sino como "no debe nadie".
 *
 * Al reves es seguro porque las tres tablas tienen clave primaria: el upsert
 * PISA la fila de cada cliente que sigue estando. En el peor caso quedan de
 * mas los clientes que desaparecieron de la planilla --visible y arreglable--
 * en vez de faltar todos.
 */
function ccSubirTabla_(destino, filas, sello) {
  const url = ccProp_("SUPABASE_URL");
  const serviceKey = ccProp_("SUPABASE_SERVICE_KEY"); // service_role, NO la anon
  if (!url || !serviceKey) {
    throw new Error("Faltan las Script Properties SUPABASE_URL y/o SUPABASE_SERVICE_KEY.");
  }

  const base = url + "/rest/v1/" + destino.tabla;
  const cabeceras = {
    "apikey": serviceKey,
    "Authorization": "Bearer " + serviceKey,
    "Content-Profile": "bronze",   // las tablas viven en `bronze`, no en `public`
  };

  // 1) UPSERT de todo lo que trae la planilla.
  const endpoint = base + "?on_conflict=" + destino.clave.join(",");
  for (let i = 0; i < filas.length; i += CC_LOTE) {
    const lote = filas.slice(i, i + CC_LOTE);
    const resp = UrlFetchApp.fetch(endpoint, {
      method: "post",
      contentType: "application/json",
      headers: Object.assign({}, cabeceras, {
        "Prefer": "resolution=merge-duplicates,return=minimal",
      }),
      payload: JSON.stringify(lote),
      muteHttpExceptions: true,
    });
    const codigo = resp.getResponseCode();
    if (codigo < 200 || codigo >= 300) {
      throw new Error(
        destino.tabla + ": fallo el lote que arranca en la fila " + (i + 1) +
        " (HTTP " + codigo + ")\n" + resp.getContentText().substring(0, 300) +
        "\nNO se borro nada: la tabla quedo como estaba mas lo que si entro."
      );
    }
  }

  // 2) RECIEN AHORA se borra lo que no vino en esta carga.
  const borrado = UrlFetchApp.fetch(
    base + "?fecha_carga=neq." + encodeURIComponent(sello),
    {
      method: "delete",
      headers: Object.assign({}, cabeceras, { "Prefer": "return=minimal" }),
      muteHttpExceptions: true,
    }
  );
  const codigoBorrado = borrado.getResponseCode();
  if (codigoBorrado < 200 || codigoBorrado >= 300) {
    throw new Error(
      destino.tabla + ": se subio todo bien pero NO se pudo borrar lo viejo " +
      "(HTTP " + codigoBorrado + ").\n" + borrado.getContentText().substring(0, 300) +
      "\nPueden haber quedado clientes que ya no estan en la planilla."
    );
  }

  return filas.length;
}

function ccSubir() {
  const libro = SpreadsheetApp.getActiveSpreadsheet();
  // UN SELLO PARA LAS TRES TABLAS: asi las tres quedan fechadas con el mismo
  // momento y `foto_cuentas.py`, que fecha la foto diaria con el `fecha_carga`
  // de scoring, no ve tres horas distintas para una misma carga.
  const sello = new Date().toISOString();

  // SE LEE Y SE VALIDA TODO ANTES DE ESCRIBIR NADA. Si la tercera hoja tiene
  // un problema, es mucho mejor enterarse con las tres sin tocar que con dos
  // ya subidas y una no: eso deja la base contandose una historia a medias.
  const leidas = [];
  for (let i = 0; i < CC_DESTINO.length; i++) {
    const r = ccLeerHoja_(libro, CC_DESTINO[i], sello);
    if (r.error) {
      SpreadsheetApp.getUi().alert(
        "No se subio NADA.\n\n" + CC_DESTINO[i].tabla + ":\n" + r.error +
        "\n\nCorré 'Revisar la planilla' para ver el detalle."
      );
      return;
    }
    leidas.push({ destino: CC_DESTINO[i], lectura: r });
  }

  const resumen = [];
  for (let i = 0; i < leidas.length; i++) {
    try {
      const cuantas = ccSubirTabla_(leidas[i].destino, leidas[i].lectura.filas, sello);
      resumen.push("✅ " + leidas[i].destino.tabla + ": " + cuantas + " filas");
    } catch (e) {
      resumen.push("❌ " + String(e.message || e));
      SpreadsheetApp.getUi().alert(resumen.join("\n\n"));
      return;
    }
  }

  SpreadsheetApp.getUi().alert(resumen.join("\n"));
}
