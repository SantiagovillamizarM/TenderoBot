# Corrida de carga «piloto»

- Estado: **terminada** · de 2026-11-08 02:47 a 2026-11-08 02:56 UTC (**0,14 h**)
- Unidades: **50** terminadas de 50 (0 pendientes) · 347,8 por hora
- Origen: 50 generado
- Ventas confirmadas (movieron el inventario): 24

## Resultado: 44 de 50 bien (88,0%)

Fallos (el agente respondió mal): 6 · errores (no se completó): 0 · reintentos: 0

| Tipo | Unidades | Éxito | Fallo | Error | Costo por unidad | p50 | p95 |
|---|---|---|---|---|---|---|---|
| consulta | 5 | 100,0% | 0 | 0 | US$0,00213 | 4,0 s | 4,6 s |
| conversacion | 6 | 100,0% | 0 | 0 | US$0,00125 | 1,9 s | 6,2 s |
| pedido | 6 | 100,0% | 0 | 0 | US$0,00188 | 3,3 s | 12,3 s |
| venta | 28 | 85,7% | 4 | 0 | US$0,00224 | 2,9 s | 7,9 s |
| venta_dificil | 5 | 60,0% | 2 | 0 | US$0,00123 | 2,1 s | 2,6 s |

## Consumo

| | Valor |
|---|---|
| Llamadas a Grok | 94 |
| Tokens de entrada (en caché) | 215.528 (171.264, 79%) |
| Tokens de salida | 3.454 |
| **Costo total** | **US$0,09822** ($313 COP) |
| Costo por unidad | US$0,00196 |
| Costo por unidad resuelta | US$0,00223 |
| Latencia p50 / p95 | 2,7 s / 7,9 s |

Costo reportado por la pasarela en cada respuesta, incluidos los reintentos. COP con TRM 3.182,99.

## Fallos y errores

| Categoría | Unidades |
|---|---|
| ventas_incorrectas | 6 |

**ventas_incorrectas**

- U00005: «se fueron dos aceite litro» — faltó ACE-GOUR-1L, sobró ACE-PREM-3L · respuesta: «Revisa el resumen de las 2 Aceite Premier 3 litros y pulsa ✅ Confirmar. ¿Algo más?»
- U00009: «se fue leche litro» — sobró LEC-ALQU-1L · respuesta: «Revisa el resumen de la venta de 1 Leche Alquería litro y confirma.»
- U00020: «hoy vendí 1 Jabón Rey x 3, 12 Jet chocolate y 1 jabon de loza» — ASE-JABO-3 1→3 · respuesta: «Revisa el resumen de ventas (3 Jabón Rey, 12 Jet y 1 Axion) y pulsa ✅ Confirmar. ¿Algo más?»
- U00024: «salieron un lavaloza, dos postobon y 6 Agua Brisa 600 ml» — faltó AGU-BRIS-600 · respuesta: «Revisa el resumen: vendiste 1 Axion 400g y 2 Postobón 1.5L. El Agua Brisa 600ml no se reconoció (parece Agua Brisa o Cristal). Confirma o dime cuál es.»
- U00030: «me compraron Axion 400 g» — sobró ASE-AXIO-400 · respuesta: «Revisa el resumen de la venta de Axion 400 g y confirma.»

## Continuidad

3 señales de vida; el mayor hueco entre dos señales fue de 4,4 min. Freno por errores de pasarela: 0 veces.
Arranques: 2 (la corrida se retomó 1 veces con el mismo comando; 1 de ellas tras un corte brusco, sin cierre ordenado). Ninguna unidad se repitió ni se perdió: las que quedaban a medias volvieron a la cola y su intento quedó contado.
Sin cortes: ninguna pausa larga entre unidades.

## Por hora

| Hora | Desde (Colombia) | Unidades | Éxito | Errores | Costo | Caché | p95 |
|---|---|---|---|---|---|---|---|
| 0 | 2026-11-07 21:47 | 50 | 88% | 0 | US$0,09822 | 79% | 7,9 s |
