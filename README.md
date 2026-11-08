# TenderoBot

Agente de inventario para tiendas de barrio en Colombia. Funciona en Telegram: el tendero le cuenta lo que vende (por voz o por texto) y le manda fotos del estante. El agente lleva el inventario y arma el pedido sugerido para el distribuidor.

Proyecto del **Agent Challenge** (categoría Comercio).

## El problema

El tendero no lleva inventario porque le toma más tiempo del que tiene. Por eso pide mal: se queda sin lo que más vende y se llena de lo que no sale.

- **Quién paga:** distribuidores de consumo masivo y fintechs de tenderos.
- **De dónde salen los datos:** las ventas que registra el tendero y el catálogo del distribuidor.

## Qué hace

| El tendero... | El agente... |
|---|---|
| Escribe `/start` | El bot pregunta si ya tiene su tienda registrada (🔑 *Sí, ya tengo cuenta* o 🆕 *No, quiero registrar mi tienda*) y le pide tocar 📱 Compartir mi número: Telegram le manda al bot el número verificado de esa cuenta (no se puede escribir otro), que funciona como su llave. Si el número es de una tienda, entra. Si dijo que ya tenía cuenta y el número no es de ninguna, se le avisa y se le ofrece registrarse; nunca se crea una tienda sin que lo pida. Si escoge registrarse, **registra su tienda ahí mismo**: primero el permiso de datos (Ley 1581) y luego 8 preguntas (nombre, dueño, cédula o NIT, ciudad, barrio, dirección, horario de recibo y forma de pago) y la ubicación con el botón de Telegram. Sin códigos ni contraseñas. |
| Escribe `/proveedores` | Le muestra los proveedores que llegan a su ciudad, con lo que venden. Marca los que quiera (puede varios) y a cada uno le llega una solicitud a su panel. Cuando un proveedor acepta, le avisa por Telegram y le pide escoger sus días de entrega con él; si rechaza, le cuenta el motivo. Puede volver a `/proveedores` para ver con quién trabaja, agregar más o **dejar de trabajar con uno** (por ejemplo, si otro le vende más barato): pide confirmación, borra el pedido que estaba armando con él, no cancela lo que ya le pidió y no pierde el inventario, porque las ventas y conteos de un producto que también vende otro de sus proveedores siguen en el mismo artículo. Si era su proveedor principal, pasa a serlo el siguiente. |
| Manda un audio o escribe *"vendí 2 Coca-Cola litro y un pan tajado"* | Entiende productos y cantidades, los busca en el catálogo de **todos** sus proveedores y muestra un resumen. Solo guarda si el tendero pulsa ✅ Confirmar. |
| Manda una foto del estante | Estima cuántas unidades hay de cada producto, avisa que es una estimación y deja corregir antes de guardar. |
| Escribe `/stock` o `/alertas` | Muestra el inventario actual, qué se va a acabar y qué no se está vendiendo. Un producto que venden dos proveedores es uno solo en la tienda: un solo stock. |
| Pregunta *"¿quién me vende más barata la coca litro?"* | Compara los precios de sus proveedores. Cada producto se le pide al más barato con existencias, salvo que el tendero escoja otro: *"la coca pídesela a Postobón"*. |
| Escribe `/catalogo` | Muestra lo que tienen todos sus proveedores, con el precio de cada uno, ⭐ en el que le pide y cuál es el más barato. |
| Escribe `/pedido` | Arma **un pedido por proveedor** para su próxima entrega, en cajas, con el motivo de cada producto, el total en pesos y la hora límite de ese proveedor. Cada pedido se envía con su botón. |
| Responde *"quita el arroz"* o *"ponle 2 cajas más de coca"* (por texto o audio) | Ajusta el pedido del proveedor de ese producto. El tendero tiene la última palabra; el camión lleva solo lo que él envió. |
| No ha pedido y se acerca la hora límite | Le manda un recordatorio por cada proveedor: *"El jueves te llega mercancía de Postobón. Tienes hasta hoy a las 6 p. m."* |
| Escribe `/recibido` | Muestra lo que traía el pedido (y de qué proveedor) para confirmarlo. Si algo llegó incompleto, el tendero escribe lo que llegó de verdad (*"coca litro 8"*) y solo eso se suma al inventario. |
| Escribe `/salir` | Cierra la sesión de la tienda en ese chat (pide confirmación). Los datos se conservan; para volver a entrar basta con compartir el número otra vez. Si entra otra cuenta de Telegram, se le pide su propia autorización de datos. |
| No usa el bot en 24 horas | Le avisa, de día, que la sesión sigue abierta, con botones para cerrarla o seguir. Un solo aviso hasta que vuelva a usar el bot. |

