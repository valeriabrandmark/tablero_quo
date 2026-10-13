/**
 * CUENTAS CORRIENTES -> Supabase
 *
 * Sube tres hojas de la planilla "Analisis de cuenta corriente" a `bronze`:
 *
 *   Scoring_Clientes          -> cuentas_corrientes_scoring
 *   Aging_CC_0001_0003        -> cuentas_corrientes_aging
 *   Historial_Cancelaciones   -> cuentas_corrientes_cancelaciones
 *
 * Esas hojas las genera `Sigma_AgingCtaCte.gs` desde la API de Sigma. Este
 * archivo NO las calcula: solo las lee y las manda. Y las dos tablas de
 * historial de Supabase (`_historial_diario` y `_historial_scoring`) las
 * escribe `foto_cuentas.py` desde el orquestador, no se tocan desde aca.
 *
 * =========================================================================
 *  LO QUE HAY QUE SABER ANTES DE TOCAR ESTO
 * =========================================================================
 *
 * EL ENCABEZADO NO ESTA EN LA FILA 1. `Scoring_Clientes` y
 * `Aging_CC_0001_0003` arrancan con una fila combinada que dice "Ultima
 * actualizacion: ...", y los nombres de columna estan en la fila 2.
 * `Historial_Cancelaciones` si los tiene en la 1. Por eso la fila de
 * encabezados SE BUSCA en vez de darse por sentada: se prueban las primeras
 * filas y gana la que reconoce mas columnas.
 *
 * EL AGING NO ES SOLO FACTURAS. Trae tres clases de fila mezcladas:
 *
 *   facturas   lo normal
 *   recibos    comprobante REC-xxxx, importes NEGATIVOS, sin CUIT ni atraso.
 *              SE SUBEN: ya estan en Supabase y son los pagos a cuenta sin
 *              aplicar, que es justo lo que explica que un saldo no cierre.
 *   SUBTOTAL   filas de corte por vendedor. NO se suben: son un total, y
 *              subirlas duplicaria la mitad de la cartera.
 *
 * LA EMPRESA VIENE COMO NUMERO EN EL AGING. La planilla le pone formato de
 * texto a la columna Vendedor pero no a Empresa, asi que donde Scoring dice
 * "0001" el Aging dice 1. Como la clave primaria de `aging` es
 * (empresa, comprobante), subir "1" en vez de "0001" no pisa la fila de
 * antes: la DUPLICA. Por eso hay un tipo `empresa` que rellena con ceros.
 *
 * =========================================================================
 *  PREFIJO `cc` / `CC_` EN TODO
 * =========================================================================
 *
 * En Apps Script todos los archivos comparten un unico espacio de nombres.
 * Una `const` repetida entre dos archivos es un SyntaxError que tumba el
 * proyecto entero. Ya paso dos veces en logistica (ver README).
 *
 * Este proyecto ya declara `CFG` y `PESO` (Sigma_AgingCtaCte.gs) y tiene su
 * `onOpen` con el menu "Sigma CC". Nada de lo de aca se llama igual, y NO se
 * define `onOpen`: para tener menu, agregar al que ya existe
 *
 *     .addItem('☁️ Subir cuentas corrientes a Supabase', 'ccSubir')
 *
 * o llamar a `ccAgregarMenu(SpreadsheetApp.getUi())`. El diagnostico tambien
 * se puede correr derecho desde el editor.
 */

// ===========================================================================
//  LO QUE SE EDITA CUANDO CAMBIA LA PLANILLA
// ===========================================================================
//
// Tipos: "texto" | "numero" | "fecha" | "empresa" | "vendedor".
//
// `empresa` y `vendedor` rellenan con ceros a la izquierda (4 y 3 digitos).
// Van como texto a proposito, igual que el CUIT: un codigo que pierde el cero
// de adelante deja de cruzar contra nada.

