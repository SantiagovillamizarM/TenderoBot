# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:14:12
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 45 de 48 (94%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00176 | 1.8 |
| consulta_dificil | 1 | 1 | 100% | US$0.00298 | 1.9 |
| conversacion | 2 | 2 | 100% | US$0.00111 | 1.1 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00053 | 1.2 |
| pedido | 8 | 8 | 100% | US$0.00191 | 2.0 |
| pedido_dificil | 2 | 2 | 100% | US$0.00266 | 2.3 |
| seguridad | 2 | 2 | 100% | US$0.00094 | 1.4 |
| ventas_dificil | 9 | 6 | 67% | US$0.00203 | 2.8 |
| ventas_texto | 17 | 17 | 100% | US$0.00208 | 2.6 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.09246 | $369,8 |
| Por tarea (promedio) | US$0.00193 | $7,7 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00205 | $8,2 |
| Por tienda al mes (20 mensajes al día) | US$1.15575 | $4.623,0 |

Promedio por tarea: 1.9 llamadas, 3018 tokens de entrada (1904 en caché) y 61 de salida. Latencia: p50 2.2 s, p95 3.5 s.

## Fallos (3)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V17 | ventas_dificil | vendí coca litro | ventas: esperaba ninguna, quedó {'GAS-COCA-1L': 1} | Revisa el resumen de la venta de 1 Coca-Cola 1 litro y pulsa ✅ Confirmar. ¿Qué más vendiste? |
| V22 | ventas_dificil | vendí pony | ventas: esperaba ninguna, quedó {'MAL-PONY-330': 1} | Revisa el resumen de la venta de 1 Pony Malta y pulsa ✅ Confirmar. ¿Algo más? |
| V23 | ventas_dificil | vendí 10 huevos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 10} | Revisa el resumen de ventas (10 huevos) y pulsa ✅ Confirmar. Los huevos son por cubeta de 30, ¿quieres registrarlo así o eran sueltos? |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