La raíz del sitio (`/`) es la **página pública**: explica qué hace TenderoBot. Los **tenderos** se registran en Telegram (la página los lleva al bot). Los **proveedores** piden entrar desde la página, después de confirmar su correo con un código (NIT y dígito de verificación, matrícula mercantil, representante legal, cobertura y categorías); los aprueba el administrador.

Hay **un solo login** en `/login`: proveedores y administrador entran con correo y contraseña y cada uno llega a su panel. Cada correo tiene un solo papel (administrador, proveedor o tendero), también en los comandos que crean cuentas.

El **administrador de TenderoBot** tiene su panel en `/admin`: aprueba o rechaza proveedores, los crea, edita, suspende o elimina, maneja sus cuentas (enlaces por correo para crear o cambiar la contraseña; el administrador nunca la conoce) y bloquea productos que no sean apropiados. Un proveedor suspendido no entra a su panel y sus tenderos no pueden usar el bot. La cuenta de administrador solo se crea por comando: `python -m scripts.admin_user tu@correo.com` (con `--eliminar` se quita).

El proveedor tiene un **panel web** (`/panel`):

- **Solicitudes:** las tiendas que lo escogieron en Telegram (aceptar con sus días de ruta y anticipación, o rechazar con motivo) y las que llegaron antes por la página.
- **Tiendas:** las que trabajan con él, aunque también les compren a otros proveedores. **De cada tienda ve solo lo suyo:** sus productos (stock, alertas, ABC, ventas), su pedido sugerido con sus días y los pedidos que le hicieron. Nunca lo que la tienda le compra a otro proveedor. Los datos del tendero los edita solo su proveedor principal (el que la registró o el primero que la aceptó).
- **Resumen, alertas, gráfico de ventas, clasificación ABC y demanda** de la próxima semana, con la misma regla.
- **Productos:** maneja su catálogo (agrega productos, cambia precios, actualiza cuántas unidades tiene y desactiva lo que ya no vende). Al hacer clic en un producto ve qué días de la semana se vende más, en todas sus tiendas o en una sola.
- El bot solo sugiere y deja pedir lo que el proveedor tiene, en cajas completas. Cuando el tendero envía un pedido, esas unidades se descuentan.
- Cuando cambia un precio o llega un producto nuevo, las tiendas que trabajan con él reciben un aviso por Telegram.

## Cómo usa Grok

Grok es un **agente con herramientas** (tool calling): lee lo que el tendero escribe o dice, decide qué herramienta usar y le responde. Las herramientas son código normal: el inventario, las alertas y el pedido los calcula el motor, nunca el modelo.

```
Voz ──► Grok /stt (voz a texto) ──┐
Texto ────────────────────────────┴─► Agente (Grok + herramientas) ──► herramienta (código)
                                          ▲                              │
                                          └──── resultado en JSON ◄──────┘
                                          │
                                          ▼
                     Respuesta corta + resumen con botones ✅ Confirmar / ❌ Cancelar
                                          │
                                          ▼
                   PostgreSQL ──► motor de inventario (código) ──► /stock, /alertas, /pedido, panel

Foto ──► Grok con imagen (JSON con esquema) ──► conteo para confirmar
```

