# Los scripts de las planillas

Copias de respaldo del código que vive adentro de los Google Sheets. **Acá no
corre nada**: el original está en cada planilla (Extensiones → Apps Script) y
es el único lugar donde se ejecuta.

Existe por una razón concreta: en cuatro días el proyecto de logística se rompió
dos veces por un choque de nombres, y las dos veces el síntoma fue el mismo —los
menús desaparecen por completo— sin ninguna forma de ver qué había cambiado. Un
`.gs` no tiene historial que se pueda leer en un diff. Acá sí.

| Archivo | De qué planilla | Qué hace |
|---|---|---|
| `sell_in.gs` | Sell in | Manda la hoja al tablero vía la función `sell-in` |
| `FLETE_AUTO.gs` | Logística | Trae `reporte_logistica` a la hoja FLETE y sube lo cargado a mano |
| `auditoria_sevillanita.gs` | Logística | Manda la hoja ACCIONES vía la función `auditoria-sevillanita` |

## Lo que todavía no está acá

De la planilla de logística faltan dos archivos, y son los más expuestos porque
sólo existen adentro del Sheet:

- **`Código.gs`** — la integración con la API de Sigma (clientes, notas de
  crédito, proveedores).
- **`MotorAuditoria.gs`** — el indicador de pendientes y los cinco cálculos:
  tarifa, peso, acciones, análisis financiero y mínimo de compra.

## LA TRAMPA QUE YA MORDIÓ DOS VECES

En Apps Script **todos los archivos de un proyecto comparten un único espacio
de nombres global**, como si estuvieran pegados uno atrás del otro. No hay
módulos, no hay imports, no hay scope por archivo.

Las consecuencias no son simétricas:

- Una **función** duplicada se tolera: gana la última que carga, en silencio.
  Es la peor de las dos, porque el código sigue andando y hace otra cosa.
- Una **`const` o `let`** duplicada es un `SyntaxError` fatal: el proyecto
  entero no compila. No corre `onOpen`, no aparece ningún menú, y el panel de
  Ejecuciones no muestra nada. Parece que la planilla se rompió.

Pasó con `HOJA_FLETE_PS` (entre `PendienteSubir.gs` y `MotorAuditoria.gs`) y
con `COLUMNAS` (entre `FLETE_AUTO.gs` y la primera versión de
`auditoria_sevillanita.gs`, que declaraba `var COLUMNAS = 7` — ver el commit
5e11b71 y su arreglo en b9dd5de).

**Por eso, en un archivo nuevo, todo lo de nivel superior lleva un prefijo
propio.** El de auditoría usa `AUD_DESTINO`, `AUD_HOJA`, `AUD_COLUMNAS`,
`audToken_`, `audFecha_`, `audTexto_`, `audNumero_`. Ninguno puede chocar con
nada.

Y **`onOpen` se define en un solo archivo**, el que arma los dos menús
(`FLETE_AUTO.gs`). Dos `onOpen` no son dos menús: es uno que reemplaza al otro.

Antes de agregar un archivo, buscá el nombre que vas a declarar con la lupa del
editor, que busca en todos los archivos a la vez.

## Cómo mantener esto al día

Es a mano y en un solo sentido: se edita en la planilla, se prueba, y recién
ahí se pega acá. Nadie copia desde el repo hacia el Sheet automáticamente.

**Los tokens nunca van en el código.** Viven en Configuración del proyecto →
Propiedades del script, y se leen con `PropertiesService`. Estos archivos son
públicos.
