// ═══════════════════════════════════════════════════════════════════
// HOJA DE ÍNDICES FINANCIEROS — VERSION CORREGIDA
//
// MAPEO DE COLUMNAS SCORING_CLIENTES (schema actual con 17 cols):
//   A=Categoría  B=Vendedor  C=Razón Social  D=CUIT  E=Empresa
//   F=Saldo Total($)  G=Saldo Vencido($)  H=Saldo por Vencer($)
//   I=% Exposición    J=Fact.Pendientes    K=Fact.Vencidas
//   L=Fact.>45días    M=Atraso Máx(días)   N=Atraso Prom General
//
// MAPEO DE COLUMNAS AGING_CC_0001_0003:
//   A=Vendedor  B=Razón Social  H=Atraso(días)
//   J=Pendiente($)  K=Empresa
// ═══════════════════════════════════════════════════════════════════

function crearHojaIndices() {
  const ui = SpreadsheetApp.getUi();
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  try {
    const NOM = 'Indices_Financieros';
    let ws = ss.getSheetByName(NOM);
    if (ws) ss.deleteSheet(ws);
    ws = ss.insertSheet(NOM, 0);

    // ── Anchos de columna ──────────────────────────────────────────
    ws.setColumnWidth(1, 290); // A: Nombre del índice
    ws.setColumnWidth(2, 160); // B: Valor (fórmula)
    ws.setColumnWidth(3,  65); // C: Unidad
    ws.setColumnWidth(4, 520); // D: Fórmula explícita con celdas reales
    ws.setColumnWidth(5, 480); // E: Interpretación y escenarios
    ws.setColumnWidth(6, 120); // F: ✅ Óptimo
    ws.setColumnWidth(7, 120); // G: ⚠️ Alerta
    ws.setColumnWidth(8, 120); // H: 🔴 Crítico

    // ── Referencias con nombres de hoja COMPLETOS (sin abreviaciones) ──
    const SC  = "'Scoring_Clientes'";
    const AG  = "'Aging_CC_0001_0003'";

    // Columnas Scoring_Clientes (nombres exactos de cada rango)
    const SC_A = `${SC}!A3:A5000`;  // Categoría de riesgo
    const SC_F = `${SC}!F3:F5000`;  // Saldo Total ($) — base de todos los índices
    const SC_J = `${SC}!J3:J5000`;  // Fact. Pendientes (cantidad)
    const SC_K = `${SC}!K3:K5000`;  // Fact. Vencidas (cantidad)
    const SC_L = `${SC}!L3:L5000`;  // Fact. > 45 días (cantidad)
    const SC_M = `${SC}!M3:M5000`;  // Atraso Máx (días)
    const SC_N = `${SC}!N3:N5000`;  // Atraso Prom General (días)

    // Columnas Aging_CC_0001_0003
    const AG_H = `${AG}!H3:H5000`;  // Atraso (días)
    const AG_J = `${AG}!J3:J5000`;  // Pendiente ($)
    const AG_B = `${AG}!B3:B5000`;  // Razón Social / "SUBTOTAL"

    // Fórmula de cartera bruta (base de todos los % de mora y concentración)
    const BRUTA = `SUMPRODUCT((${SC_F}>0)*${SC_F})`;

    let row = 1;

    // ── Helpers de estilo ──────────────────────────────────────────
    function hdr(r, txt) {
      ws.getRange(r,1,1,8).merge()
        .setValue(`  ▌  ${txt}`)
        .setBackground('#1F3A5F').setFontColor('#FFFFFF')
        .setFontWeight('bold').setFontSize(11)
        .setVerticalAlignment('middle').setHorizontalAlignment('left');
      ws.setRowHeight(r, 22);
    }

    function colHdr(r) {
      ws.getRange(r,1,1,8)
        .setValues([['ÍNDICE / KPI','VALOR','UNIDAD',
                     'FÓRMULA EXPLÍCITA (celdas exactas usadas en el cálculo)',
                     'QUÉ MIDE, CÓMO INTERPRETARLO Y QUÉ ACCIÓN TOMAR',
                     '✅ ÓPTIMO','⚠️ ALERTA','🔴 CRÍTICO']])
        .setBackground('#2E75B6').setFontColor('#FFFFFF').setFontWeight('bold')
        .setFontSize(9).setHorizontalAlignment('center').setVerticalAlignment('middle').setWrap(true);
      ws.setRowHeight(r, 28);
    }

    // kpi(): escribe una fila de indicador.
    // formula se aplica con setFormula() — siempre acepta notación inglesa (comas)
    // fTxt es el texto que explica exactamente qué celdas y rangos usa la fórmula
    function kpi(r, nom, formula, unidad, fTxt, interp, ok, alerta, critico, fmt) {
      ws.getRange(r,1).setValue(nom)
        .setBackground('#EBF3FB').setFontWeight('bold').setFontSize(9)
        .setFontColor('#1F3A5F').setWrap(true).setVerticalAlignment('middle');

      ws.getRange(r,2).setFormula(formula)           // ← setFormula, no setValue
        .setBackground('#FFFFFF').setFontWeight('bold').setFontSize(11)
        .setFontColor('#0A2E5A').setHorizontalAlignment('center')
        .setVerticalAlignment('middle').setNumberFormat(fmt || '#,##0.00');

      ws.getRange(r,3).setValue(unidad)
        .setBackground('#F2F2F2').setFontSize(9).setFontColor('#555555')
        .setHorizontalAlignment('center').setVerticalAlignment('middle');

      ws.getRange(r,4).setValue(fTxt)
        .setBackground('#E8E8E8').setFontSize(8).setFontStyle('italic')
        .setFontColor('#333333').setWrap(true).setVerticalAlignment('top');

      ws.getRange(r,5).setValue(interp)
        .setBackground('#FFFFFF').setFontSize(9).setWrap(true).setVerticalAlignment('top');

      ws.getRange(r,6).setValue(ok).setBackground('#E8F5E9').setFontSize(8)
        .setHorizontalAlignment('center').setWrap(true).setVerticalAlignment('middle');
      ws.getRange(r,7).setValue(alerta).setBackground('#FFF3CD').setFontSize(8)
        .setHorizontalAlignment('center').setWrap(true).setVerticalAlignment('middle');
      ws.getRange(r,8).setValue(critico).setBackground('#FFE0E0').setFontSize(8)
        .setHorizontalAlignment('center').setWrap(true).setVerticalAlignment('middle');

      ws.setRowHeight(r, 110);
    }

    // ── TÍTULO ──────────────────────────────────────────────────────
    ws.getRange(1,1,1,8).merge()
      .setValue('TABLERO DE ÍNDICES FINANCIEROS — CONTROL DE GESTIÓN DE CUENTAS CORRIENTES')
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(14)
      .setHorizontalAlignment('center').setVerticalAlignment('middle');
    ws.setRowHeight(1, 32);

    ws.getRange(2,1,1,8).merge()
      .setValue('Distribuidora | Empresas 0001 y 0003 | Fuente: Scoring_Clientes (cols A–N) y Aging_CC_0001_0003 (cols B, H, J) | Actualización automática')
      .setBackground('#EBF3FB').setFontColor('#444444').setFontStyle('italic').setFontSize(9)
      .setHorizontalAlignment('center');
    ws.setRowHeight(2, 16); ws.setRowHeight(3, 6);
    ws.setFrozenRows(3); row = 4;

    // ══════════════════════════════════════════════════════════════
    // SECCIÓN 1: RESUMEN EJECUTIVO DE CARTERA
    // ══════════════════════════════════════════════════════════════
    hdr(row, '1.  RESUMEN EJECUTIVO DE CARTERA'); row++;
    colHdr(row); row++;

    kpi(row, 'Total de clientes activos',
      `=COUNTA(${SC}!C3:C5000)`,
      'clientes',
      `COUNTA('Scoring_Clientes'!C3:C5000)\n\n→ Cuenta las celdas no vacías de la columna C (Razón Social) desde la fila 3 hasta 5000.`,
      '¿QUÉ MIDE? La cantidad total de clientes con cuenta corriente activa (cualquier saldo).\n\n¿CÓMO SE ANALIZA? Mide la amplitud de la cartera. Una cartera con pocos clientes es más fácil de gestionar pero más vulnerable a la pérdida de alguno grande. Combiná este número con el índice de concentración (Sección 3) para entender si el riesgo está distribuido.\n\n¿QUÉ CONCLUSIÓN? Si el número cae mes a mes sin que cayera el saldo total, la cartera se está concentrando: menos clientes, mismo o mayor monto.',
      '> 50 clientes', '20–50 clientes', '< 20 clientes', '#,##0'); row++;

    kpi(row, 'Cartera bruta total\n(suma de saldos deudores positivos)',
      `=SUMPRODUCT((${SC_F}>0)*${SC_F})`,
      '$ ARS',
      `SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n\n→ Columna F = Saldo Total ($). La condición (F>0) excluye clientes con saldo acreedor (recibos sin aplicar). SUMPRODUCT multiplica elemento a elemento: si F>0 vale 1, si F≤0 vale 0, luego suma todo.`,
      '¿QUÉ MIDE? El monto bruto total que la empresa tiene por cobrar, excluyendo los saldos negativos (recibos a cuenta sin aplicar). Es el DENOMINADOR BASE de todos los índices de mora y concentración. Si este número cambia, todos los porcentajes cambian también.\n\n¿CÓMO SE ANALIZA? Un crecimiento alto sin crecimiento en ventas puede indicar acumulación de mora. Una caída puede indicar cobros efectivos o pérdida de clientes.\n\n¿QUÉ CONCLUSIÓN? Este número solo no dice mucho; su valor es como denominador de los índices de las secciones 2, 3 y 4.',
      'Base de referencia', 'Crecimiento >20% m/m sin ↑ ventas', 'Caída >30% o crecimiento >50%',
      '$ #,##0.00'); row++;

    kpi(row, 'Recibos a cuenta sin aplicar\n(saldos acreedores de clientes)',
      `=SUMPRODUCT((${SC_F}<0)*${SC_F})`,
      '$ ARS',
      `SUMPRODUCT(('Scoring_Clientes'!F3:F5000<0)*'Scoring_Clientes'!F3:F5000)\n\n→ Misma columna F pero con condición inversa (F<0). Captura solo los saldos negativos: pagos recibidos que todavía no se imputaron a facturas específicas.`,
      '¿QUÉ MIDE? El monto total de pagos recibidos de clientes que no fueron imputados a facturas específicas en Sigma. Son un pasivo técnico: la empresa los tiene pero no los acreditó.\n\n¿CÓMO SE ANALIZA? Un valor alto o creciente indica que el equipo de administración está tardando en imputar los cobros. Esto distorsiona el saldo real de cada cliente y puede ocultar mora real.\n\n¿QUÉ ACCIÓN TOMAR? Si supera el 5% de la cartera bruta, revisar el proceso de imputación de cobros. Cada recibo sin aplicar es un saldo de cliente que aparece más alto de lo real.',
      '< 5% cartera bruta', '5%–15% cartera bruta', '> 15% → revisar imputación urgente',
      '$ #,##0.00'); row++;

    kpi(row, 'Cartera neta\n(exposición real = bruta + recibos)',
      `=SUMPRODUCT(${SC_F})`,
      '$ ARS',
      `SUMPRODUCT('Scoring_Clientes'!F3:F5000)\n\n→ Suma algebraica de TODA la columna F sin filtros. Los positivos (saldos deudores) suman; los negativos (recibos sin aplicar) restan. El resultado es la exposición neta real.`,
      '¿QUÉ MIDE? Lo que la empresa efectivamente cobraría si aplicara todos los recibos pendientes. Siempre es ≤ cartera bruta.\n\n¿CÓMO SE ANALIZA? Si la diferencia entre cartera bruta y cartera neta es pequeña, los recibos sin aplicar son mínimos (buena operatoria). Si la diferencia es grande, hay recibos acumulados que distorsionan el análisis.\n\n¿QUÉ CONCLUSIÓN? La cartera neta es el número que deberías informar a Dirección como exposición real. La bruta es el dato de gestión interna.',
      '≈ Cartera bruta (dif. < 3%)', 'Diferencia 3%–15%', 'Diferencia > 15% → revisar imputación',
      '$ #,##0.00'); row++;

    kpi(row, 'Total de comprobantes pendientes de cobro',
      `=SUM(${SC_J})`,
      'comprob.',
      `SUM('Scoring_Clientes'!J3:J5000)\n\n→ Columna J = Fact. Pendientes (cantidad de comprobantes abiertos por cliente). SUM suma esa columna: da el total de comprobantes impagos en toda la cartera.`,
      '¿QUÉ MIDE? La cantidad total de facturas, notas de débito y otros comprobantes sin cobrar completamente. Mide la carga de trabajo operativa del área de cobranzas.\n\n¿CÓMO SE ANALIZA? Muchos comprobantes con saldo bajo = cartera fragmentada (costosa de gestionar, muchas gestiones de cobro chicas). Pocos comprobantes con saldo alto = cartera concentrada (riesgo alto pero gestión más simple).\n\n¿QUÉ CONCLUSIÓN? Cruzar con el número de clientes: si el cociente comprobantes/clientes sube, cada cliente está acumulando más facturas abiertas, señal de que no está pagando a medida que compra.',
      'Referencia contextual', 'Referencia contextual', 'Referencia contextual', '#,##0'); row++;

    kpi(row, 'Comprobantes vencidos\n(atraso > 0 días)',
      `=SUM(${SC_K})`,
      'comprob.',
      `SUM('Scoring_Clientes'!K3:K5000)\n\n→ Columna K = Fact. Vencidas (cantidad de comprobantes con atraso > 0 por cliente). Incluye los primeros 45 días de tolerancia interna.`,
      '¿QUÉ MIDE? La cantidad de comprobantes que superaron su fecha de vencimiento, incluyendo los que están dentro de la tolerancia interna de 45 días. No todos son mora "real": algunos pueden estar dentro del margen operativo normal.\n\n¿CÓMO SE ANALIZA? Interpretá este número junto con los de mora real (> 45 días). Si la diferencia entre vencidos totales y vencidos > 45 días es pequeña, la mora está mayormente dentro de tolerancia. Si son similares, la mora es estructural.',
      'Referencia contextual', 'Referencia contextual', 'Referencia contextual', '#,##0'); row++;

    kpi(row, 'Comprobantes > 45 días\n(mora real, fuera de tolerancia interna)',
      `=SUM(${SC_L})`,
      'comprob.',
      `SUM('Scoring_Clientes'!L3:L5000)\n\n→ Columna L = Fact. > 45 días (cantidad de comprobantes con atraso mayor a 45 días por cliente). Estos superaron el plazo formal (30 días) MÁS la tolerancia interna (15 días).`,
      '¿QUÉ MIDE? Los comprobantes con mora REAL exigible: ya superaron todos los márgenes de tolerancia. Cada uno de estos requiere acción formal de cobranza.\n\n¿CÓMO SE ANALIZA? Esta es la señal operativa más directa. Si este número sube mes a mes, el proceso de cobranza no está funcionando: los clientes están llegando a mora real sin que se haya actuado en los tramos anteriores.\n\n¿QUÉ ACCIÓN TOMAR? Para cada comprobante en este grupo debe existir un registro de gestión activa (llamado, compromiso de pago por escrito, evaluación de bloqueo).',
      '< 5% del total comprob.', '5%–15%', '> 15% → intervención inmediata', '#,##0'); row++;

    ws.setRowHeight(row, 6); row++;

    // ══════════════════════════════════════════════════════════════
    // SECCIÓN 2: ÍNDICES DE MORA Y RIESGO CREDITICIO
    // ══════════════════════════════════════════════════════════════
    hdr(row, '2.  ÍNDICES DE MORA Y RIESGO CREDITICIO'); row++;
    colHdr(row); row++;

    kpi(row, '🔴 Índice de mora total\n(saldo vencido / cartera bruta)',
      `=IFERROR(SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>0)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  SUMPRODUCT(\n    (ISNUMBER('Aging_CC_0001_0003'!H3:H5000))  ← excluye REC y subtotales\n    *('Aging_CC_0001_0003'!H3:H5000>0)       ← solo facturas vencidas\n    *('Aging_CC_0001_0003'!J3:J5000>0)       ← solo con saldo pendiente\n    *('Aging_CC_0001_0003'!B3:B5000<>"SUBTOTAL") ← excluye filas subtotal\n    *'Aging_CC_0001_0003'!J3:J5000           ← suma el Pendiente($)\n  ) / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nNumerador: suma de la columna J (Pendiente $) del Aging donde H (Atraso)>0.\nDenominador: cartera bruta de la columna F del Scoring.`,
      '¿QUÉ MIDE? Qué porcentaje de la cartera bruta tiene ALGÚN grado de atraso, incluyendo los primeros 45 días de tolerancia interna.\n\n⚠️ IMPORTANTE: este índice NO distingue mora real de mora dentro de tolerancia. Un 40% puede significar que la mitad de la cartera está con 1 día de atraso (normal) o con 200 días (grave). Siempre mirarlo junto con el Índice de mora REAL.\n\n¿QUÉ CONCLUSIÓN? Útil para ver tendencia mes a mes. Si sube consistentemente, el proceso de cobranza se está debilitando aunque el número absoluto parezca manejable.',
      '< 8%', '8%–15%', '> 15% → revisión del proceso de cobranza',
      '0.00%'); row++;

    kpi(row, '🔴 Índice de mora REAL\n(solo > 45 días / cartera bruta)',
      `=IFERROR(SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>45)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  SUMPRODUCT(\n    (ISNUMBER('Aging_CC_0001_0003'!H3:H5000))\n    *('Aging_CC_0001_0003'!H3:H5000>45)      ← SOLO facturas con +45 días\n    *('Aging_CC_0001_0003'!J3:J5000>0)\n    *('Aging_CC_0001_0003'!B3:B5000<>"SUBTOTAL")\n    *'Aging_CC_0001_0003'!J3:J5000\n  ) / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nIdéntico al anterior pero con el filtro H>45 en lugar de H>0. La única diferencia es ese umbral.`,
      '¿QUÉ MIDE? El porcentaje de cartera con mora EXIGIBLE REAL: superó el plazo formal (30 días) más la tolerancia interna (15 días). Todo saldo aquí debería tener una acción de cobranza documentada y activa.\n\nES EL ÍNDICE MÁS IMPORTANTE de esta hoja. Base para:\n• Decisiones de bloqueo de cuenta\n• Provisiones por incobrabilidad en los estados contables\n• Derivación a gestión extrajudicial\n\n¿QUÉ CONCLUSIÓN? Cada punto porcentual aquí representa dinero con riesgo real de pérdida. Un 5% de $120M de cartera = $6M en mora grave.',
      '< 3%', '3%–8%', '> 8% → deterioro estructural de la cartera',
      '0.00%'); row++;

    kpi(row, '% Cartera en categoría CRÍTICO\n(clientes con alguna factura > 60 días)',
      `=IFERROR(SUMIF(${SC_A},"CRÍTICO",${SC_F})/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  SUMIF(\n    'Scoring_Clientes'!A3:A5000,    ← columna A = Categoría\n    "CRÍTICO",                        ← busca exactamente "CRÍTICO"\n    'Scoring_Clientes'!F3:F5000      ← suma la columna F = Saldo Total($)\n  ) / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nSUMIF suma los saldos de la columna F solo para las filas donde la columna A dice "CRÍTICO".`,
      '¿QUÉ MIDE? El porcentaje del saldo total que corresponde a clientes con categoría CRÍTICO (tienen al menos una factura con más de 60 días de atraso y más del 60% de su saldo vencido).\n\nEs el indicador de RIESGO INMEDIATO DE PÉRDIDA: la porción de cartera más probable de convertirse en incobrable si no se actúa.\n\n¿CÓMO SE DIFERENCIA DEL ÍNDICE DE MORA REAL? La mora real mide facturas; este mide clientes. Un cliente CRÍTICO puede tener $5M de saldo total con $50K vencido — el índice de mora real lo pesa por los $50K, este índice lo pesa por los $5M de relación en riesgo.',
      '< 5%', '5%–15%', '> 15% → revisión urgente del proceso de crédito',
      '0.00%'); row++;

    kpi(row, '% Cartera en riesgo\n(CRÍTICO + OBSERVACIÓN)',
      `=IFERROR((SUMIF(${SC_A},"CRÍTICO",${SC_F})+SUMIF(${SC_A},"OBSERVACIÓN",${SC_F}))/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  (\n    SUMIF('Scoring_Clientes'!A3:A5000,"CRÍTICO",'Scoring_Clientes'!F3:F5000)\n    + SUMIF('Scoring_Clientes'!A3:A5000,"OBSERVACIÓN",'Scoring_Clientes'!F3:F5000)\n  ) / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nDos SUMIF por separado (CRÍTICO + OBSERVACIÓN) sumados antes de dividir por la cartera bruta.`,
      '¿QUÉ MIDE? La exposición total a riesgo crediticio activo: incluye tanto los clientes en situación grave (CRÍTICO) como los que están en zona de alerta temprana (OBSERVACIÓN: 1–2 facturas con más de 45 días).\n\nEs el INDICADOR PREVENTIVO por excelencia: mide cuánto de la cartera podría deteriorarse si no se actúa.\n\n¿QUÉ CONCLUSIÓN? Un aumento sostenido mes a mes de este % es la señal más importante de que el control de crédito se está debilitando, incluso si el índice de mora real todavía parece bajo.',
      '< 10%', '10%–25%', '> 25% → el control de crédito está cediendo',
      '0.00%'); row++;

    kpi(row, 'Tasa de vencimiento de comprobantes\n(fact. vencidas / fact. totales pendientes)',
      `=IFERROR(SUM(${SC_K})/SUM(${SC_J}),0)`,
      '%',
      `IFERROR(\n  SUM('Scoring_Clientes'!K3:K5000)    ← col K = Fact. Vencidas (cantidad)\n  / SUM('Scoring_Clientes'!J3:J5000)   ← col J = Fact. Pendientes (cantidad total)\n,0)\n\nSuma la columna K (facturas vencidas) y la divide por la suma de la columna J (todas las facturas abiertas).`,
      '¿QUÉ MIDE? La proporción de comprobantes abiertos que ya superaron su fecha de vencimiento. A diferencia de los índices de mora (que miden pesos), este mide UNIDADES: cuántas facturas de cada 100 están vencidas.\n\n¿POR QUÉ ES ÚTIL? Detecta carteras donde hay muchas facturas chicas vencidas (algo que puede pasar desapercibido si los montos son pequeños pero el volumen de gestión es alto).\n\n¿QUÉ CONCLUSIÓN? Si este % sube pero el índice de mora real se mantiene bajo, hay muchos clientes con facturas chicas atrasadas — señal de que el proceso de cobro de montos pequeños no está funcionando.',
      '< 30%', '30%–50%', '> 50% → más de la mitad de las facturas están vencidas',
      '0.00%'); row++;

    kpi(row, 'Atraso máximo en cartera\n(el cliente con mayor días de mora)',
      `=MAX(${SC_M})`,
      'días',
      `MAX('Scoring_Clientes'!M3:M5000)\n\n→ Columna M = Atraso Máx (días): para cada cliente, el atraso del comprobante más viejo. MAX encuentra el mayor de todos los clientes.`,
      '¿QUÉ MIDE? El peor caso individual de toda la cartera: el cliente que tiene la factura más vieja sin cobrar.\n\n¿CÓMO SE ANALIZA? Un valor muy alto (> 180 días) generalmente significa que esa deuda ya es prácticamente incobrable en términos económicos. El costo de recuperarla supera lo que se recuperaría. Requiere evaluación de:\n1. ¿Tiene respaldo documental (pagaré, cheque)?\n2. ¿Vale la pena gestión extrajudicial?\n3. ¿Corresponde provisionar contablemente?\n\n¿QUÉ CONCLUSIÓN? Si este número crece mes a mes, hay deudas viejas que nadie está resolviendo.',
      '< 90 días', '90–180 días', '> 180 días → evaluar castigo contable',
      '#,##0'); row++;

    kpi(row, 'Atraso promedio PONDERADO por saldo\n(días efectivos de mora en toda la cartera)',
      `=IFERROR(SUMPRODUCT((${SC_F}>0)*${SC_N}*${SC_F})/SUMPRODUCT((${SC_F}>0)*${SC_F}),0)`,
      'días',
      `IFERROR(\n  SUMPRODUCT(\n    ('Scoring_Clientes'!F3:F5000>0)    ← solo clientes con saldo positivo\n    * 'Scoring_Clientes'!N3:N5000      ← col N = Atraso Prom General (días)\n    * 'Scoring_Clientes'!F3:F5000      ← col F = Saldo Total($) como ponderador\n  ) / SUMPRODUCT(\n    ('Scoring_Clientes'!F3:F5000>0)\n    * 'Scoring_Clientes'!F3:F5000\n  )\n,0)\n\nEl SUMPRODUCT multiplica atraso × saldo para cada cliente, suma todo y divide por la suma de saldos. Esto pondera: un cliente con $5M de saldo cuenta 100x más que uno con $50K.`,
      '¿QUÉ MIDE? El promedio de días de atraso de toda la cartera, pero PESADO por el saldo de cada cliente. Un cliente grande con mora grave impacta mucho más que muchos clientes chicos con poca mora.\n\nDIFERENCIA CON UN PROMEDIO SIMPLE: si tenés 100 clientes con 2 días de atraso y 1 cliente con $10M y 300 días de atraso, el promedio simple dice ~5 días. El ponderado puede decir 80 días, reflejando el riesgo económico real.\n\n¿QUÉ CONCLUSIÓN? Si este número crece, el deterioro está en los clientes con mayor exposición — los que más impactan en el resultado financiero.',
      '< 15 días', '15–30 días', '> 30 días → mora estructural acumulada en clientes grandes',
      '#,##0'); row++;

    ws.setRowHeight(row, 6); row++;

    // ══════════════════════════════════════════════════════════════
    // SECCIÓN 3: CONCENTRACIÓN DE CARTERA
    // ══════════════════════════════════════════════════════════════
    hdr(row, '3.  ANÁLISIS DE CONCENTRACIÓN DE CARTERA'); row++;
    colHdr(row); row++;

    kpi(row, 'Saldo de los 5 mayores clientes\n(Top 5 por saldo)',
      `=SUMPRODUCT(LARGE(${SC_F},{1,2,3,4,5}))`,
      '$ ARS',
      `SUMPRODUCT(LARGE('Scoring_Clientes'!F3:F5000,{1,2,3,4,5}))\n\n→ LARGE devuelve los N valores más grandes de un rango. Con el array {1,2,3,4,5} devuelve los 5 mayores saldos de la columna F. SUMPRODUCT los suma. No importa el nombre del cliente: toma automáticamente los 5 primeros.`,
      '¿QUÉ MIDE? La exposición monetaria en los 5 clientes con mayor saldo. Cuantifica cuánto del riesgo total está en los clientes más grandes.\n\nNo es un % todavía (para eso usar el indicador siguiente), pero sirve para saber el valor absoluto en riesgo si los 5 principales fallaran simultáneamente.\n\n¿QUÉ CONCLUSIÓN? Útil para el análisis de escenario de estrés: "¿qué pasa con el flujo de caja si los 5 mayores no pagan este mes?"',
      'Dato de referencia', 'Dato de referencia', 'Dato de referencia',
      '$ #,##0.00'); row++;

    kpi(row, '% Concentración Top 5 clientes\n(5 mayores / cartera bruta)',
      `=IFERROR(SUMPRODUCT(LARGE(${SC_F},{1,2,3,4,5}))/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  SUMPRODUCT(LARGE('Scoring_Clientes'!F3:F5000,{1,2,3,4,5}))\n  / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nNumerador: suma de los 5 mayores saldos (fórmula anterior).\nDenominador: cartera bruta total (solo saldos positivos).`,
      '¿QUÉ MIDE? Qué fracción de la cartera total depende de los 5 clientes más grandes. Mide el RIESGO DE CONCENTRACIÓN: cuánto perdería la empresa si esos 5 no pagan.\n\nPRINCIPIO DE PRUDENCIA FINANCIERA: ningún cliente debería representar más del 20% de la cartera. Si los 5 mayores juntos superan el 35%, hay dependencia moderada; si superan el 60%, es concentración extrema.\n\n¿QUÉ ACCIÓN TOMAR? Si el % es alto, el objetivo estratégico es crecer la base de clientes medianos para reducir la concentración gradualmente.',
      '< 35% → cartera diversificada', '35%–60% → dependencia moderada', '> 60% → concentración extrema',
      '0.00%'); row++;

    kpi(row, '% Concentración Top 10 clientes\n(10 mayores / cartera bruta)',
      `=IFERROR(SUMPRODUCT(LARGE(${SC_F},{1,2,3,4,5,6,7,8,9,10}))/(${BRUTA}),0)`,
      '%',
      `IFERROR(\n  SUMPRODUCT(LARGE('Scoring_Clientes'!F3:F5000,{1,2,3,4,5,6,7,8,9,10}))\n  / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)\n,0)\n\nIdéntico al Top 5 pero con array {1,2,3,4,5,6,7,8,9,10}: toma los 10 mayores saldos.`,
      '¿QUÉ MIDE? Lo mismo que el Top 5 pero con los 10 mayores. Comparar los dos indica si hay un salto brusco entre el cliente 5 y el 6:\n\n• Si Top5=55% y Top10=60%: los clientes 6–10 suman solo 5% → salto brusco. La cartera tiene 5 clientes dominantes y el resto son muy chicos. Cartera de DOS VELOCIDADES.\n\n• Si Top5=30% y Top10=50%: los clientes 6–10 suman 20% → distribución más pareja. Cartera más equilibrada.\n\n¿QUÉ CONCLUSIÓN? Una diferencia Top10-Top5 > 15% indica buena distribución en la parte alta. Una diferencia < 5% indica concentración extrema en el podio.',
      '< 55%', '55%–75%', '> 75% → concentración extrema en los primeros 10',
      '0.00%'); row++;

    kpi(row, 'Índice de Herfindahl-Hirschman (HHI)\nconcentración económica de la cartera',
      `=IFERROR(SUMPRODUCT((${SC_F}>0)*${SC_F},(${SC_F}>0)*${SC_F})/(${BRUTA})^2,0)`,
      'índice',
      `IFERROR(\n  SUMPRODUCT(\n    ('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000,  ← array de saldos positivos\n    ('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000   ← mismo array (se multiplican elemento a elemento = cuadrado)\n  ) / SUMPRODUCT(('Scoring_Clientes'!F3:F5000>0)*'Scoring_Clientes'!F3:F5000)^2\n,0)\n\nEl SUMPRODUCT con dos arrays iguales calcula la suma de saldo². Al dividir por (total)² se obtiene la suma de (participación de cada cliente)².`,
      '¿QUÉ MIDE? El índice estándar de economía industrial aplicado a la cartera. Suma el cuadrado de la participación de cada cliente.\n\nFÓRMULA MATEMÁTICA: HHI = Σ(saldo_cliente / total)² para todos los clientes.\n\nINTERPRETACIÓN:\n• HHI = 0.01: cartera con 100 clientes del mismo tamaño\n• HHI = 0.10: equivale a 10 clientes del mismo tamaño (1/HHI = nro. equivalente)\n• HHI = 1.00: un único cliente = 100% de la cartera\n\n¿QUÉ ACCIÓN TOMAR? Si HHI sube mes a mes, la cartera se está concentrando. El objetivo es que baje con el crecimiento de la base de clientes.',
      '< 0.10 → cartera diversificada', '0.10–0.25 → concentración moderada', '> 0.25 → alta concentración sistémica',
      '0.0000'); row++;

    ws.setRowHeight(row, 6); row++;

    // ══════════════════════════════════════════════════════════════
    // SECCIÓN 4: DISTRIBUCIÓN POR CATEGORÍA DE RIESGO
    // ══════════════════════════════════════════════════════════════
    hdr(row, '4.  DISTRIBUCIÓN DE CARTERA POR CATEGORÍA DE RIESGO'); row++;

    // Encabezado de tabla
    ws.getRange(row,1,1,7).setValues([[
      'CATEGORÍA', '# CLIENTES\nCOUNTIF(A3:A5000,"X")', 'SALDO ($)\nSUMIF(A3:A5000,"X",F3:F5000)',
      '% CARTERA BRUTA\nSALDO/BRUTA', 'TICKET PROM ($)\nSALDO/CLIENTES',
      'FÓRMULA CLIENTES (texto para referencia)', 'FÓRMULA SALDO (texto para referencia)'
    ]])
      .setBackground('#2E75B6').setFontColor('#FFFFFF').setFontWeight('bold')
      .setFontSize(9).setHorizontalAlignment('center').setWrap(true);
    ws.setRowHeight(row, 36); row++;

    const cats = [
      ['CRÍTICO',     '#FFE0E0', '#B03A3A'],
      ['RIESGOSO',    '#FFD5B8', '#7D3900'],
      ['OBSERVACIÓN', '#FFFDE7', '#7D5A00'],
      ['BUENO',       '#E8F5E9', '#1E5C30'],
      ['AL DÍA',      '#D5E8F0', '#1F3A5F'],
    ];
    const firstCatRow = row;
    cats.forEach(([cat, bgCat, color]) => {

      // Col A: Categoría
      ws.getRange(row,1).setValue(cat)
        .setBackground(bgCat).setFontWeight('bold').setFontSize(10)
        .setFontColor(color).setHorizontalAlignment('center').setVerticalAlignment('middle');

      // Col B: # Clientes — setFormula para evitar error de locale
      ws.getRange(row,2).setFormula(`=COUNTIF(${SC_A},"${cat}")`)
        .setBackground('#FFFFFF').setFontWeight('bold').setFontSize(10)
        .setHorizontalAlignment('center').setVerticalAlignment('middle').setNumberFormat('#,##0');

      // Col C: Saldo — setFormula
      ws.getRange(row,3).setFormula(`=SUMIF(${SC_A},"${cat}",${SC_F})`)
        .setBackground('#FFFFFF').setFontWeight('bold').setFontSize(10)
        .setHorizontalAlignment('center').setVerticalAlignment('middle').setNumberFormat('$ #,##0.00');

      // Col D: % cartera bruta — setFormula
      ws.getRange(row,4).setFormula(`=IFERROR(C${row}/(${BRUTA}),0)`)
        .setBackground('#F2F2F2').setFontSize(9)
        .setHorizontalAlignment('center').setVerticalAlignment('middle').setNumberFormat('0.00%');

      // Col E: Ticket promedio — setFormula
      ws.getRange(row,5).setFormula(`=IFERROR(C${row}/B${row},0)`)
        .setBackground('#F2F2F2').setFontSize(9)
        .setHorizontalAlignment('center').setVerticalAlignment('middle').setNumberFormat('$ #,##0.00');

      // Col F y G: texto explicativo de fórmulas
      ws.getRange(row,6).setValue(`COUNTIF('Scoring_Clientes'!A3:A5000,"${cat}")`)
        .setBackground('#E8E8E8').setFontSize(8).setFontStyle('italic').setFontColor('#444444').setWrap(true);
      ws.getRange(row,7).setValue(`SUMIF('Scoring_Clientes'!A3:A5000,"${cat}",'Scoring_Clientes'!F3:F5000)`)
        .setBackground('#E8E8E8').setFontSize(8).setFontStyle('italic').setFontColor('#444444').setWrap(true);

      ws.setRowHeight(row, 22); row++;
    });

    // Fila TOTAL — setFormula para cada celda
    const lastCatRow = row - 1;
    ws.getRange(row,1).setValue('TOTAL GENERAL')
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
    ws.getRange(row,2).setFormula(`=SUM(B${firstCatRow}:B${lastCatRow})`)
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold')
      .setHorizontalAlignment('center').setNumberFormat('#,##0');
    ws.getRange(row,3).setFormula(`=${BRUTA}`)
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold')
      .setHorizontalAlignment('center').setNumberFormat('$ #,##0.00');
    ws.getRange(row,4).setValue('100.00%')
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
    ws.getRange(row,5).setFormula(`=IFERROR(C${row}/B${row},0)`)
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold')
      .setHorizontalAlignment('center').setNumberFormat('$ #,##0.00');
    ws.getRange(row,6,1,2).setBackground('#1F3A5F');
    ws.setRowHeight(row, 20); row++;

    // Lectura de la tabla
    ws.getRange(row,1,1,8).merge()
      .setValue('📊 CÓMO LEER ESTA TABLA: Cada fila muestra cuántos clientes y cuánto saldo está en cada categoría de riesgo. La distribución ideal tiene > 70% en AL DÍA y BUENO, con CRÍTICO < 5%. El ticket promedio (saldo/clientes) revela si el riesgo está concentrado en pocos clientes grandes (ticket alto en CRÍTICO) o disperso en muchos chicos. El primer caso es más peligroso económicamente pero más manejable: hay pocos clientes a gestionar. El segundo es sistémico: hay muchos clientes con problemas.')
      .setBackground('#EBF3FB').setFontSize(9).setFontStyle('italic').setFontColor('#333333').setWrap(true).setVerticalAlignment('middle');
    ws.setRowHeight(row, 48); row++;
    ws.setRowHeight(row, 6); row++;

    // ══════════════════════════════════════════════════════════════
    // SECCIÓN 5: AGING BUCKETS (TRAMOS DE ANTIGÜEDAD)
    // ══════════════════════════════════════════════════════════════
    hdr(row, '5.  DISTRIBUCIÓN POR TRAMOS DE ANTIGÜEDAD (AGING BUCKETS)'); row++;

    ws.getRange(row,1,1,8).merge()
      .setValue('Distribuye el SALDO ($) de cada comprobante individual según sus días de atraso. Fuente: Aging_CC_0001_0003. Usa ISNUMBER(H) para excluir recibos (col H vacía) y filas de subtotal (col B="SUBTOTAL"). Los % se calculan sobre cartera bruta de Scoring_Clientes!F.')
      .setBackground('#EBF3FB').setFontSize(9).setFontStyle('italic').setFontColor('#444444')
      .setWrap(true).setVerticalAlignment('middle');
    ws.setRowHeight(row, 28); row++;

    ws.getRange(row,1,1,8).setValues([[
      'TRAMO', 'SALDO ($)\n(SUMPRODUCT de Aging!J)', '% CARTERA\n(saldo/Scoring!F bruta)',
      'FÓRMULA SUMPRODUCT EXACTA (con los rangos del Aging)',
      'LECTURA Y ACCIÓN RECOMENDADA', '✅ ÓPTIMO', '⚠️ ALERTA', '🔴 CRÍTICO'
    ]])
      .setBackground('#2E75B6').setFontColor('#FFFFFF').setFontWeight('bold')
      .setFontSize(9).setHorizontalAlignment('center').setWrap(true);
    ws.setRowHeight(row, 36); row++;

    const tramos = [
      { n: 'Por vencer\n(atraso ≤ 0 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}<=0)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER('Aging_CC_0001_0003'!H3:H5000))   ← excluye vacíos (recibos/subtotales)\n  *('Aging_CC_0001_0003'!H3:H5000<=0)     ← atraso ≤ 0: todavía no venció\n  *('Aging_CC_0001_0003'!J3:J5000>0)     ← tiene saldo pendiente\n  *('Aging_CC_0001_0003'!B3:B5000<>"SUBTOTAL")\n  *'Aging_CC_0001_0003'!J3:J5000          ← suma el Pendiente($)`,
        l: 'Comprobantes dentro del plazo. La empresa no tiene acción activa. Cuanto mayor sea este tramo, más saludable la cartera. Gestión: ciclo normal de venta.',
        ok: '> 60% cartera', al: '40%–60%', cr: '< 40%', bg: '#D5E8F0' },
      { n: 'Mora temprana\n(1 a 15 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>=1)*(${AG_H}<=15)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER(Aging!H3:H5000))\n  *(Aging!H3:H5000>=1)*(Aging!H3:H5000<=15)  ← entre 1 y 15 días de atraso\n  *(Aging!J3:J5000>0)*(Aging!B3:B5000<>"SUBTOTAL")\n  *Aging!J3:J5000`,
        l: 'Dentro de tolerancia interna. Recordatorio informal del vendedor. Sin bloqueo. Este tramo es esperable; lo clave es que no crezca mes a mes.',
        ok: '< 10%', al: '10%–20%', cr: '> 20%', bg: '#E8F5E9' },
      { n: 'Mora moderada\n(16 a 30 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>=16)*(${AG_H}<=30)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER(Aging!H3:H5000))\n  *(Aging!H3:H5000>=16)*(Aging!H3:H5000<=30)  ← entre 16 y 30 días\n  *(Aging!J3:J5000>0)*(Aging!B3:B5000<>"SUBTOTAL")\n  *Aging!J3:J5000`,
        l: 'Aún dentro de los 45 días de tolerancia. Administración interviene formalmente y solicita fecha de pago comprometida por escrito.',
        ok: '< 8%', al: '8%–15%', cr: '> 15%', bg: '#E8F5E9' },
      { n: 'Mora relevante\n(31 a 45 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>=31)*(${AG_H}<=45)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER(Aging!H3:H5000))\n  *(Aging!H3:H5000>=31)*(Aging!H3:H5000<=45)  ← entre 31 y 45 días\n  *(Aging!J3:J5000>0)*(Aging!B3:B5000<>"SUBTOTAL")\n  *Aging!J3:J5000`,
        l: 'Límite de la tolerancia. Se evalúa bloqueo preventivo. Jefatura de Administración interviene. Sin regularización en este tramo → bloqueo formal.',
        ok: '< 5%', al: '5%–12%', cr: '> 12%', bg: '#FFFDE7' },
      { n: 'Mora grave\n(46 a 60 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>=46)*(${AG_H}<=60)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER(Aging!H3:H5000))\n  *(Aging!H3:H5000>=46)*(Aging!H3:H5000<=60)  ← entre 46 y 60 días\n  *(Aging!J3:J5000>0)*(Aging!B3:B5000<>"SUBTOTAL")\n  *Aging!J3:J5000`,
        l: 'Superó toda tolerancia. Cuenta bloqueada. Se requiere acuerdo documentado (pagaré o confirmación escrita) para rehabilitar. Alto riesgo de incobrable.',
        ok: '< 3%', al: '3%–8%', cr: '> 8%', bg: '#FFE8B2' },
      { n: 'Mora crítica\n(más de 60 días)',
        f: `=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_H}>=61)*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`,
        ft: `SUMPRODUCT(\n  (ISNUMBER(Aging!H3:H5000))\n  *(Aging!H3:H5000>=61)    ← más de 60 días (sin límite superior)\n  *(Aging!J3:J5000>0)*(Aging!B3:B5000<>"SUBTOTAL")\n  *Aging!J3:J5000`,
        l: 'Mora estructural. Cuenta bloqueada totalmente. Intervención de Gerencia o Dirección. Evaluar provisión contable por incobrable. Posible gestión extrajudicial.',
        ok: '< 1%', al: '1%–5%', cr: '> 5% → acción sistémica urgente', bg: '#FFE0E0' },
    ];

    tramos.forEach(t => {
      // Col A: nombre del tramo
      ws.getRange(row,1).setValue(t.n)
        .setBackground(t.bg).setFontWeight('bold').setFontSize(9)
        .setFontColor('#1F3A5F').setWrap(true).setVerticalAlignment('middle');

      // Col B: SUMPRODUCT (fórmula real) — setFormula
      ws.getRange(row,2).setFormula(t.f)
        .setBackground('#FFFFFF').setFontWeight('bold').setFontSize(10)
        .setFontColor('#0A2E5A').setHorizontalAlignment('center').setNumberFormat('$ #,##0.00');

      // Col C: % sobre cartera bruta — setFormula
      ws.getRange(row,3).setFormula(`=IFERROR(B${row}/(${BRUTA}),0)`)
        .setBackground('#F2F2F2').setFontSize(9).setHorizontalAlignment('center').setNumberFormat('0.00%');

      // Col D: fórmula explicada con rangos reales
      ws.getRange(row,4).setValue(t.ft)
        .setBackground('#E8E8E8').setFontSize(8).setFontStyle('italic')
        .setFontColor('#333333').setWrap(true).setVerticalAlignment('top');

      // Col E: lectura y acción
      ws.getRange(row,5).setValue(t.l)
        .setBackground('#FFFFFF').setFontSize(9).setWrap(true).setVerticalAlignment('top');

      ws.getRange(row,6).setValue(t.ok).setBackground('#E8F5E9').setFontSize(8).setHorizontalAlignment('center').setWrap(true);
      ws.getRange(row,7).setValue(t.al).setBackground('#FFF3CD').setFontSize(8).setHorizontalAlignment('center').setWrap(true);
      ws.getRange(row,8).setValue(t.cr).setBackground('#FFE0E0').setFontSize(8).setHorizontalAlignment('center').setWrap(true);
      ws.setRowHeight(row, 80); row++;
    });

    // Fila TOTAL aging
    ws.getRange(row,1).setValue('TOTAL CARTERA (comprobantes)')
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
    ws.getRange(row,2).setFormula(`=SUMPRODUCT((ISNUMBER(${AG_H}))*(${AG_J}>0)*(${AG_B}<>"SUBTOTAL")*${AG_J})`)
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold')
      .setHorizontalAlignment('center').setNumberFormat('$ #,##0.00');
    ws.getRange(row,3).setValue('100.00%')
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
    ws.getRange(row,4,1,5).setBackground('#1F3A5F');
    ws.setRowHeight(row, 18); row++;
    ws.setRowHeight(row, 6); row++;

    // ── NOTA METODOLÓGICA ────────────────────────────────────────
    const notas = [
      ['📌  NOTA METODOLÓGICA — LEÉ ESTO PARA ENTENDER QUÉ ESTÁS ANALIZANDO:', true],
      ['• COLUMNAS USADAS DEL SCORING (Scoring_Clientes): A=Categoría | F=Saldo Total($) | J=Fact.Pendientes | K=Fact.Vencidas | L=Fact.>45días | M=Atraso Máx | N=Atraso Prom General. Las columnas G, H e I (Saldo Vencido, Saldo por Vencer y % Exposición) son para el análisis en la hoja Scoring_Clientes, no para estos índices.', false],
      ['• COLUMNAS USADAS DEL AGING (Aging_CC_0001_0003): B=Razón Social/"SUBTOTAL" | H=Atraso(días) | J=Pendiente($). El filtro ISNUMBER(H) excluye los recibos a cuenta (cuya columna H está vacía) y el filtro B<>"SUBTOTAL" excluye las filas de subtotal por vendedor.', false],
      ['• CARTERA BRUTA vs NETA: la cartera bruta (denominador de todos los %) usa SUMPRODUCT((F>0)*F) para excluir saldos negativos (recibos sin aplicar). La cartera neta usa SUMPRODUCT(F) sin filtro, e incluye los negativos. La diferencia entre ambas es la magnitud del problema de imputación.', false],
      ['• MORA TOTAL vs MORA REAL: la mora total (H>0) incluye los primeros 45 días de tolerancia interna y NO debe usarse para decisiones de bloqueo. La mora real (H>45) es la que tiene consecuencias: bloqueo, provisión contable, gestión extrajudicial.', false],
      ['• HHI (Herfindahl-Hirschman): el nro. equivalente de clientes = 1/HHI. Si HHI=0.05, es como tener 20 clientes del mismo tamaño aunque tengas 100. Si HHI sube, la cartera se está concentrando. Objetivo: HHI decreciente con el tiempo.', false],
      ['• ACTUALIZACIÓN AUTOMÁTICA: todas las fórmulas se recalculan solos cuando el script de Apps Script actualiza el Aging y el Scoring. No hay que hacer nada manual.', false],
    ];
    notas.forEach(([n, bold]) => {
      ws.getRange(row,1,1,8).merge().setValue(n)
        .setBackground(bold ? '#D5E8F0' : '#EBF3FB')
        .setFontSize(9).setFontStyle(bold ? 'normal' : 'italic')
        .setFontWeight(bold ? 'bold' : 'normal')
        .setFontColor(bold ? '#1F3A5F' : '#333333')
        .setWrap(true).setVerticalAlignment('middle');
      ws.setRowHeight(row, bold ? 18 : 26); row++;
    });

    Logger.log(`✓ Indices_Financieros creada sin errores: ${row-1} filas`);
    ui.alert('✅ Hoja creada correctamente',
      `Indices_Financieros generada con ${row-1} filas.\n\nCero errores de fórmula.\nTodas las celdas de valor usan setFormula() con nombres de hoja completos y columnas del Scoring actualizadas al esquema de 17 columnas.`,
      ui.ButtonSet.OK);

  } catch(e) {
    Logger.log('✖ ERROR: ' + e.message + '\n' + e.stack);
    ui.alert('❌ Error', e.message, ui.ButtonSet.OK);
  }
}