const CC_DESTINO = [
  {
    clave_interna: "scoring",
    hoja: ["Scoring_Clientes", "Scoring Clientes", "Scoring"],
    tabla: "cuentas_corrientes_scoring",
    clave: ["cuit"],
    columnas: {
      cuit:                { alias: ["CUIT"], tipo: "texto", requerida: true },
      categoria:           { alias: ["Categoría", "Categoria"], tipo: "texto" },
      vendedor:            { alias: ["Vendedor"], tipo: "vendedor" },
      razon_social:        { alias: ["Razón Social", "Razon Social"], tipo: "texto" },
      empresa:             { alias: ["Empresa"], tipo: "empresa" },
      saldo_total:         { alias: ["Saldo Total ($)", "Saldo Total"], tipo: "numero" },
      saldo_vencido:       { alias: ["Saldo Vencido ($)", "Saldo Vencido"], tipo: "numero" },
      saldo_por_vencer:    { alias: ["Saldo por Vencer ($)", "Saldo por Vencer"], tipo: "numero" },
      pct_exposicion:      { alias: ["% Exposición", "% Exposicion"], tipo: "numero" },
      fact_pendientes:     { alias: ["Fact. Pendientes"], tipo: "numero" },
      fact_vencidas:       { alias: ["Fact. Vencidas"], tipo: "numero" },
      // OJO CON ESTA. La columna de Supabase se llama `fact_mayor_45` pero la
      // planilla nueva cuenta las de MAS DE 30 DIAS: `Sigma_AgingCtaCte.gs`
      // hace `if (atraso > 30) c.cantMoraReal++`. El nombre quedo de cuando
      // el corte era 45. Se acepta el encabezado viejo y el nuevo para no
      // depender de cual este, pero el numero que entra es el del corte que
      // use la planilla EN ESE MOMENTO.
      fact_mayor_45:       { alias: ["Fact. > 30 días", "Fact. > 30 dias",
                                     "Fact. > 45 días", "Fact. > 45 dias"], tipo: "numero" },
      atraso_max:          { alias: ["Atraso Máx (días)", "Atraso Max (dias)", "Atraso Máx"], tipo: "numero" },
      atraso_prom_general: { alias: ["Atraso Prom General (días)", "Atraso Prom General (dias)",
                                     "Atraso Prom General"], tipo: "numero" },
      meses_en_mora_hist:  { alias: ["Meses en mora (últ. 6)", "Meses en mora (ult. 6)"], tipo: "texto" },
      peor_categoria_hist: { alias: ["Peor categoría (últ. 6)", "Peor categoria (ult. 6)"], tipo: "texto" },
      tendencia:           { alias: ["Tendencia"], tipo: "texto" },
    },
  },
  {
    clave_interna: "aging",
    hoja: ["Aging_CC_0001_0003", "Aging"],
    tabla: "cuentas_corrientes_aging",
    clave: ["empresa", "comprobante"],
    // Las filas de corte por vendedor. Son un TOTAL, no un comprobante.
    excluir: { columna: "razon_social", valores: ["SUBTOTAL"] },
    columnas: {
      comprobante:   { alias: ["Comprobante"], tipo: "texto", requerida: true },
      empresa:       { alias: ["Empresa"], tipo: "empresa", requerida: true },
      cuit:          { alias: ["CUIT"], tipo: "texto" },
      razon_social:  { alias: ["Razón Social", "Razon Social"], tipo: "texto" },
      vendedor:      { alias: ["Vendedor"], tipo: "vendedor" },
      direccion:     { alias: ["Dirección", "Direccion"], tipo: "texto" },
      fecha:         { alias: ["Fecha"], tipo: "fecha" },
      vencimiento:   { alias: ["Vencimiento"], tipo: "fecha" },
      atraso:        { alias: ["Atraso (días)", "Atraso (dias)", "Atraso"], tipo: "numero" },
      total:         { alias: ["Total"], tipo: "numero" },
      pendiente:     { alias: ["Pendiente"], tipo: "numero" },
      observaciones: { alias: ["Observaciones"], tipo: "texto" },
    },
  },
  {
    clave_interna: "cancelaciones",
    hoja: ["Historial_Cancelaciones", "Cancelaciones"],
    tabla: "cuentas_corrientes_cancelaciones",
    clave: ["cuit", "periodo_con_mora"],
    columnas: {
      cuit:                    { alias: ["CUIT"], tipo: "texto", requerida: true },
      periodo_con_mora:        { alias: ["Período con Mora", "Periodo con Mora", "Período", "Periodo"], tipo: "texto", requerida: true },
      fecha_deteccion:         { alias: ["Fecha Detección", "Fecha Deteccion"], tipo: "fecha" },
      cliente_id:              { alias: ["ClienteId", "Cliente Id", "Cliente ID"], tipo: "texto" },
      razon_social:            { alias: ["Razón Social", "Razon Social"], tipo: "texto" },
      vendedor:                { alias: ["Vendedor"], tipo: "vendedor" },
      empresa:                 { alias: ["Empresa"], tipo: "empresa" },
      saldo_vencido_cancelado: { alias: ["Saldo Vencido Cancelado ($)", "Saldo Vencido Cancelado"], tipo: "numero" },
      categoria_que_tenia:     { alias: ["Categoría que Tenía", "Categoria que Tenia"], tipo: "texto" },
      dias_atraso_max:         { alias: ["Días Atraso Máx al Cancelar", "Dias Atraso Max al Cancelar"], tipo: "numero" },
      dias_atraso_prom:        { alias: ["Días Atraso Prom al Cancelar", "Dias Atraso Prom al Cancelar"], tipo: "numero" },
      tipo_cancelacion:        { alias: ["Tipo de Cancelación", "Tipo de Cancelacion"], tipo: "texto" },
    },
  },
];