| Herramienta | Qué hace |
|---|---|
| `registrar_ventas` | Prepara el resumen de ventas para que el tendero confirme. Busca sola los productos en el catálogo. |
| `consultar_stock` | Cuántas unidades tiene la tienda, de todo o de un producto. |
| `ver_alertas` | Qué se va a acabar antes de la próxima entrega y qué no se está vendiendo. |
| `buscar_producto` | Precio, presentación y si está agotado donde el proveedor. |
| `armar_pedido` | Arma el pedido sugerido para la próxima entrega y lo deja como borrador. |
| `ajustar_pedido` | Agrega, quita o cambia productos del pedido que el tendero está revisando (el del proveedor de cada producto). |
| `comparar_proveedores` | Precios de un producto entre los proveedores del tendero, o la lista de lo que se ahorra pidiéndole al más barato. |
| `preferir_proveedor` | Cambia a qué proveedor se le pide un producto. Solo cuando el tendero lo pide. |

- Ninguna herramienta guarda ventas ni envía pedidos: solo crean borradores. El tendero siempre confirma con un botón.
- **Reglas que verifica el código, no el modelo.** Si el tendero no dijo cuántas unidades vendió, o habla de unidades sueltas o docenas de un producto que se vende en paquete, esa venta no se registra y el bot le pregunta. Grok tiene que citar las palabras con que el tendero dijo la cantidad (`texto_cantidad`), y la herramienta comprueba que esas palabras estén en el mensaje. Esto salió de la evaluación: con solo instrucciones, Grok a veces asumía 1 o inventaba la cita (ver [eval/resultados/HISTORIAL.md](eval/resultados/HISTORIAL.md)).
- El ciclo tiene máximo 4 vueltas. Si Grok falla antes de hacer algo, el bot usa la extracción directa de antes (sin herramientas). Con `AGENT_ENABLED=false` se usa siempre esa extracción.
- La conexión es con la API del reto (`https://api.reto.pltk.mx/v1`), compatible con OpenAI. El modelo por defecto es `grok-4.20-0309-non-reasoning`.
- Código: el agente en [app/ai/agent.py](app/ai/agent.py), las herramientas en [app/ai/tools.py](app/ai/tools.py), las instrucciones en [app/ai/prompts.py](app/ai/prompts.py) y la conexión en [app/ai/grok_client.py](app/ai/grok_client.py).

### Ejemplo de ejecución

Ejecución real contra la pasarela (tienda de la evaluación). El tendero escribe en Telegram:

```
vendí 2 Coca-Cola litro, un pan tajado y 3 bon bon bum
```

Grok llama a la herramienta:

```json
{"name": "registrar_ventas", "arguments": {"ventas": [
  {"producto": "Coca-Cola litro", "cantidad": 2, "texto_cantidad": "2"},
  {"producto": "pan tajado",      "cantidad": 1, "texto_cantidad": "un"},
  {"producto": "bon bon bum",     "cantidad": 3, "texto_cantidad": "3"}
]}}
```

La herramienta busca los productos, crea el borrador y le devuelve a Grok:

```json
{"borrador": "creado",
 "reconocidas": [{"producto": "Coca-Cola 1 litro", "cantidad": 2},
                 {"producto": "Pan tajado", "cantidad": 1},
                 {"producto": "Bon Bon Bum", "cantidad": 3}],
 "no_reconocidas": [],
 "nota": "Aún no se guardó: el tendero debe pulsar ✅ Confirmar en el resumen."}
```

Grok responde *"Revisa el resumen de ventas y pulsa ✅ Confirmar para guardar."* y el bot muestra el resumen con los botones. Esta tarea usó 2 llamadas a Grok, 3.248 tokens de entrada (1.664 en caché) y 79 de salida, y costó US$0,0025.

### Bitácora de consumo

Cada llamada a Grok queda en la tabla `llm_usage`: tokens de entrada, en caché y de salida, costo, latencia, si falló, y la tarea a la que pertenece (un mensaje del tendero puede necesitar varias llamadas). El costo sale de la pasarela, que lo reporta en cada respuesta (`usage.cost_in_usd_ticks`, donde 1 USD = 10¹⁰ ticks). Solo se guardan números: nunca el texto del tendero ni la respuesta del modelo. Código en [app/ai/usage.py](app/ai/usage.py) y [app/usage_log.py](app/usage_log.py).

## Evaluación y costo por tarea

