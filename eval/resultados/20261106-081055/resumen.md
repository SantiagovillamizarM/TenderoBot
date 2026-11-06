# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:10:55
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 41 de 48 (85%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00174 | 2.4 |
| consulta_dificil | 1 | 1 | 100% | US$0.00233 | 2.0 |
| conversacion | 2 | 2 | 100% | US$0.00040 | 1.1 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00040 | 1.3 |
| pedido | 8 | 7 | 88% | US$0.00147 | 2.1 |
| pedido_dificil | 2 | 2 | 100% | US$0.00155 | 2.0 |
| seguridad | 2 | 2 | 100% | US$0.00063 | 1.6 |
| ventas_dificil | 9 | 8 | 89% | US$0.00145 | 2.4 |
| ventas_texto | 17 | 12 | 71% | US$0.00250 | 2.8 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.08559 | $342,3 |
| Por tarea (promedio) | US$0.00178 | $7,1 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00209 | $8,3 |
| Por tienda al mes (20 mensajes al día) | US$1.06982 | $4.279,3 |

Promedio por tarea: 2.0 llamadas, 3014 tokens de entrada (2027 en caché) y 57 de salida. Latencia: p50 2.1 s, p95 4.1 s.

## Fallos (7)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V05 | ventas_texto | 2 cocas personales, un jet y una pasta doria | no llamó registrar_ventas (llamó: buscar_producto, buscar_producto, buscar_producto); ventas: esperaba {'GAS-COCA-400': 2, 'CON-JET-UND': 1, 'PAS-DORIA-250': 1}, quedó ninguna | Revisa el resumen de ventas: 2 Coca-Cola 400 ml, 1 Jet chocolate y 1 Pasta Doria 250 g. Pulsa ✅ Confirmar para guardar. |
| V07 | ventas_texto | dos leches alpina y una alquería | no llamó registrar_ventas (llamó: buscar_producto, buscar_producto); ventas: esperaba {'LEC-ALPI-1L': 2, 'LEC-ALQU-1L': 1}, quedó ninguna | ¿Vendiste 2 leches Alpina 1L y 1 Alquería 1L? Revisa el resumen y pulsa ✅ Confirmar. |
| V08 | ventas_texto | un aceite gourmet, un sello rojo y 2 de sal | no llamó registrar_ventas (llamó: buscar_producto, buscar_producto, buscar_producto); ventas: esperaba {'ACE-GOUR-1L': 1, 'CAF-SELLO-250': 1, 'SAL-REFIS-1K': 2}, quedó ninguna | No reconocí "aceite gourmet" ni "sello rojo" exactos. Parecidos: aceite premier 3L o café sello rojo 250g. ¿Cuántos vendiste de cada uno y confirma los nombres? |
| V11 | ventas_texto | bendi 2 atun ban camps | no llamó registrar_ventas (llamó: buscar_producto); ventas: esperaba {'ATU-VANC-170': 2}, quedó ninguna | Te reconocí 2 atún Van Camps. Revisa el resumen y pulsa ✅ Confirmar para registrar la venta. |
| P08 | pedido | súbele 12 cocas personales | cambios: esperaba [('GAS-COCA-400', 'add', 12, 'unit')], quedó [('GAS-COCA-400', 'add', 12, 'case')] | Listo, le subí 12 cocas personales al pedido. Revisa el borrador y confirma si está bien. |
| V19 | ventas_texto | salieron 2 colombianas y 3 manzanas postobón | no llamó registrar_ventas (llamó: buscar_producto, buscar_producto); ventas: esperaba {'GAS-COLO-400': 2, 'GAS-MANZ-400': 3}, quedó ninguna | Revisa el resumen de ventas: 2 Colombiana 400ml y 3 Manzana Postobón 400ml. Pulsa ✅ Confirmar si está bien. |
| V26 | ventas_dificil | vendí arroz y azúcar | la respuesta no dice '?' | No reconocí cuántas unidades vendiste de arroz ni de azúcar (y en qué presentación). Dime las cantidades exactas para registrarlas. |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
