# Orquestador de datos — Brandmark / NOA / Unibrandco

Pipeline que llena Supabase con las ventas, costos, stock y logística de los
tres negocios. Lo que produce lo lee el tablero web
([mi-tablero-app](https://github.com/valeriabrandmark/mi-tablero-app), desplegado
en <https://brandmark-business.vercel.app>), que consulta Postgres en vivo en
cada request: no hay caché intermedia, lo que está en Supabase es lo que se ve.

Corre en una computadora de la oficina, con una tarea programada cada 2 horas.
**Si esa máquina está apagada, el tablero no se actualiza** — no hay nada
corriendo en la nube.

---

## Correr

```bash
python orquestador.py            # el pipeline entero, respetando frecuencias
python orquestador.py --listar   # qué corre, cada cuánto, cuándo fue la última vez
```

`--listar` es lo primero que conviene mirar cuando algo se ve viejo en el
tablero: dice qué paso está pendiente y hace cuánto que no termina bien.

Otras formas:

```bash
python orquestador.py --forzar          # ignora las frecuencias, corre todo
python orquestador.py --solo modelo.py  # un solo paso
```

---

## Los pasos, en orden

El orden no es decorativo: `modelo.py` arma `gold.fact_ventas` leyendo lo que
dejaron todas las extracciones, así que **todas van antes que él**.

| # | Paso | Cada | Escribe | Corta si falla |
|---|---|---|---|---|
| 1 | `sigma.py --ventas` | siempre | `bronze.sigma_ventas` | sí |
| 2 | `sigma.py --catalogo` | 24 h | `bronze.sigma_articulos` | no |
| 3 | `digip_pedidos.py` | siempre | `bronze.digip_pedidos` | sí |
| 4 | `digip_preparaciones.py` | 6 h | `bronze.digip_preparaciones` | no |
| 5 | `mercadolibre.py --ventas` | 2 h | `bronze.ml_ventas` | no |
| 6 | `ml_envios.py` | 4 h | `bronze.ml_envios` | no |
| 7 | `mercadolibre.py --catalogo` | 12 h | `bronze.ml_publicaciones`, `ml_stock_full` | no |
| 8 | `digip.py` | 4 h | `bronze.digip_stock`, `digip_stock_detalle` | no |
| 9 | `tiendanube.py` | 4 h | `bronze.tn_pedidos`, `tn_pedidos_items` | no |
| 10 | `costos.py --si-cambio` | si cambió un Excel | `bronze.costos_historicos` | sí |
| 11 | `modelo.py` | siempre | **`gold.fact_ventas`** | sí |
| 12 | `prorratear_flete.py` | siempre | `gold.fact_ventas_flete` | sí |
| 13 | `clasificar_clientes.py` | siempre | `gold.clientes_clasificados` | sí |

### De dónde salen esas frecuencias

Del log de 24 corridas reales, midiendo cuánto tarda cada paso:

| Paso | Mediana |
|---|---|
| `mercadolibre.py --catalogo` | 33 min |
| `digip_preparaciones.py` | 9,8 min |
| `sigma.py` (las dos juntas) | 6,9 min |
| `modelo.py` | 5,9 min |
| `costos.py` | 1,4 min |
| el resto | menos de 30 s |

`mercadolibre.py --catalogo` no estaba en esa medición porque **nunca había
llegado a correr**: se midió el 19/08/2026, la primera vez. Son ~4.300 llamadas
a la API — una por cada `inventory_id` para el stock Full— y con la `PAUSA` de
1 segundo que tenía tardaba **83 minutos, de los cuales 72 eran el script
durmiendo**. Con `PAUSA = 0.3` baja a ~33. El freno real nunca fue esa pausa
sino el 429 de la API, que `llamar_ml` ya sabe manejar.

La corrida entera daba **25 minutos**, cada dos horas, y la mayor parte era volver
a pedir cosas que no habían cambiado. El caso más claro: `sigma_articulos` acumuló
**335.816 inserciones para tener 8.194 filas vivas** — unas 41 recargas del mismo
catálogo.

Los tres criterios para decidir cada frecuencia:

1. **Cuánto tarda.** `digip_preparaciones` hace una llamada por pedido de la
   ventana; es el más caro de todos.
2. **Cada cuánto cambia de verdad.** Un alta de artículo o un cambio de
   descripción no pasa cada dos horas. Las ventas sí.
3. **Con qué urgencia se mira.** Logística se mira por semana; las ventas de hoy,
   hoy.

El orquestador se acuerda en `estado_pasos.json` (local, no se sube) de cuándo
terminó bien cada paso. Si la computadora estuvo apagada tres días, el paso
vencido corre en la primera pasada — no espera un horario fijo que ya pasó.

`costos.py --si-cambio` es la excepción: se lo llama en **cada** corrida y el que
decide es el script, comparando una huella (nombre + tamaño + fecha) de los
`.xlsx`. Si ninguno se movió, no hace nada y termina en un segundo. Así un Excel
corregido entra en la corrida siguiente sin que haya que acordarse de nada.

Los pasos que no cortan el pipeline son extracciones sueltas: que se quede vieja
una parte es mejor que no actualizar nada.

---

## Flags

Solo tres scripts aceptan argumentos. El resto se corre pelado.

| Script | Flags | Sin flags |
|---|---|---|
| `modelo.py` | `--dias N`, `--todo` | ventana de 7 días |
| `mercadolibre.py` | `--ventas`, `--catalogo` | corre todo |
| `sigma.py` | `--ventas`, `--catalogo` | corre todo |
| `orquestador.py` | `--forzar`, `--solo`, `--listar` | respeta frecuencias |
| `costos.py` | `AAAA-MM` (posicional), `--listar`, `--si-cambio` | todos los meses |
| `ml_envios.py` | — | **siempre incremental** (solo lo que falta) |

`ml_envios.py` no necesita flags: ya resuelve solo su alcance, porque pide
únicamente los envíos que todavía no tiene.

En `costos.py` el mes va suelto, sin `--`:

```bash
python costos.py            # los cuatro meses, reescribe la tabla entera
python costos.py 2026-08    # solo agosto, los otros meses quedan intactos
python costos.py --listar   # qué meses hay en la carpeta
```

Con un mes **no** se reescribe la tabla: se borra ese mes y se vuelve a
insertar. Reescribir todo teniendo un solo mes cargado se llevaría puestos los
demás, que es justo lo que uno no quiere al corregir un Excel suelto. Al
terminar imprime cuántos SKUs quedaron por mes en la tabla completa, que es la
forma de confirmar que los otros siguen ahí.

---

## Recetas

### Cambié un `.xlsx` de costos

```bash
python costos.py 2026-08     # el mes que tocaste
python modelo.py --dias 30
```

**El `--dias 30` no es opcional.** El mes comercial va del 6 al 5, así que a mitad
de mes la ventana de 7 días no llega al principio del mes: las ventas del 6 al 10
se quedarían con el costo viejo, sin ningún error a la vista. El 18/08 eso eran
1.617 líneas por $82 M.

### Poner todo al día después de un parate

```bash
python mercadolibre.py --ventas   # 2-3 min
python ml_envios.py               # puede tardar horas la primera vez
python costos.py
python modelo.py --todo           # 15-20 min
```

Ojo con la ventana: `mercadolibre.py --ventas` solo pide los últimos 7 días. Si
`bronze.ml_ventas` quedó más atrasada que eso, **se abre un hueco que la ventana
móvil ya no cubre**. Antes de correrlo, mirá hasta qué fecha llega:

```sql
select max(date_created) from bronze.ml_ventas where status = 'paid';
```

Si falta más de una semana, subí `WINDOW_DAYS` en `mercadolibre.py` para esa
corrida.

### Falta el costo de envío de Mercado Libre

`ml_envios.py` no le pregunta a ML qué envíos hay: lee `bronze.ml_ventas` y pide
el costo de los que ahí figuran. Así que primero las ventas, después los envíos:

```bash
python mercadolibre.py --ventas
python ml_envios.py
python modelo.py --todo
```

Es incremental y se guarda agregando, así que **se puede cortar y retomar**: lo
ya bajado queda, y los envíos que dieron error se reintentan en la corrida
siguiente. Al final imprime cuántos hay en total; ese número tiene que acercarse
a la cantidad de envíos distintos que haya en `ml_ventas`.

**No corras dos a la vez.** Antes de saltear un envío, el script mira qué hay en
la tabla; dos procesos leen la misma foto y escriben lo mismo. Pasó una vez al
quedar corriendo la versión vieja mientras arrancaba la nueva después de un
`git pull`: quedaron 7.800 filas duplicadas. `gold.fact_ventas` no se vio
afectada porque `modelo.py` descarta duplicados, pero para limpiar la tabla está
`limpiar_duplicados_ml_envios.sql`.

---

## Reglas de negocio que no se deducen del código

**El mes comercial va del 6 al 5.** Día ≥ 6 → mes actual; día < 6 → mes anterior.
Está en `mes_comercial()` de `modelo.py` y lo espeja el tablero web.

**Piso histórico: 06/05/2026.** Es `FECHA_CORTE`. No hay nada antes, y `--todo`
llega exactamente hasta ahí.

**La comisión de Mercado Libre se guarda POR UNIDAD.** `sale_fee` de la API es la
comisión de una unidad, no la del ítem. En `gold.fact_ventas`, la columna
`comision` sigue esa convención: **quien la use tiene que multiplicarla por
`cantidad`**. Es la única columna con ese comportamiento junto a `precio_neto` y
`costo_unitario`; `envio`, en cambio, ya viene por línea.

**El envío se cobra por PAQUETE, no por orden.** Un carrito junta varias órdenes
en un solo envío. `ml_envios.py` guarda una fila por envío; `modelo.py` reparte
ese costo entre todas las líneas de todas las órdenes del paquete, proporcional
a cuánto vale cada una.

**`costo_unitario` ya trae aplicado el descuento del proveedor.**
`costos.py` calcula `costo_real = costo_teorico × (1 − oferta%)` y es ese el que
llega a `gold.fact_ventas`.

**Nunca `if_exists="replace"` sobre una tabla que ya existe.** `replace` hace
`DROP TABLE`, y el DROP **falla** si alguien creó una vista encima:

```
cannot drop table bronze.tn_pedidos_items because other objects depend on it
DETAIL:  view bronze.tn_control_cancelaciones depends on table ...
```

Eso dejó a `tiendanube.py` sin traer nada desde el 12/06: se creó la vista y el
script murió en cada corrida, en el último paso, después de haber bajado bien los
pedidos. Todos los scripts usan ahora **TRUNCATE + append**, que vacía la tabla
sin borrarla y deja las vistas en pie. Hoy hay vistas sobre `tn_pedidos_items`,
`sigma_ventas`, `digip_pedidos`, `digip_preparaciones` y `gold.fact_ventas`.

**Las ventanas móviles reprocesan, no acumulan.** `sigma.py`, `mercadolibre.py` y
`modelo.py` borran su ventana y la vuelven a insertar. Lo anterior a la ventana
queda intacto — por eso un dato que llega tarde a `bronze` **no entra solo** a
`gold`: hay que reconstruir con `--dias` o `--todo`.

**Qué cuenta como venta en Tienda Nube: que esté PAGADA.** El criterio es
`estado_pago = 'paid'` **y** `estado <> 'cancelled'`, y no el estado del pedido,
que sería lo intuitivo. En Tienda Nube las ventas **no pasan solas a `closed`**:
hay que cerrarlas a mano y nadie lo hace, así que en toda la historia de la
tienda no hay ni un pedido `closed`. Si el criterio fuera el estado, el tablero
mostraría cero para siempre.

Los dos filtros hacen falta por separado, porque las dos cosas pasan:

| Estado | Pago | Pedidos | ¿Es venta? | Por qué |
|---|---|---|---|---|
| `open` | `paid` | 29 | **sí** | pagada y viva |
| `cancelled` | `paid` | 12 | no | se cobró y después se canceló |
| `cancelled` | `voided` | 6 | no | anulada |
| `open` | `voided` | 1 | no | el pago se cayó |
| `open` | `partially_refunded` | 1 | no | devuelta en parte |

**Tienda Nube tiene dos costos de envío y no son lo mismo.**
`shipping_cost_customer` es lo que **paga el comprador** (es ingreso);
`shipping_cost_owner` es lo que **paga la tienda** (es costo). Suelen coincidir,
pero no cuando hay envío gratis o bonificado — que es justo cuando el margen se
cae y hay que poder verlo. `modelo.py` resta **`shipping_cost_owner`**, lo pasa a
neto (viene con IVA) y lo reparte entre las líneas del pedido proporcional al
valor de cada una, igual que en Mercado Libre.

**El margen de Tienda Nube no descuenta comisión de pasarela.** Lo que cobra Pago
Nube / Mercado Pago por procesar el cobro **no está en ningún campo de la API**,
así que `comision` queda en 0 en vez de inventar un porcentaje. El margen de ese
canal está por eso algo sobreestimado, y el tablero lo aclara.

---

## El Excel de costos

`costos_mensuales/AAAA-MM.xlsx`, uno por mes comercial. **El nombre del archivo
es el mes**, no hay ninguna columna de fecha adentro.

`costos.py` necesita dos pestañas y cuatro columnas:

| Pestaña | Columnas |
|---|---|
| `Costos` | `Codigo`, `Costo Teorico` |
| `Ofertas` | `SKU`, `DESCUENTO TOTAL PROVEEDOR` |

Busca los encabezados en las primeras 5 filas, así que agregar o sacar filas de
título no lo rompe. Renombrar o borrar una de esas cuatro columnas sí: corta con
`No se encontraron las columnas ...`. Si termina sin error, leyó lo que
corresponde.

Al final imprime cuántos SKUs cargó por mes. Vale la pena mirar que el mes nuevo
tenga un número parecido al anterior; si bajó mucho, algo se movió de lugar en el
Excel.

---

## Qué se está actualizando y qué no

```bash
python orquestador.py --listar
```

Es la respuesta a "¿esto está fresco?". Para cada paso muestra cada cuánto corre,
cuándo terminó bien por última vez, si está pendiente, y **qué tabla escribe** —
así se puede ir de "este número del tablero se ve viejo" al paso que lo llena.

Un paso puede aparecer al día y el dato igual verse raro: eso significa que el
problema no es la frecuencia sino el contenido, y ahí hay que mirar la tabla.

**Un paso que viene fallando aparece como `FALLA xN`**, con las últimas líneas del
error. Eso es distinto de `PENDIENTE`, que solo quiere decir "todavía no le tocó
el turno". La diferencia importa: `tiendanube.py` estuvo desde el 12/06 sin traer
nada porque fallaba en silencio, y en la pantalla se veía igual que un paso
esperando su turno.

## Por qué la corrida tiene un reloj encima

El 19/08/2026 el orquestador corrió a las 10 y **no volvió a correr en todo el
día**, sin dejar ni un error. La causa es una cadena de tres eslabones, y ninguno
avisa:

1. **Once llamadas HTTP no tenían `timeout`.** Sin él, `requests` espera *para
   siempre* si el servidor acepta la conexión y después no contesta. No falla, no
   reintenta: se queda.
2. **`subprocess.run` tampoco lo tenía**, así que un paso colgado colgaba al
   orquestador entero.
3. **El Programador de tareas de Windows** viene por defecto con *"si la tarea ya
   se está ejecutando, no iniciar una nueva instancia"*. Con el proceso de las 10
   todavía vivo, las 12 y las 14 se saltearon **en silencio** — ni siquiera
   quedaron como error.

Lo peligroso no es que un paso falle: es que una corrida colgada **arrastra a
todas las siguientes**. Por eso ahora hay tres topes.

| Tope | Dónde | Cuánto |
|---|---|---|
| `TIMEOUT_HTTP` | en cada script | 30 s a 120 s según la API |
| `techo` | por paso, en `PASOS` | 15 a 60 min (3-4× la mediana medida) |
| `PRESUPUESTO_TOTAL` | la corrida entera | 100 min |

**El presupuesto total es el que resuelve el problema de fondo.** Antes de cada
paso se mira si todavía entra: si quedan 5 minutos y el paso puede tardar 30, se
saltea y la corrida termina. Con 100 minutos contra un intervalo de 120, la
corrida **siempre** libera la máquina antes del disparo siguiente.

Lo que se saltea no se pierde: al no quedar registrado como "corrió bien", entra
primero en la corrida de después.

**Un paso cortado por tiempo NO se reintenta**, aunque le queden intentos.
Reintentar costaría otro techo entero, y dos techos seguidos se comen la ventana
—que es justo lo que el tope viene a evitar—. Además un cuelgue rara vez es
transitorio: es un socket esperando una respuesta que no llega, y el reintento se
cuelga igual.

Si en el log aparece seguido `NO ENTRARON en los 100 min`, algún paso se volvió
lento o el presupuesto quedó corto. Es una señal para mirar, no para ignorar.

### Cómo tiene que estar el Programador de tareas

Si el orquestador deja de correr solo, mirar esto antes que el código. En
PowerShell (la carpeta da igual, estos comandos le preguntan a Windows):

```powershell
Get-ScheduledTask -TaskName "*orquestador*" | Get-ScheduledTaskInfo
```

`LastTaskResult` en **`267009`** (`0x41301`) significa "la tarea sigue
ejecutándose": hay una corrida colgada y por eso no arranca ninguna nueva.

Tres cosas que conviene tener puestas en la tarea:

- **Disparador → "Repetir cada 2 horas durante: Indefinidamente".** Si dice una
  duración corta, la repetición se corta sola cuando esa duración se cumple.
- **Configuración → "Detener la tarea si se ejecuta más de: 2 horas"**, y
  **"Si la tarea ya se está ejecutando: Detener la instancia existente"**. Es el
  cinturón por si algún día algo se cuelga fuera de los topes de Python.
- **Condiciones → destildar "Iniciar la tarea solo si el equipo está con
  alimentación de CA"**, y tildar **"Reactivar el equipo para ejecutar esta
  tarea"** si la máquina se suspende.

---

## Cuando algo falla

1. `python orquestador.py --listar` — ver qué paso está pendiente.
2. `orquestador_log.txt` — cada corrida, cada paso, y el error completo si lo hubo.
3. Correr ese paso solo, para ver el error en pantalla:
   `python orquestador.py --solo <paso>`

Cada paso se reintenta 2 o 3 veces con espera antes de darse por vencido, así que
un timeout suelto de Supabase se resuelve solo.

---

## Configuración

Credenciales en `.env` (no se sube). Hacen falta:

```
DB_HOST  DB_PORT  DB_NAME  DB_USER  DB_PASS                  # Supabase
SIGMA_TOKEN  SIGMA_URL_CLIENTE  SIGMA_BASEALIAS  SIGMA_ID_CLIENTE
DIGIP_URL_BASE  DIGIP_API_KEY
ML_USER_ID  ML_CLIENT_ID  ML_CLIENT_SECRET  ML_REDIRECT_URI
TN_STORE_ID  TN_TOKEN  TN_USER_AGENT
```

Los tokens de Mercado Libre viven aparte en `ml_tokens.json` (tampoco se sube).
**Mercado Libre devuelve un `refresh_token` nuevo en cada renovación y el script
lo pisa**, así que si se corren dos procesos a la vez contra la misma API, uno
invalida el token del otro.

Dependencias: `pip install -r requirements.txt`.

---

## Archivos locales que no se suben

| Archivo | Qué es | Si se borra |
|---|---|---|
| `.env` | credenciales | no corre nada |
| `ml_tokens.json` | tokens de ML | hay que re-autorizar la app |
| `estado_pasos.json` | última corrida OK de cada paso | todos los pasos corren una vez de más |
| `cache_ml_ventas/` | caché de descargas viejas de ML | se vuelve a bajar |
| `orquestador_log.txt` | historial de corridas | se crea de nuevo en la siguiente |