Set propio de 54 casos en [eval/casos.jsonl](eval/casos.jsonl): ventas (también con trampas, como "cerveza Águila" o "media docena de huevos"), pedido, consultas, conversación y seguridad. El agente corre de verdad contra Grok, sobre una tienda fija ([eval/tienda.json](eval/tienda.json)), y un calificador compara lo que hizo con lo esperado. Cómo agregar casos: [eval/README.md](eval/README.md).

```bash
python -m scripts.run_eval        # corre los casos y escribe eval/resultados/<fecha>/resumen.md
python -m scripts.cost_report     # costo por tarea resuelta y proyección por tienda
```

**Resultado:** 47 de 48 casos ejecutados (98%), estable en 3 repeticiones. Los 6 casos de audio y foto esperan archivos reales y no cuentan.

| Tipo de tarea | Acierto | Costo por tarea resuelta |
|---|---|---|
| Ventas | 96% | US$0,00133 ($4,23) |
| Pedido | 100% | US$0,00119 ($3,78) |
| Consulta | 100% | US$0,00117 ($3,71) |
| Conversación | 100% | US$0,00068 ($2,17) |

Con 20 mensajes al día, una tienda cuesta unos **US$0,71 al mes (~$2.270)**. Detalle, supuestos y lo que no se pudo costear (la transcripción de audio): [eval/resultados/COSTOS.md](eval/resultados/COSTOS.md).

La primera corrida dio 92%. Las mejoras, incluida una regresión al 85% que obligó a pasar reglas del modelo al código, están en [eval/resultados/HISTORIAL.md](eval/resultados/HISTORIAL.md).

**Falla todavía:** *"vendí 10 huevos"* se registra como 10 cubetas de 30. El tendero lo ve en el resumen antes de confirmar, pero arreglarlo bien requiere que el catálogo diga en qué unidad se vende cada producto.

## Semana de carga (S4)

El agente procesa **1.000 mensajes o más en una corrida continua de al menos 12 horas, sin supervisión**, y deja la bitácora completa: tokens, costo, tasa de éxito y errores.

**Unidad de trabajo:** un mensaje de un tendero que el agente atiende de punta a punta, igual que en el bot. Grok escoge las herramientas, las herramientas leen y escriben en la base (borradores de ventas y de pedido, stock, alertas) y al final el código revisa si el resultado fue el correcto. Si la venta quedó bien, se confirma como lo haría el tendero y mueve el inventario; si quedó mal, se cancela. Definición en [app/load/units.py](app/load/units.py).

### Cómo funciona

| Pieza | Qué hace | Código |
|---|---|---|
| Base aparte | PostgreSQL local en Docker, con el mismo catálogo y 3 tiendas con 60 días de historia. Las ventas de la carga nunca caen en la base real: los scripts se niegan a correr si `LOAD_DATABASE_URL` es la misma base que `DATABASE_URL`. | [docker-compose.carga.yml](docker-compose.carga.yml), [app/load/database.py](app/load/database.py) |
| Generador | Arma los mensajes como los escribe un tendero ("salieron diez atun, 3 cocas cola litro y 2 jabon de ropa"), cada uno con su resultado esperado. La mezcla es 61% ventas, 16% consultas, 10% pedido, 8% conversación y 5% ventas difíciles (sin cantidad, productos que no existen, huevos sueltos). Es determinista: la misma semilla da los mismos mensajes. Se pueden mezclar mensajes reales (`--reales`). | [app/load/generator.py](app/load/generator.py) |
| Lista de pendientes | Una tabla con cada mensaje y su estado (pendiente, procesando, éxito, fallo, error). Si el programa se cae, al volver sigue donde iba, sin repetir ni perder mensajes. | [app/load/models.py](app/load/models.py) |
| Trabajador | Procesa un mensaje a la vez, a ritmo fijo (con `--horas 12.5`, 1.500 mensajes quedan a uno cada 30 s). Si la pasarela falla (429, 5xx, tiempo agotado, sin red), reintenta 2 veces más; con 5 mensajes seguidos fallando, pausa 5 minutos. La venta y su resultado se guardan en una sola transacción: una caída nunca registra una venta dos veces. Deja una señal de vida cada 5 minutos y Windows no se suspende mientras corre. | [app/load/worker.py](app/load/worker.py), [app/load/runner.py](app/load/runner.py) |
| Bitácora | Cada mensaje con su resultado, intentos, tokens, costo y error, más cada llamada a Grok (también las de reintentos). Los fallos se clasifican: errores de pasarela, red, tiempo o código; y fallos del agente (alucinó un resumen, ventas o cambios equivocados, herramienta no usada o prohibida, respuesta sin el dato). | [app/load/logbook.py](app/load/logbook.py) |

