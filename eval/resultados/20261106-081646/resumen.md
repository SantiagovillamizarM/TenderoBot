# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:16:46
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 47 de 48 (98%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00145 | 1.8 |
| consulta_dificil | 1 | 1 | 100% | US$0.00096 | 2.0 |
| conversacion | 2 | 2 | 100% | US$0.00051 | 1.3 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00052 | 1.3 |
| pedido | 8 | 8 | 100% | US$0.00114 | 1.9 |
| pedido_dificil | 2 | 2 | 100% | US$0.00105 | 2.2 |
| seguridad | 2 | 2 | 100% | US$0.00067 | 1.5 |
| ventas_dificil | 9 | 8 | 89% | US$0.00123 | 2.0 |
| ventas_texto | 17 | 17 | 100% | US$0.00134 | 2.5 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.05764 | $230,6 |
| Por tarea (promedio) | US$0.00120 | $4,8 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00123 | $4,9 |
| Por tienda al mes (20 mensajes al día) | US$0.72048 | $2.881,9 |

Promedio por tarea: 1.9 llamadas, 3020 tokens de entrada (2600 en caché) y 62 de salida. Latencia: p50 2.1 s, p95 3.0 s.

## Fallos (1)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V23 | ventas_dificil | vendí 10 huevos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 10} | Revisa el resumen de 10 huevos y pulsa ✅ Confirmar. (Recuerda que los huevos van por cubeta de 30). |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
