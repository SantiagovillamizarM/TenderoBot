# Evaluación de TenderoBot

- Fecha: 2026-11-07T11:42:39
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 47 de 48 (98%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00094 | 1.4 |
| consulta_dificil | 1 | 1 | 100% | US$0.00105 | 1.6 |
| conversacion | 2 | 2 | 100% | US$0.00117 | 0.7 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00044 | 0.8 |
| pedido | 8 | 8 | 100% | US$0.00129 | 1.5 |
| pedido_dificil | 2 | 2 | 100% | US$0.00169 | 1.6 |
| seguridad | 2 | 2 | 100% | US$0.00068 | 1.1 |
| ventas_dificil | 9 | 8 | 89% | US$0.00146 | 1.8 |
| ventas_texto | 17 | 17 | 100% | US$0.00173 | 1.8 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.06699 | $268,0 |
| Por tarea (promedio) | US$0.00140 | $5,6 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00143 | $5,7 |
| Por tienda al mes (20 mensajes al día) | US$0.83735 | $3.349,4 |

Promedio por tarea: 1.9 llamadas, 3097 tokens de entrada (2501 en caché) y 60 de salida. Latencia: p50 1.5 s, p95 2.5 s.

## Fallos (1)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V23 | ventas_dificil | vendí 10 huevos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 10} | Revisa el resumen de las 10 huevos y pulsa ✅ Confirmar. |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
