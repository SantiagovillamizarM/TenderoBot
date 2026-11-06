# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:16:19
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 47 de 48 (98%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00096 | 1.9 |
| consulta_dificil | 1 | 1 | 100% | US$0.00095 | 1.9 |
| conversacion | 2 | 2 | 100% | US$0.00118 | 1.2 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00053 | 1.2 |
| pedido | 8 | 8 | 100% | US$0.00137 | 2.2 |
| pedido_dificil | 2 | 2 | 100% | US$0.00098 | 2.3 |
| seguridad | 2 | 2 | 100% | US$0.00060 | 1.3 |
| ventas_dificil | 9 | 8 | 89% | US$0.00125 | 2.1 |
| ventas_texto | 17 | 17 | 100% | US$0.00127 | 2.5 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.05653 | $226,1 |
| Por tarea (promedio) | US$0.00118 | $4,7 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00120 | $4,8 |
| Por tienda al mes (20 mensajes al día) | US$0.70664 | $2.826,6 |

Promedio por tarea: 1.9 llamadas, 3020 tokens de entrada (2621 en caché) y 62 de salida. Latencia: p50 2.3 s, p95 2.9 s.

## Fallos (1)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V23 | ventas_dificil | vendí 10 huevos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 10} | Revisa el resumen de las 10 huevos (por cubeta de 30) y pulsa ✅ Confirmar. ¿Cuántas cubetas vendiste en realidad? |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
