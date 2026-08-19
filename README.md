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
| 1 | `sigma.py` | siempre | `bronze.sigma_ventas`, `sigma_articulos` | sí |
| 2 | `digip_pedidos.py` | siempre | `bronze.digip_pedidos` | sí |
| 3 | `digip_preparaciones.py` | siempre | `bronze.digip_preparaciones` | sí |
| 4 | `mercadolibre.py --ventas` | 2 h | `bronze.ml_ventas` | no |
| 5 | `ml_envios.py` | 4 h | `bronze.ml_envios` | no |
| 6 | `mercadolibre.py --catalogo` | 12 h | `bronze.ml_publicaciones`, `ml_stock_full` | no |
| 7 | `digip.py` | 4 h | `bronze.digip_stock`, `digip_stock_detalle` | no |
| 8 | `tiendanube.py` | 4 h | `bronze.tn_pedidos_items` | no |
| 9 | `costos.py` | siempre | `bronze.costos_historicos` | sí |
| 10 | `modelo.py` | siempre | **`gold.fact_ventas`** | sí |
| 11 | `prorratear_flete.py` | siempre | `gold.fact_ventas_flete` | sí |
| 12 | `clasificar_clientes.py` | siempre | `gold.clientes_clasificados` | sí |

Las frecuencias existen porque los pasos no cuestan lo mismo: el catálogo de
Mercado Libre son ~3.800 llamadas a la API y no se puede pedir cada hora. El
orquestador se acuerda en `estado_pasos.json` (local, no se sube) de cuándo
terminó bien cada uno. Si la computadora estuvo apagada tres días, el paso
vencido corre en la primera pasada — no espera un horario fijo que ya pasó.

Los pasos que no cortan el pipeline son extracciones sueltas: que se quede vieja
una parte es mejor que no actualizar nada.

---

## Flags

Solo tres scripts aceptan argumentos. El resto se corre pelado.

| Script | Flags | Sin flags |
|---|---|---|
| `modelo.py` | `--dias N`, `--todo` | ventana de 7 días |
| `mercadolibre.py` | `--ventas`, `--catalogo` | corre todo |
| `orquestador.py` | `--forzar`, `--solo`, `--listar` | respeta frecuencias |
| `costos.py` | `AAAA-MM` (posicional), `--listar` | todos los meses |
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

**Las ventanas móviles reprocesan, no acumulan.** `sigma.py`, `mercadolibre.py` y
`modelo.py` borran su ventana y la vuelven a insertar. Lo anterior a la ventana
queda intacto — por eso un dato que llega tarde a `bronze` **no entra solo** a
`gold`: hay que reconstruir con `--dias` o `--todo`.

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
