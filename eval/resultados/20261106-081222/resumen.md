# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:12:22
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 48 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 48 redactado

## Resultado: 44 de 48 (92%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00228 | 2.1 |
| consulta_dificil | 1 | 1 | 100% | US$0.00243 | 2.3 |
| conversacion | 2 | 2 | 100% | US$0.00044 | 1.3 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00165 | 1.4 |
| pedido | 8 | 8 | 100% | US$0.00208 | 2.6 |
| pedido_dificil | 2 | 2 | 100% | US$0.00231 | 2.3 |
| seguridad | 2 | 2 | 100% | US$0.00131 | 1.8 |
| ventas_dificil | 9 | 5 | 56% | US$0.00180 | 2.4 |
| ventas_texto | 17 | 17 | 100% | US$0.00223 | 2.9 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.09664 | $386,5 |
| Por tarea (promedio) | US$0.00201 | $8,1 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00220 | $8,8 |
| Por tienda al mes (20 mensajes al día) | US$1.20795 | $4.831,8 |

Promedio por tarea: 1.9 llamadas, 2906 tokens de entrada (1669 en caché) y 53 de salida. Latencia: p50 2.3 s, p95 3.8 s.

## Fallos (4)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V17 | ventas_dificil | vendí coca litro | ventas: esperaba ninguna, quedó {'GAS-COCA-1L': 1} | Revisa el resumen de la venta de 1 Coca-Cola 1 litro y pulsa ✅ Confirmar. ¿Qué más vendiste? |
| V22 | ventas_dificil | vendí pony | ventas: esperaba ninguna, quedó {'MAL-PONY-330': 1}; la respuesta no dice '?' | Revisa el resumen de la venta de 1 Pony Malta y confirma. |
| V23 | ventas_dificil | vendí 10 huevos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 10} | Revisa el resumen de ventas (10 huevos) y pulsa ✅ Confirmar. Los huevos son por cubeta de 30, ¿estás seguro de las 10? |
| V26 | ventas_dificil | vendí arroz y azúcar | ventas: esperaba ninguna, quedó {'ARR-ROSA-500': 1, 'AZU-MANU-1K': 1}; la respuesta no dice '?' | Revisa el resumen de ventas (arroz y azúcar) y confirma con el botón. |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