### Comandos

```bash
docker compose -f docker-compose.carga.yml up -d     # 1. base de carga (una vez)
python -m scripts.load_db setup                      # 2. tablas, catálogo y tiendas en esa base
python -m scripts.load_generate                      # 3. 1.500 mensajes en carga/unidades.jsonl

python -m scripts.load_run crear  --corrida grande --unidades carga/unidades.jsonl
python -m scripts.load_run correr --corrida grande --horas 12.5   # si se corta, el mismo comando sigue
python -m scripts.load_run estado --corrida grande                # cómo va
python -m scripts.load_report     --corrida grande                # bitácora completa
```

`crear` hace que cada tienda "cuente" su estante antes de empezar (300 unidades por producto, `--conteo-inicial`), para que las ventas de 12 horas no dejen el inventario en negativo.

En el bot, "hazme el pedido" pregunta con botones (✍️ Armarlo yo / 📋 Ver pedido sugerido). En la carga nadie toca botones: el tendero simulado escoge el pedido sugerido y sus cambios ("quita el arroz") caen sobre ese borrador.

La bitácora queda en `carga/resultados/<corrida>/`:

- `resumen.md` y `resumen.json`: resultado general y por tipo de mensaje, consumo (llamadas, tokens, % en caché, costo total, por unidad y por unidad resuelta, en USD y pesos), latencia, fallos clasificados con ejemplos, continuidad (cortes de 10 minutos o más y el mayor hueco entre señales de vida) y una fila por hora.
- `unidades.csv` y `unidades.jsonl`: cada mensaje con lo que hizo el agente.
- `llamadas.csv`: cada llamada a Grok, atada a su mensaje (`task_id = r<corrida>-<unidad>`).
- `por_hora.csv` y `senales.csv`.

### Estado de S4

- **Listo:** la unidad de trabajo, el generador (1.500 mensajes en [carga/unidades.jsonl](carga/unidades.jsonl) y un piloto de 50 con todos los tipos en [carga/piloto.jsonl](carga/piloto.jsonl)), la base aparte, el trabajador y la bitácora.
- **Probado contra Grok:** una corrida de 7 mensajes, uno de cada tipo, con 7 de 7 bien a US$0,0015 por mensaje. Se revisó en la base que la venta quedó confirmada, que no quedaron resúmenes abiertos, que cada llamada quedó en la bitácora y que hubo señales de vida.
- **Piloto de 50 mensajes (7 de noviembre):** primera corrida 44 de 50 (88%). Los 6 fallos llevaron a 4 arreglos: el texto exacto del tendero ("aceite litro") manda sobre el código que propone Grok; la cantidad citada tiene que ser un número ("litro" no lo es); el tamaño del producto ("Axion 400 g", "Jabón Rey x 3") no cuenta como cantidad; y con varios proveedores el agente encuentra el producto también por los nombres de los otros proveedores ("Postobón 1.5 litros"). Segunda corrida con los mismos 50: **49 de 50 (98%)**, US$0,00144 por mensaje. Sigue fallando «1 Jabón Rey x 3»: Grok toma el 3 como cantidad; ahora el bot no lo registra y pregunta, pero el caso cuenta como fallo.
- **Prueba de recuperación:** en el primer piloto se mató el proceso a la fuerza con 18 de 50 hechos. Con el mismo comando siguió en la unidad 19, sin repetir ni perder ninguna. La bitácora lo dice: "Arranques: 2 … 1 de ellas tras un corte brusco".
- **Falta:** la corrida de 12 horas.
- **Costo estimado:** con ~US$0,0015 por mensaje, 1.500 mensajes cuestan unos US$2,30.
- **Los mensajes son generados:** se marcan `generado`; los mensajes reales de tenderos entran con `origen: real` y la bitácora los cuenta aparte.