const CC_LOTE = 500;

// Cuantas filas se prueban buscando el encabezado. Con 5 sobra: la planilla
// usa la 1 o la 2.
const CC_FILAS_ENCABEZADO = 5;

// ===========================================================================
//  MENU (opcional: ver el comentario del encabezado sobre `onOpen`)
// ===========================================================================

function ccAgregarMenu(ui) {
  ui.createMenu("Cuentas Corrientes → Supabase")
    .addItem("1. Revisar la planilla (no sube nada)", "ccRevisar")
    .addSeparator()
    .addItem("2. Subir a Supabase", "ccSubir")
    .addToUi();
}

// ===========================================================================
//  LECTURA
// ===========================================================================

/** Para comparar encabezados: sin acentos, sin mayusculas, sin espacios. */
function ccNormalizar_(texto) {
  return String(texto == null ? "" : texto)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function ccBuscarHoja_(libro, nombres) {
  const buscados = nombres.map(ccNormalizar_);
  const hojas = libro.getSheets();
  for (let i = 0; i < hojas.length; i++) {
    if (buscados.indexOf(ccNormalizar_(hojas[i].getName())) !== -1) return hojas[i];
  }
  return null;
}

function ccMapearColumnas_(encabezados, columnas) {
  const normalizados = (encabezados || []).map(ccNormalizar_);
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

/**
 * En que fila estan los encabezados.
 *
 * NO SE DA POR SENTADO QUE ES LA 1. `Scoring_Clientes` y `Aging_CC_0001_0003`
 * arrancan con una fila combinada ("Ultima actualizacion: ...") y los nombres
 * de columna estan en la 2; `Historial_Cancelaciones` si los tiene en la 1.
 * Leyendo siempre la 1 se mapearia cero columnas y el error seria "faltan
 * columnas obligatorias", que manda a buscar el problema donde no esta.
 *
 * Gana la fila que reconoce MAS columnas. Si empatan, la primera.
 */
function ccFilaDeEncabezados_(datos, columnas) {
  let mejor = 0;
  let mejorCuantas = -1;
  const hasta = Math.min(CC_FILAS_ENCABEZADO, datos.length);
  for (let i = 0; i < hasta; i++) {
    const cuantas = Object.keys(ccMapearColumnas_(datos[i], columnas).indices).length;
    if (cuantas > mejorCuantas) { mejorCuantas = cuantas; mejor = i; }
  }
  return mejor;
}

function ccTexto_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  return String(valor).trim() || null;
}

/**
 * Un codigo con ceros a la izquierda: empresa (4) y vendedor (3).
 *
 * La planilla escribe la empresa del Aging como NUMERO --dice 1 donde el
 * Scoring dice "0001"-- porque solo le pone formato de texto a la columna
 * Vendedor. Como la clave primaria de `aging` es (empresa, comprobante),
 * subir "1" no pisa la fila de antes: la duplica.
 */
function ccCodigo_(valor, largo) {
  if (valor === null || valor === undefined || valor === "") return null;
  const soloNumeros = String(valor).replace(/[^0-9]/g, "");
  if (!soloNumeros) return ccTexto_(valor);
  const numero = parseInt(soloNumeros, 10);
  if (!isFinite(numero)) return ccTexto_(valor);
  let texto = String(numero);
  while (texto.length < largo) texto = "0" + texto;
  return texto;
}

/**
 * Un numero de la planilla. Acepta "$ 1.234,50" y "1,234.50".
 *
 * Lo que NO hace es devolver 0 cuando no entiende: devuelve null. Un cero
 * inventado se suma en el tablero y no se nota; un null se ve. Importa
 * especialmente en el Aging, donde los recibos vienen SIN atraso: ese vacio
 * es "no aplica", no "cero dias de atraso".
 */
function ccNumero_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  if (typeof valor === "number") return isFinite(valor) ? valor : null;

  let texto = String(valor).replace(/[^0-9,.\-]/g, "");
  if (!texto || texto === "-") return null;

  const coma = texto.lastIndexOf(",");
  const punto = texto.lastIndexOf(".");
  if (coma > punto) {
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
 * Las columnas de fecha en Supabase son TEXTO, no `date`. Se respeta: el
 * tablero ya las castea con `::date` donde las necesita, y cambiar el tipo
 * obligaria a migrar las cinco tablas y las consultas que las leen.
 */
function ccFecha_(valor) {
  if (valor === null || valor === undefined || valor === "") return null;
  if (Object.prototype.toString.call(valor) === "[object Date]") {
    if (isNaN(valor.getTime())) return null;
    return Utilities.formatDate(valor, "America/Argentina/Buenos_Aires", "yyyy-MM-dd");
  }
  const texto = String(valor).trim();
  const partes = /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/.exec(texto);
  if (partes) {
    const anio = partes[3].length === 2 ? "20" + partes[3] : partes[3];
    return anio + "-" + ("0" + partes[2]).slice(-2) + "-" + ("0" + partes[1]).slice(-2);
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(texto)) return texto.slice(0, 10);
  return texto || null;
}

function ccConvertir_(valor, tipo) {
  if (tipo === "numero") return ccNumero_(valor);
  if (tipo === "fecha") return ccFecha_(valor);
  if (tipo === "empresa") return ccCodigo_(valor, 4);
  if (tipo === "vendedor") return ccCodigo_(valor, 3);
  return ccTexto_(valor);
}

/** Lee una hoja. `sello` va en `fecha_carga` de cada fila. */
function ccLeerHoja_(libro, destino, sello) {
  const hoja = ccBuscarHoja_(libro, destino.hoja);
  if (!hoja) {
    return { error: "No se encontro ninguna hoja con estos nombres: " + destino.hoja.join(" / ") };
  }

  const datos = hoja.getDataRange().getValues();
  if (datos.length < 2) {
    return { error: "La hoja '" + hoja.getName() + "' no tiene datos." };
  }

  const filaEnc = ccFilaDeEncabezados_(datos, destino.columnas);
  const mapeo = ccMapearColumnas_(datos[filaEnc], destino.columnas);

  const faltanRequeridas = mapeo.sinMapear.filter(function (c) {
    return destino.columnas[c].requerida;
  });
  if (faltanRequeridas.length) {
    return {
      error: "Faltan columnas obligatorias en '" + hoja.getName() + "': " +
        faltanRequeridas.join(", ") +
        "\nEncabezados leidos (fila " + (filaEnc + 1) + "): " + datos[filaEnc].join(" | "),
      sinMapear: mapeo.sinMapear,
    };
  }

  const filas = [];
  let excluidas = 0;
  let sinClave = 0;

  for (let i = filaEnc + 1; i < datos.length; i++) {
    const cruda = datos[i];
    const fila = { fecha_carga: sello };
    Object.keys(mapeo.indices).forEach(function (columna) {
      fila[columna] = ccConvertir_(cruda[mapeo.indices[columna]], destino.columnas[columna].tipo);
    });

    // Las filas de corte del Aging. Son un TOTAL por vendedor: subirlas
    // duplicaria la mitad de la cartera, sumada otra vez.
    if (destino.excluir) {
      const valor = ccNormalizar_(fila[destino.excluir.columna]);
      const prohibidos = destino.excluir.valores.map(ccNormalizar_);
      if (prohibidos.indexOf(valor) !== -1) { excluidas++; continue; }
    }

    // Sin clave no se sube: el upsert la mandaria en null, Postgres
    // rechazaria el lote entero de 500 y se perderian las 499 buenas.
    if (destino.clave.some(function (c) { return !fila[c]; })) { sinClave++; continue; }

    filas.push(fila);
  }

  return {
    hoja: hoja.getName(),
    fila_encabezado: filaEnc + 1,
    filas: filas,
    sinMapear: mapeo.sinMapear,
    excluidas: excluidas,
    sinClave: sinClave,
    encabezados: datos[filaEnc],
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
    partes.push("  hoja: " + r.hoja + "  (encabezados en la fila " + r.fila_encabezado + ")");
    partes.push("  filas listas: " + r.filas.length +
      (r.excluidas ? "  | excluidas (SUBTOTAL): " + r.excluidas : "") +
      (r.sinClave ? "  | sin clave: " + r.sinClave : ""));
    if (r.sinMapear.length) {
      partes.push("  COLUMNAS QUE NO SE ENCONTRARON: " + r.sinMapear.join(", "));
      partes.push("  -> van a quedar vacias. Si la planilla las tiene con otro");
      partes.push("     nombre, agregalo al `alias` de esa columna.");
    } else {
      partes.push("  todas las columnas mapeadas");
    }
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
 * Sube una tabla: PRIMERO escribe lo nuevo, DESPUES borra lo viejo.
 *
 * Ese orden es lo mas importante de este archivo.
 *
 * Lo intuitivo seria borrar todo y despues insertar, porque estas tablas son
 * una foto: dicen como esta cada cliente hoy. Pero entre el borrado y la
 * insercion la tabla queda VACIA, y si la insercion falla --se corta la red,
 * expira el token, una fila rompe una restriccion-- queda vacia y punto. El
 * tablero mostraria cero deuda, que no se lee como "fallo la carga" sino como
 * "no debe nadie".
 *
 * Al reves es seguro porque las tres tablas tienen clave primaria: el upsert
 * PISA la fila de cada cliente que sigue estando. En el peor caso quedan de
 * mas los que desaparecieron de la planilla --visible y arreglable-- en vez
 * de faltar todos.
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

  for (let i = 0; i < filas.length; i += CC_LOTE) {
    const lote = filas.slice(i, i + CC_LOTE);
    const resp = UrlFetchApp.fetch(base + "?on_conflict=" + destino.clave.join(","), {
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
  // UN SELLO PARA LAS TRES: asi quedan fechadas con el mismo momento y
  // `foto_cuentas.py`, que fecha la foto diaria con el `fecha_carga` de
  // scoring, no ve tres horas distintas para una misma carga.
  const sello = new Date().toISOString();

  // SE LEE Y SE VALIDA TODO ANTES DE ESCRIBIR NADA. Si la tercera hoja tiene
  // un problema, es mejor enterarse con las tres sin tocar que con dos ya
  // subidas: eso deja la base contandose una historia a medias.
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
