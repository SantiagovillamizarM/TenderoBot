# Corrida de carga «piloto2»

- Estado: **terminada** · de 2026-11-08 03:03 a 2026-11-08 03:09 UTC (**0,09 h**)
- Unidades: **50** terminadas de 50 (0 pendientes) · 527,6 por hora
- Origen: 50 generado
- Ventas confirmadas (movieron el inventario): 27

## Resultado: 49 de 50 bien (98,0%)

Fallos (el agente respondió mal): 1 · errores (no se completó): 0 · reintentos: 0

| Tipo | Unidades | Éxito | Fallo | Error | Costo por unidad | p50 | p95 |
|---|---|---|---|---|---|---|---|
| consulta | 5 | 100,0% | 0 | 0 | US$0,00168 | 3,2 s | 6,9 s |
| conversacion | 6 | 100,0% | 0 | 0 | US$0,00089 | 2,1 s | 19,2 s |
| pedido | 6 | 100,0% | 0 | 0 | US$0,00119 | 3,4 s | 4,6 s |
| venta | 28 | 96,4% | 1 | 0 | US$0,00159 | 2,9 s | 5,8 s |
| venta_dificil | 5 | 100,0% | 0 | 0 | US$0,00127 | 2,8 s | 3,4 s |

## Consumo

| | Valor |
|---|---|
| Llamadas a Grok | 94 |
| Tokens de entrada (en caché) | 215.629 (196.480, 91%) |
| Tokens de salida | 3.433 |
| **Costo total** | **US$0,07181** ($229 COP) |
| Costo por unidad | US$0,00144 |
| Costo por unidad resuelta | US$0,00147 |
| Latencia p50 / p95 | 2,9 s / 6,9 s |

Costo reportado por la pasarela en cada respuesta, incluidos los reintentos. COP con TRM 3.182,99.

## Fallos y errores

| Categoría | Unidades |
|---|---|
| ventas_incorrectas | 1 |

**ventas_incorrectas**

- U00020: «hoy vendí 1 Jabón Rey x 3, 12 Jet chocolate y 1 jabon de loza» — faltó ASE-JABO-3 · respuesta: «Revisa el resumen de ventas (12 Jet chocolate y 1 Axion 400g) y pulsa ✅ Confirmar.  

Para el Jabón Rey x 3, ¿cuántos vendiste? (el "x 3" parece el tamaño, no la cantidad).»

## Continuidad

3 señales de vida; el mayor hueco entre dos señales fue de 5,1 min. Freno por errores de pasarela: 0 veces.
Arranques: 1 (corrida de un solo tirón, sin reinicios).
Sin cortes: ninguna pausa larga entre unidades.

## Por hora

| Hora | Desde (Colombia) | Unidades | Éxito | Errores | Costo | Caché | p95 |
|---|---|---|---|---|---|---|---|
| 0 | 2026-11-07 22:03 | 50 | 98% | 0 | US$0,07181 | 91% | 6,9 s |
