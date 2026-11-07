# Historial de corridas

Todas contra `grok-4.20-0309-non-reasoning` por la pasarela del reto, con la misma tienda fija (`eval/tienda.json`). El costo sale de la pasarela (`cost_in_usd_ticks`) en cada respuesta.

| # | Carpeta | Cambio antes de correr | Pasaron | Costo por tarea resuelta | Tokens de entrada (en caché) | p50 / p95 |
|---|---|---|---|---|---|---|
| 1 | `20261106-080033` | Línea base (40 casos) | 37/40 (92%) | US$0,00158 | 2.552 (1.776) | 2,5 s / 4,4 s |
| 2 | `20261106-081055` | Búsqueda con plurales + reglas en las instrucciones + 8 casos de control | 41/48 (85%) | US$0,00209 | 3.014 (2.027) | 2,1 s / 4,1 s |
| 3 | `20261106-081222` | Reglas más concretas, prohibido hablar de un resumen que no existe | 44/48 (92%) | US$0,00220 | 2.906 (1.669) | 2,3 s / 3,8 s |
| 4 | `20261106-081412` | El código exige la cita de la cantidad (`texto_cantidad`) | 45/48 (94%) | US$0,00205 | 3.018 (1.904) | 2,2 s / 3,6 s |
| 5 | `20261106-081523` | El código verifica que la cita esté en el mensaje del tendero | **47/48 (98%)** | **US$0,00119** | 3.020 (2.635) | 2,1 s / 2,9 s |
| 5b | `20261106-081619` | Repetición, sin cambios | 47/48 (98%) | US$0,00120 | 3.020 (2.621) | 2,3 s / 2,9 s |
| 5c | `20261106-081646` | Repetición, sin cambios | 47/48 (98%) | US$0,00123 | 3.020 (2.600) | 2,1 s / 3,0 s |
| 6 | `20261107-114239` | Varios proveedores por tienda: dos líneas nuevas en las instrucciones (comparar y escoger proveedor) | 47/48 (98%) | US$0,00143 | 3.097 (2.501) | 1,5 s / 2,5 s |

## Qué pasó en cada corrida

**1. Línea base.** Fallaron 3 de 40:
- V12, "4 cocas de litro, …": **error nuestro**. La búsqueda difusa no reconocía el plural "cocas de litro"; se parecía más a "Leche Alquería 1 litro" y quedaba empatada.
- V15, "media docena de huevos sueltos": Grok registró 6 cubetas de 30.
- V17, "vendí coca litro": Grok asumió 1 sin preguntar.

**2. Regresión.** Se corrigió la búsqueda: ahora compara también la frase sin plurales ni palabras de relleno. V12 pasó. Además se agregaron dos reglas a las instrucciones (preguntar si falta la cantidad, no convertir unidades sueltas en paquetes). V15 y V17 pasaron, pero la regla de los paquetes llevó a Grok a revisar cada producto con `buscar_producto` antes de registrar. En V05, V11 y V19 **nunca creó el borrador y aun así le dijo al tendero "Revisa el resumen y pulsa ✅ Confirmar"**, un resumen que no existía. Es el fallo más grave encontrado. También P08 ("súbele 12 cocas personales") pasó de unidades a cajas, aunque en la corrida 1 había pasado.

Antes de esta corrida se agregaron 8 casos de control (V19 a V26) con otra redacción, que no se usaron para ajustar el agente.

**3.** Se le dijo a Grok que `registrar_ventas` ya busca los productos sola, se le prohibió hablar de un resumen que no creó y se aclaró la unidad. La alucinación y P08 quedaron corregidos. Pero las reglas de cantidad y huevos volvieron a fallar (V17, V22, V23, V26). Con solo instrucciones, arreglar una regla soltaba otra.

**4. El código decide.** `registrar_ventas` pide `texto_cantidad`, las palabras exactas con que el tendero dijo la cantidad. Si viene vacía, o si habla de unidades sueltas o docenas de un producto de paquete, esa línea no se registra y la herramienta le pide a Grok que pregunte. V26 pasó, pero en V17 y V22 Grok **inventó la cita**: escribió `texto_cantidad: "una"` cuando el tendero solo dijo "vendí pony".

**5. La cita se verifica.** La herramienta comprueba que `texto_cantidad` aparezca en el mensaje del tendero, sin importar tildes ni mayúsculas. Pasaron V17 y V22. Repetida tres veces, da el mismo resultado.

El costo por tarea bajó en la corrida 5 por dos razones: Grok dejó de hacer búsquedas de más y las instrucciones, ya estables, quedaron más tiempo en la caché de la pasarela (87% de los tokens de entrada en caché, contra 63% en la corrida 4). El número de llamadas por tarea no cambió (1,9).

**6. Varios proveedores.** Se agregaron al agente las herramientas `comparar_proveedores` y `preferir_proveedor` y dos líneas a sus instrucciones. La evaluación (sobre la tienda fija de un proveedor) sigue en 47/48. El costo por tarea resuelta subió un poco: las instrucciones son más largas y esta corrida tuvo menos tokens en caché.

## Fallos que quedan

- **V23, "vendí 10 huevos":** registra 10 cubetas (300 huevos). La regla del código solo detecta "suelto" o "docena", y la de las instrucciones no basta. El tendero lo ve en el resumen antes de confirmar, pero el error es real. Arreglarlo bien exige que el catálogo diga cuál es la unidad de venta de cada producto.
- **La regla de los huevos en las instrucciones nombra un producto concreto.** La versión general (cualquier producto de paquete) fue la que causó la regresión de la corrida 2. Lo que sí es general es la regla del código: cualquier presentación "x N" con "suelto" o "docena".
- **El calificador es estricto con las preguntas:** exige "?". En la corrida 2, V26 contó como fallo aunque Grok pidió las cantidades ("Dime las cantidades exactas…") sin signo de interrogación. No se cambió lo esperado después de ver el resultado.
- **Los casos son redactados:** los 48 casos ejecutados los escribió el equipo con el lenguaje de tenderos. Faltan los audios y fotos reales (A01–A03, F01–F03) y casos tomados de mensajes reales.