## Cálculos de inventario (sin IA)

En [app/inventory/engine.py](app/inventory/engine.py):

- **Stock actual** = último conteo + mercancía recibida − ventas desde ese conteo.
- **Venta diaria** = promedio de los últimos 28 días.
- **Días de cobertura** = stock actual ÷ venta diaria.
- **Varios proveedores:** un producto que venden varios proveedores de la tienda (emparejados por nombre y presentación en [app/catalog_merge.py](app/catalog_merge.py)) es un solo artículo con un solo stock: ventas, conteos, recibos y pedidos de cualquiera de ellos se cuentan juntos. Se le pide a su proveedor preferido (el más barato con existencias, o el que escoja el tendero), y cada proveedor tiene su propio plan de pedido.
- **Entregas:** con cada proveedor el tendero escoge sus días de entrega. Un pedido alcanza una entrega si se envía antes de las 6 p. m., con los días de anticipación de ese proveedor (mínimo 1).
- **Horizonte** = días desde hoy hasta la entrega siguiente a la próxima entrega alcanzable (lo que se pide hoy debe durar hasta que llegue el pedido siguiente).
- **Stock de seguridad** = 1,65 × desviación estándar de la venta diaria × √horizonte.
- **Pedido** = venta diaria × horizonte + stock de seguridad − stock actual, redondeado hacia arriba a cajas completas.
- **ABC** por valor vendido: A hasta el 80 %, B hasta el 95 %, C el resto.
- **Riesgo de agotarse:** días de cobertura menores que el horizonte.
- **Estancado:** sin ventas en 21 días teniendo stock, o más de 45 días de cobertura.

## Tecnologías

Python 3.12 · FastAPI · python-telegram-bot · PostgreSQL (Neon) · SQLAlchemy + Alembic · Grok (xAI) · rapidfuzz · Jinja2 + Chart.js · pytest · Docker (solo la base de la semana de carga)

## Cómo correrlo

Requisitos: Python 3.12, una base PostgreSQL (por ejemplo Neon), un bot creado en @BotFather y la API key del reto.

```bash
pip install -r requirements.txt
cp .env.example .env        # llena TELEGRAM_BOT_TOKEN, DATABASE_URL, GROK_API_KEY, SESSION_SECRET
alembic upgrade head        # crea las tablas (o las pone al día)
python -m scripts.seed      # datos base: 3 proveedores, 3 tiendas sin vincular (sin ventas; --demo inventa 60 días)
uvicorn app.main:app --reload
```

Con `BOT_MODE=polling` el bot funciona desde tu computador, sin desplegar nada.

