# Orquestador de datos — Brandmark / NOA / Unibrandco

Pipeline que llena Supabase con las ventas, costos, stock y logística de los
tres negocios. Lo que produce lo lee el tablero web
([mi-tablero-app](https://github.com/valeriabrandmark/mi-tablero-app), desplegado
en <https://brandmark-business.vercel.app>), que consulta Postgres en vivo en
cada request: no hay caché intermedia, lo que está en Supabase es lo que se ve.

Corre en una computadora de la oficina, con una tarea programada **cada hora**.
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

El orden **es el del camino crítico**, no el histórico. Primero va todo lo que
`modelo.py` necesita para armar `gold.fact_ventas`, después `modelo.py`, y
recién al final lo que nadie está esperando.

Eso sale de mirar qué tabla lee cada transformación: **`modelo.py` no usa
`ml_publicaciones`, `ml_stock_full` ni `digip_pedidos`**. Antes el catálogo de
Mercado Libre —33 minutos— estaba en el medio, así que el tablero de ventas
esperaba media hora por datos que no usa.

#### Bloque 1 — el tablero de ventas queda al día (~2 min)

| # | Paso | Cada | Escribe | Corta si falla |
|---|---|---|---|---|
| 1 | `sigma.py --ventas` | siempre | `bronze.sigma_ventas` | sí |
| 2 | `sigma.py --catalogo` | 24 h | `bronze.sigma_articulos` | no |
| 3 | `mercadolibre.py --ventas` | siempre | `bronze.ml_ventas` | no |
| 4 | `ml_envios.py` | **con las ventas** | `bronze.ml_envios` | no |
| 5 | `tiendanube.py` | 4 h | `bronze.tn_pedidos`, `tn_pedidos_items` | no |
| 6 | `costos.py --si-cambio` | si cambió un Excel | `bronze.costos_historicos` | sí |
| 7 | **`modelo.py`** | siempre | **`gold.fact_ventas`** | sí |

#### Bloque 2 — nadie está esperando esto

| # | Paso | Cada | Escribe | Corta si falla |
|---|---|---|---|---|
| 8 | `ml_pulso.py` | **siempre** | `bronze.ml_estado_item`, `ml_precio_item` | no |
| 9 | `ml_pulso.py --solo-buybox` | 6 h | `bronze.ml_buybox_item` | no |
| 10 | `experimento.py --consolidar` | 6 h | `gold.fact_experimento` | no |
| 11 | `digip.py` | siempre | `bronze.digip_stock`, `digip_stock_detalle` | no |
| 12 | `digip_pedidos.py` | siempre | `bronze.digip_pedidos` | no |
| 13 | `digip_preparaciones.py` | 6 h | `bronze.digip_preparaciones` | no |
| 14 | `prorratear_flete.py` | 12 h | `gold.fact_ventas_flete` | no |
| 15 | `clasificar_clientes.py` | siempre | `gold.clientes_clasificados` | no |
| 16 | `mercadolibre.py --catalogo` | **1/día** | `bronze.ml_publicaciones`, `ml_stock_full`, `ml_stock_full_historico` | no |

**`ml_pulso.py` es el primero del bloque 2 y no el último, a propósito.** El
resto de este bloque refresca *fotos*: si un paso se saltea, su tabla conserva
la anterior y el dato queda viejo de una corrida, no roto. El pulso no es eso —
guarda «cómo estaba el catálogo a las 14:00 del martes», y esa observación no se
recupera después: la corrida siguiente ve las 15:00. **Un pulso salteado es un
agujero permanente en la historia**, y los agujeros son justo lo que arruina la
medición de elasticidad. Por eso va donde el presupuesto todavía no se gastó.

**`ml_envios.py` va pegado a las ventas, no cada N horas.** Le pide a la API el
costo de las órdenes que todavía no tiene, así que cada vez que entran órdenes
nuevas hay envíos nuevos que pedir. Si no corre, `modelo.py` arma `gold` en el
medio y esas líneas quedan con **envío en cero — o sea con el margen inflado**—
hasta la corrida siguiente. Es el mismo agujero que en julio dejó la
rentabilidad de Mercado Libre inflada. Medido el 20/08 con envíos cada 4 h:
**479 órdenes de agosto sin su envío**.

Se resuelve con `depende_de` y no poniéndoles la misma frecuencia, que sería lo
fácil: si mañana alguien cambia la de las ventas, los envíos la siguen solos.
Con dos números iguales escritos aparte, tarde o temprano uno se mueve y el otro
no.

**`1/día` no es lo mismo que `cada_horas: 24`.** Con 24 horas, un paso que ayer
corrió a las 15 hoy vuelve a las 15 — plena tarde, con gente mirando el tablero.
`primera_del_dia` lo pone en la **primera corrida del día**, que en esta oficina
es cuando se prende la máquina a la mañana. Y si estuvo apagada tres días, corre
en la primera que haya: no espera un horario fijo que ya pasó.

### De dónde salen esas frecuencias

Del log de 24 corridas reales, midiendo cuánto tarda cada paso:

| Paso | Mediana |
|---|---|
| `mercadolibre.py --catalogo` | ~7 min |
| `digip_preparaciones.py` | 9,8 min |
| `sigma.py` (las dos juntas) | 6,9 min |
| `modelo.py` | 5,9 min |
| `costos.py` | 1,4 min |
| el resto | menos de 30 s |

`mercadolibre.py --catalogo` no estaba en esa medición porque **nunca había
llegado a correr**: se midió el 19/08/2026, la primera vez. Son ~4.300 llamadas
a la API y tardaba **83 minutos**, de los cuales 72 eran el script durmiendo en
la `PAUSA` de 1 segundo. Bajarla a 0,3 lo dejó en 33 min; **mandar las llamadas
en paralelo lo dejó en ~2**.

### Por qué el stock Full va en paralelo

La API de Mercado Libre **no deja pedir varios inventarios juntos**: hay que
preguntar de a uno, y son ~3.800. En fila eso es media hora — el 88 % de todo lo
que tardaba el catálogo. De a 12 a la vez tarda poco más de un minuto.

**Cuántos hilos:** se midió con 12 y dio **13,8 min con cero inventarios
fallados** (0 filas con `error` sobre 3.830). Cero errores significa que el
límite de la API no se estaba tocando — lo que frenaba era la latencia de la
red, no Mercado Libre —, así que se subió a **24**, que debería dejarlo en ~7.

Cómo darse cuenta de que hay que bajarlo, sin leer el log entero:

```sql
select count(*) filter (where error is not null) from bronze.ml_stock_full;
```

Tiene que dar **0**. Si da otra cosa, la API empezó a rechazar y hay que volver
a 12 — y ahí ya sabemos que ése es el techo real.

La idea salió del Apps Script de la planilla de stock, que usa `fetchAll` por lo
mismo. Pero con una diferencia importante: **ese script hace
`if (código === 200)` y descarta todo lo demás en silencio**, incluido el 429
("demasiadas peticiones"). Un inventario que la API no contestó desaparece del
total sin dejar rastro, y el stock queda más bajo que la realidad sin que nada
avise. Acá el que falla queda como una fila con su `error` —no en cero— y el log
dice cuántos fueron.

**No se puede usar el `available_quantity` de las publicaciones** para evitarse
esas llamadas, aunque la tentación es grande: varias publicaciones comparten el
mismo `inventory_id`, así que sumar por publicación cuenta la misma unidad
varias veces. Verificado contra los datos: da **19.211 unidades contra las 10.577
reales**, y 1.512 de 3.830 inventarios no coinciden. Además el desglose de "no
disponible" (dañado, en revisión, reservado) **solo** está en ese endpoint, y son
699 unidades que conviene ver.

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

**Qué cuenta como venta en Mercado Libre: `paid` y `partially_refunded`.**

Las **canceladas** quedan afuera, y eso no se discute: no son venta. Pero son
muchas —~5.000 órdenes, **$14,2 M solo en agosto**, el 8,3 % del monto— así que
se miran aparte, en su propio panel del tablero, que las lee directo de `bronze`.

Las **parcialmente devueltas** sí entran, y antes no entraban. Son ventas reales
donde el cliente devolvió una parte y se quedó con el resto: dejarlas afuera
enteras borraba plata que sí entró (9 órdenes y $220.445 en agosto).

**Ojo con lo que eso no hace:** se cuentan por el importe completo, sin descontar
lo devuelto, porque la API no informa el monto de la devolución en la orden. O
sea que sobreestiman un poco. Es menos malo que el error anterior —contarlas en
cero— pero no es exacto, y el tablero lo aclara.

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

## Por qué modelo.py bajó de 6 minutos a menos de 1

Las consultas a `bronze` **no filtraban por fecha**. Para reconstruir la ventana
de 7 días se traían los cuatro meses enteros, se les parseaba el JSON a todas
las órdenes, y después se descartaba el 94 % con un `if` en Python:

| Tabla | Traía | Usaba (7 días) | De más |
|---|---|---|---|
| `bronze.ml_ventas` | 38.490 | 2.226 | **17×** |
| `bronze.sigma_ventas` | 10.453 | 565 | **18×** |

Ahí se iban casi seis minutos de cada corrida. Ahora el filtro va en el `WHERE`.

**La ventana de 7 días no era el problema**, y por eso se dejó en 7: entre 7 días
y 2 hay 1.300 filas de diferencia — segundos. Lo caro era traer el histórico. Y
esos 7 días son la red que cubre a la máquina cuando pasa medio día apagada.

`piso_sql()` le pasa a SQL **un día antes** de `CUTOFF`. Las fechas en `bronze`
son texto y vienen en el huso de cada origen —Mercado Libre manda `-04:00`, que
no es el de Argentina—, así que una orden de las 23 hs puede caer en el día
siguiente al convertirla. El filtro exacto lo sigue haciendo Python. Verificado
contra los datos: **0 filas perdidas**.

---

## Nadie tiene que ver una tabla a medio escribir

El borrado y la inserción estaban en **transacciones separadas**:

```python
with engine.begin() as con:
    ... DELETE FROM gold.fact_ventas WHERE fecha >= cutoff ...   # confirmado
df.to_sql(...)                                                   # recién acá
```

Entre una cosa y la otra, `gold.fact_ventas` —que el tablero lee **en vivo**— se
quedaba sin los últimos 7 días. Quien entrara justo ahí veía la semana en cero y
lo leía como que no se vendió nada. No es teórico: en mitad de una corrida
`bronze.ml_ventas` tenía 40.588 órdenes cuando un minuto antes tenía 43.207.

Ahora las dos operaciones van en **una sola transacción**, en todos los scripts.
Quien consulta sigue viendo la versión anterior completa hasta que la nueva está
entera.

**`DELETE` y no `TRUNCATE`:** los dos son transaccionales, pero `TRUNCATE` toma
un lock exclusivo que ahora duraría toda la inserción y dejaría al tablero
esperando. `DELETE` usa el control de versiones de Postgres y no bloquea a los
lectores. Con estas tablas —miles de filas, no millones— la diferencia de
velocidad no se nota.

---

## La única tabla que solo crece: la foto diaria del stock

`bronze.ml_stock_full` se sobrescribe entera en cada corrida, así que sabe
cuánto stock hay **hoy** y nada más. Con eso alcanza para "cuántas unidades
tengo paradas", pero no para la pregunta que de verdad importa:

> ¿cuántos días seguidos lleva este artículo **con stock y sin venderse**?

Esa cuenta necesita saber si había stock **cada día**, y eso **no se puede
reconstruir hacia atrás**: el dato de ayer ya se pisó. La única forma es empezar
a guardarlo. Por eso `bronze.ml_stock_full_historico` es la única tabla del
pipeline que solo **agrega**: una foto por día, ~3.800 filas (1,4 M al año, que
para Postgres no es nada).

Empezó a acumular el **20/08/2026**. Antes de esa fecha no hay historia y no la
va a haber nunca — lo que se puede medir hacia atrás es "días desde la última
venta", que sale de `gold.fact_ventas` y llega hasta mayo.

**Es idempotente:** la tabla tiene clave primaria `(fecha, inventory_id)`, así
que correr el catálogo dos veces el mismo día no puede duplicar el día.

> **Antes no lo era.** La versión original borraba el día y lo insertaba en una
> transacción, pero si el `DELETE` fallaba —y fallaba *siempre* la primera vez,
> porque la tabla todavía no existía— el `except` reintentaba el `INSERT` solo,
> sin el borrado. En el arranque funcionaba de casualidad (no había nada que
> duplicar); cualquier otro fallo del `DELETE` dejaba el día dos veces sin que
> nada avisara. Hoy la tabla se crea con DDL explícito en `esquema.py` y la PK
> hace imposible el duplicado.

---

## Medidor de elasticidad de precios

El experimento reparte los artículos en tres grupos. Cada grupo pasa una semana
en cada banda de markup sobre el costo (**10-18 %**, **18-25 %**, **25-35 %**) y
después rota, de modo que en tres semanas los tres grupos pasaron por las tres
bandas. Es un **cuadrado latino**: sirve para que el efecto «esta semana se
vendió más» —feriados, quincena, campañas de ML— no se confunda con el efecto de
la banda, porque cada semana contiene a las tres bandas al mismo tiempo.

### Mercado Libre sí avisa el quiebre. Lo que no guarda es *desde cuándo*

La API dice perfectamente si una publicación quebró stock: ML la pausa sola y le
pone `sub_status = ["out_of_stock"]`. Ese dato ya está en
`bronze.ml_publicaciones` — hoy con **4.732 de 8.509** publicaciones marcadas así
(55,6 %) y **2.140 de 3.830** inventarios de Full en cero.

Lo que no existía es la **historia**, porque esa tabla se escribe con
`modo="replace"`: sabe cómo está la publicación hoy y pisa lo de ayer. Sin
historia no se puede contestar «cuántas horas de esta semana el artículo estuvo
realmente a la venta», que es la pregunta que hace falta.

### Por qué el denominador es lo único que importa

Comparar **unidades** entre semanas no mide elasticidad: mide disponibilidad.

Si un SKU quebró stock el martes de la semana de markup 25-35 %, esa semana
muestra menos unidades y la lectura ingenua es «con markup alto vende menos» —
cuando en realidad no vendió porque no estaba a la venta. Con más de la mitad
del catálogo quebrado en cualquier momento dado, ese sesgo **es más grande que
el efecto que se busca medir**.

Por eso la métrica es una tasa: `uds_por_dia_vendible` = unidades ÷ (horas a la
venta ÷ 24). Y el quiebre deja de ser un flag para pasar a ser lo que descuenta
el denominador.

Ojo que el stock no es la única causa de exposición cero: hoy hay además 38
publicaciones en `under_review` y 135 pausadas por nosotros. Todo eso es tiempo
sin vender que **no** es quiebre, y por eso `gold.fact_experimento` reparte la
ventana en cuatro columnas que siempre suman `horas_ventana`:

| Columna | Qué es |
|---|---|
| `horas_vendible` | se podía comprar |
| `horas_sin_stock` | quebrado |
| `horas_pausada` | pausada por nosotros, en revisión, cerrada |
| `horas_sin_dato` | **el pulso no corrió**: ni vendible ni quebrado |

### Cómo se guarda: tramos, no fotos

Lo obvio sería guardar las 8.509 publicaciones en cada pulso. Con un pulso cada
2 horas eso da 102.000 filas por día y **37 millones al año** para un dato que
casi nunca cambia. Por eso `ml_pulso.py` guarda **tramos** (SCD tipo 2): una fila
nueva sólo cuando el estado cambia de verdad. Un artículo que estuvo vendible
tres semanas es *una* fila con `desde` y `hasta`, no 250 fotos idénticas.

El precio va en su propia tabla a propósito. Son dos preguntas distintas y
cambian a ritmos distintos: la disponibilidad se mueve pocas veces por mes, el
precio lo mueve el repricer varias veces por día. Juntos, cada cambio de precio
abriría también un tramo de disponibilidad y la tabla que tenía que ser chica se
fragmentaría hasta volver al problema que veníamos a evitar.

**`visto_hasta` no es lo mismo que `hasta`.** Es el último pulso que confirmó ese
estado. Si el pipeline estuvo caído dos días, el tramo abierto sigue diciendo
«vendible» pero `visto_hasta` quedó dos días atrás: esas 48 horas son horas *sin
dato*, no horas a la venta. Contarlas como vendibles inflaría el denominador
justo en las semanas en que falló el pipeline, y nada lo delataría. De ahí también
`bronze.ml_pulso_corrida`, que registra cada pulso: es lo que permite decir «esto
no lo miramos» en vez de suponer que el estado se mantuvo.

### El buy box no es un lujo, es el otro censor

**3.880 de los 4.360 SKU del experimento (89 %) están en catálogo.** En catálogo,
el que no gana la caja vende cerca de cero sin importar su precio. Y como la
estrategia es «ponerse apenas debajo del competidor», perder la caja está
**correlacionado con el tratamiento**: la semana de markup alto es justo la
semana en la que más se pierde.

Sin `bronze.ml_buybox_item`, «vendió menos porque estaba caro» y «vendió menos
porque le sacaron la caja» son indistinguibles, y las dos empujan para el mismo
lado: **la elasticidad medida sale exagerada**.

`price_to_win` no tiene multiget —es una llamada por publicación—, así que va en
su propio paso cada 6 h y sólo para los SKU bajo experimento. En cada pulso serían
~35.000 llamadas por día sólo para esto.

### La banda no es la variable explicativa: el markup realizado sí

El experimento asigna una *banda*, pero el repricer sigue al competidor, así que
dentro de 25-35 % el markup realmente aplicado se mueve —y se mueve dentro del
día. `markup_realizado` se calcula desde `bronze.ml_precio_item`, ponderado por
las **horas vendibles** (el precio que estuvo puesto con la publicación pausada
no lo vio nadie) y descontándole el IVA, porque el precio de ML lo lleva adentro
y el costo no.

### El objetivo no es vender más: es el equilibrio

El máximo de margen por día **no alcanza como criterio**, y por eso
`mejorBanda` (en `lib/elasticidad.ts` del tablero) no devuelve el máximo pelado.

Entre dos bandas que dejan lo mismo por día conviene la de **markup más alto**,
porque vende menos unidades para ganar la misma plata. Dicho con el ejemplo del
negocio: *vender 50 unidades marcando 10 % y vender 20 marcando 30 % no son
equivalentes aunque den el mismo total.* Importa por dos motivos concretos:

1. **El stock dura más.** Quemar la mercadería a mitad de semana deja al
   artículo sin nada que vender hasta que se repone, y en Full reponer no es
   inmediato.
2. **Cada unidad movida cuesta trabajo** —preparar, despachar, atender— que no
   depende de a cuánto se vendió.

La regla que quedó: gana la banda de mayor margen por día, salvo que otra quede
dentro de `EMPATE_TECNICO` (10 %) y tenga markup más alto. Ese 10 % **no es un
número estadístico, es una preferencia del negocio**, y está declarado como
constante justamente para que se pueda discutir y mover.

> **Un sesgo que hay que tener presente.** Medir por día a la venta corrige los
> quiebres que no dependen del precio, pero cuando el quiebre lo *causa* el
> precio bajo —se vendió todo el martes— la banda barata queda medida sobre
> menos horas y sale **mejor** de lo que fue. Por eso el tablero muestra
> "Perdido por quiebre" al lado de la tasa: si una banda quema stock, ahí se ve.

### Entran TODOS los artículos

El experimento cubre los **4.360 SKU** con publicación en ML, no una selección.

Al principio se pedía haber vendido algo en los últimos 60 días, y estaba mal:
dejaba afuera 2.077 artículos —casi la mitad— con el argumento de que sin ventas
recientes no aportan señal. Pero un artículo que no vendió nada en dos meses es
justamente uno de los que hay que revisar: **puede no estar vendiendo porque
está caro**. Excluirlo daba por sentada la respuesta que el experimento tiene
que dar.

### Qué se puede concluir, y qué no

Lo que sigue siendo cierto es el volumen. Medido el 21/08/2026 sobre los 2.283
SKU que sí vendieron algo en 60 días:

| | |
|---|---|
| SKU en el experimento | 4.360 |
| De ésos, sin ninguna venta en 60 días | 2.077 |
| **Mediana de ventas por SKU y semana** | **0,58 unidades** |
| SKU que venden menos de 1 por semana | 1.316 |
| SKU que venden 5 o más por semana | 93 |
| Participación de los 200 más vendidos | 45,6 % de las unidades |

Esto **no** invalida el experimento, pero define qué preguntas puede contestar:

- **Sí puede** decir cuál es la mejor banda a nivel agregado y por segmento
  (proveedor, marca, rango de precio). Con ~1.450 SKU por grupo y siete días,
  ahí sobra volumen.
- **No puede** dar «el markup exacto para cada producto» en tres semanas. Un SKU
  que vende 0,58 unidades por semana no distingue 10 % de 35 % ni en tres
  semanas ni en diez: la diferencia entre vender 0 y vender 1 es ruido. Sólo el
  puñado de arriba —del orden de los 93 a 200 SKU de mayor rotación— tiene
  volumen para una lectura individual.

Por eso `gold.fact_experimento` guarda el grano SKU-semana pero el tablero lo lee
agregado, y marca explícitamente qué filas tienen volumen suficiente para leerse
solas. La conclusión útil de tres semanas es una **política de markup por
segmento**, más una lista corta de artículos con evidencia propia. Para el resto,
lo que hace falta es dejar el experimento corriendo más tiempo — y por eso los
artículos sin ventas entran igual: adentro suman al agregado de su banda.

### Qué NO cancela el cuadrado latino

- **Arrastre entre semanas.** ML tarda en digerir un cambio de precio: la
  posición en el listado y el rendimiento de la publicidad se reacomodan durante
  el día siguiente. Por eso `experimento.py` descarta las primeras **24 h** de
  cada semana (`LAVADO`); sin eso, las ventas del lunes —que todavía responden al
  precio de la semana anterior— se le atribuyen a la banda nueva.
- **Composición de los grupos.** El reparto no es al azar: se ordena por unidades
  y se reparte en serpentina (1-2-3, 3-2-1, …), con la disponibilidad de hoy como
  segunda clave. Con 4.360 SKU al azar los grupos quedarían parecidos *en
  promedio*, pero el experimento no se juega en el promedio: los 200 más vendidos
  son el 45,6 % de las unidades, así que un solo artículo de alta rotación mal
  repartido mueve el total de su grupo más que cien de cola. La serpentina además
  es **reproducible**, que es lo que permite auditar un resultado meses después.

### Comandos

```bash
python esquema.py                                  # crea las tablas (idempotente)
python ml_pulso.py                                 # un pulso a mano
python experimento.py --asignar --desde 2026-08-25 # arma el cuadrado latino
python experimento.py --consolidar                 # recalcula gold.fact_experimento
```

La asignación se escribe **una vez** y no se toca: reescribirla a mitad del
experimento le cambiaría la banda a semanas ya medidas. Para rehacerla hay que
borrarla por nombre, a mano.

**La fecha se toma en hora argentina, con huso explícito.** El catálogo corre a
la mañana, pero si algún día corriera después de las 21, un `date.today()` en
UTC ya sería el día siguiente y la foto quedaría fechada mal — el mismo error
que duplicó 790 ventas.

---

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
| `PRESUPUESTO_TOTAL` | la corrida entera | 50 min |

**El presupuesto total es el que resuelve el problema de fondo.** Antes de cada
paso se mira si todavía entra: si quedan 5 minutos y el paso puede tardar 30, se
saltea y la corrida termina. Con 50 minutos contra un intervalo de 60, la
corrida **siempre** libera la máquina antes del disparo siguiente.

Lo que se saltea no se pierde: al no quedar registrado como "corrió bien", entra
primero en la corrida de después.

**Un paso cortado por tiempo NO se reintenta**, aunque le queden intentos.
Reintentar costaría otro techo entero, y dos techos seguidos se comen la ventana
—que es justo lo que el tope viene a evitar—. Además un cuelgue rara vez es
transitorio: es un socket esperando una respuesta que no llega, y el reintento se
cuelga igual.

Si en el log aparece seguido `NO ENTRARON en los 50 min`, algún paso se volvió
lento o el presupuesto quedó corto. Es una señal para mirar, no para ignorar.

### Qué hubo que cambiar al pasar de 2 horas a 1

El intervalo se bajó a una hora el 21/08/2026, para que el pulso mida el quiebre
de stock con el doble de resolución. Eso obligó a mover dos cosas:

**El presupuesto, de 100 a 50 minutos.** Con la tarea disparando cada 60, un
presupuesto de 100 era *más largo que el intervalo*: una corrida normal podía
seguir viva cuando llegaba el disparo siguiente, y ahí vuelve el problema que
todo esto viene a evitar — la corrida nueva no arranca y el pipeline se saltea
horas en silencio. Ahora son 50 contra 60, con 10 de colchón.

**Los techos, que es la parte que no se ve.** El chequeo es
`gastado + techo > presupuesto`: un paso se saltea cuando su **techo** no entra
en lo que queda, no cuando su duración real no entra. Con eso, un paso cuyo
techo sea mayor o igual al presupuesto **no corre nunca** — se saltea en todas
las corridas, y nadie lo nota porque se saltea "por presupuesto", que es un
mensaje que suena normal. `ml_envios.py`, con techo de 45, caía justo ahí.

Pero el problema de fondo estaba en *todos* los techos: se habían puesto como
valores absolutos generosos y no según la mediana de cada paso. Había pasos de
**medio minuto con techos de 20**, o sea reservando 40 veces lo que tardan. Con
un intervalo de 120 sobraba lugar y no molestaba; con 60, esas reservas se comen
el presupuesto y hacen que se saltee lo que va abajo. Ahora se aplica la regla
que este README ya decía —3 o 4 veces la mediana medida— con un piso de 10 min.

Simulando la corrida más pesada del día (cuando disparan también los pasos de
24 h y de 6 h) con las medianas reales, da **35 minutos** y el único que queda
afuera es `mercadolibre.py --catalogo`, que al ser `primera_del_dia` reintenta y
entra en la vuelta siguiente. En una corrida normal son ~22 minutos y no se
saltea nada.

**Los dos pasos del experimento se movieron junto al pulso**, en vez de ir
últimos. Leen justo lo que el pulso acaba de escribir, y así le ceden el lugar a
los pasos pesados que corren una vez por día, que son los que se pueden permitir
esperar una hora.

### Cómo tiene que estar el Programador de tareas

Si el orquestador deja de correr solo, mirar esto antes que el código. En
PowerShell (la carpeta da igual, estos comandos le preguntan a Windows):

```powershell
Get-ScheduledTask -TaskName "*orquestador*" | Get-ScheduledTaskInfo
```

`LastTaskResult` en **`267009`** (`0x41301`) significa "la tarea sigue
ejecutándose": hay una corrida colgada y por eso no arranca ninguna nueva.

Tres cosas que conviene tener puestas en la tarea:

- **Disparador → "Repetir cada 1 hora durante: Indefinidamente".** Si dice una
  duración corta, la repetición se corta sola cuando esa duración se cumple.
- **Configuración → "Detener la tarea si se ejecuta más de: 1 hora"**, y
  **"Si la tarea ya se está ejecutando: Detener la instancia existente"**. Es el
  cinturón por si algún día algo se cuelga fuera de los topes de Python.
- **Condiciones → destildar "Iniciar la tarea solo si el equipo está con
  alimentación de CA"**, y tildar **"Reactivar el equipo para ejecutar esta
  tarea"** si la máquina se suspende.

---

## Qué corta el pipeline y qué no

`critico: True` significa **"los de abajo darían resultados MAL"**, no "este dato
es importante". No es lo mismo, y confundirlo salió caro.

El 20/08 el servidor de SIGMA no contestaba. Como `sigma.py --ventas` estaba
marcado crítico, la corrida abortó ahí — y con ella **Mercado Libre, Tienda Nube
y los stocks**, que no tienen nada que ver con SIGMA. Una caída de un proveedor
dejó el tablero entero sin actualizar.

Que una extracción falle **no** hace que lo de abajo esté mal: su tabla en
`bronze` conserva la última foto buena y `modelo.py` la usa igual. El dato queda
**viejo de una corrida, no roto**. Y eso es seguro justamente porque las
escrituras son atómicas: una tabla nunca queda a medio escribir ni vacía.

Hoy el único crítico es **`modelo.py`**, y por una razón concreta:
`prorratear_flete.py` lee `gold.fact_ventas` para repartir el flete. Si
`modelo.py` no reconstruyó la ventana, el flete se prorratearía sobre una foto
que no corresponde, y eso sí da un número **mal**, no viejo. Los demás pasos
siguen datos; ése sigue una cuenta.

Un paso no crítico que falla queda como `FALLA xN` en `--listar`, con las
últimas líneas del error. Ahí es donde tiene que verse.

### Los timeouts son dos números, no uno

```python
TIMEOUT_HTTP = (10, 120)   # (conectar, leer)
```

Con un solo valor, `timeout=120` es también el de conexión: tres intentos contra
un host que no contesta se van **seis minutos** antes de rendirse. Separados, el
intento muere en 10 segundos si no hay con quién hablar, y sigue teniendo su
tiempo largo para una consulta pesada que sí arrancó.

El de conexión es corto a propósito: un servidor sano acepta la conexión en
milisegundos. Si tarda diez segundos, no está pensando — no está.

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