- **Bot:** abre tu bot en Telegram, escribe `/start` y toca 📱 Compartir mi número. Si tu número no es de ninguna tienda, registras la tuya ahí mismo y escoges proveedores con `/proveedores`; el proveedor te acepta en su panel (Solicitudes). También puedes registrar tu celular en una tienda que ya existe (Tiendas → una tienda → Acceso al bot).
- **Proveedores de la demo:** el seed crea *Distribuidora El Barrio* (con la cuenta del panel de `PANEL_EMAIL`), *Postobón Santander* y *Mayorista La Economía*, que venden algunos de los mismos productos a otro precio. La tienda *Mini Market El Parque* trabaja con los tres. Para entrar al panel de los otros dos, créales una cuenta desde `/admin`.
- **Datos reales:** el seed no inventa ventas, conteos ni pedidos. Todo lo que muestra el panel llega desde Telegram, y el panel se actualiza solo (indicador "En vivo"). Volver a correr el seed borra los datos del distribuidor, también los celulares registrados.
- **Panel:** entra a http://localhost:8000/login con `PANEL_EMAIL` y `PANEL_PASSWORD` del `.env`. Para cambiarlos sin borrar datos: `python -m scripts.panel_user tu@correo.com` (pide la contraseña oculta). El login no deja entrar con contraseñas de prueba como `demo1234`.
- **Administración:** crea tu cuenta con `python -m scripts.admin_user tu@correo.com` y entra a http://localhost:8000/login (el mismo login te lleva a `/admin`).
- **Pruebas:** `python -m pytest`
- **Evaluación:** `python -m scripts.run_eval` (corre contra Grok, cuesta unos US$0,06)
- **Semana de carga:** ver [Semana de carga (S4)](#semana-de-carga-s4)

### Variables de entorno

| Variable | Para qué |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot de @BotFather |
| `BOT_MODE` | `polling` (local) o `webhook` (servidor) |
| `PUBLIC_BASE_URL`, `TELEGRAM_WEBHOOK_SECRET` | Solo en modo webhook |
| `DATABASE_URL` | Conexión a PostgreSQL |
| `GROK_API_KEY` | API key del reto |
| `GROK_BASE_URL` | Por defecto `https://api.reto.pltk.mx/v1` |
| `GROK_MODEL` | Por defecto `grok-4.20-0309-non-reasoning` |
| `AGENT_ENABLED` | `true` (por defecto): agente con herramientas. `false`: extracción directa sin herramientas |
| `LOAD_DATABASE_URL` | Base de la semana de carga (S4). Por defecto, el PostgreSQL local de `docker-compose.carga.yml`. Nunca la misma que `DATABASE_URL` |
| `SESSION_SECRET` | Firma de la sesión del panel |
| `PANEL_EMAIL`, `PANEL_PASSWORD` | Cuenta del panel que crea el seed (contraseña de 12 caracteres o más) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Correo de respaldo para tenderos que no abren el bot (gratis con Gmail y una contraseña de aplicación). Vacíos = no se mandan correos. |

Las claves van solo en `.env`, que está en `.gitignore`. Nunca se suben al repositorio.

## Reglas que cumple

- La IA entiende al tendero y escoge la herramienta; ningún cálculo de inventario lo hace la IA.
- Toda venta, conteo y pedido se confirma con botones antes de guardarse o enviarse.
- Antes de usar el bot, el tendero acepta el tratamiento de sus datos personales (Ley 1581 de 2012).
- Cada tienda ve solo sus datos, y cada proveedor ve de cada tienda solo sus productos y sus pedidos.
- Las fotos y los audios se procesan y se descartan; no se guardan.

## Estado

- **Semana 1, flujo de punta a punta:** listo. El agente recibe voz, texto o foto, llama a Grok, procesa la respuesta y entrega un resultado (resumen para confirmar, inventario, alertas y pedido).
- **Semana 2, Grok por la pasarela y herramientas:** listo. Agente con tool calling (hoy 8 herramientas).
- **Semana 3, evaluación y costo:** set propio de 54 casos con resultados, bitácora de consumo y costo por tarea resuelta. Faltan los casos con audios y fotos reales.
- **Semana 4, carga de 12 horas:** en curso. Están listos el generador de 1.500 mensajes, la base aparte, el trabajador desatendido y la bitácora, y se probaron con corridas cortas contra Grok (también en una tienda con tres proveedores). Faltan la prueba piloto y la corrida de 12 horas.
- **Varios proveedores por tienda:** listo. Registro de la tienda en Telegram, `/proveedores` (escoger, enviar solicitudes, avisos de aceptación o rechazo), catálogo junto con precios comparados y proveedor preferido, un pedido por proveedor, y el panel de cada proveedor con solo sus datos. La evaluación sigue en 47/48.

**Pendiente:**

- Prueba piloto, prueba de recuperación y corrida de 12 horas de la semana de carga.
- Casos de evaluación con audios, fotos y mensajes reales de tenderos.
- Unidad de venta por producto en el catálogo (para *"vendí 10 huevos"*).
- Despliegue con Docker en Render y UptimeRobot para que no se apague.
- Aviso al distribuidor cuando el tendero envía un pedido; hoy queda registrado en el panel.
