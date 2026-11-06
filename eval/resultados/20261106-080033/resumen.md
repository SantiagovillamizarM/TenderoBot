# Evaluación de TenderoBot

- Fecha: 2026-11-06T08:00:33
- Modelo: `grok-4.20-0309-non-reasoning` por la pasarela del reto
- Casos: 40 ejecutados, 6 omitidos (sin archivo)
- Origen de los casos ejecutados: 40 redactado

## Resultado: 37 de 40 (92%)

| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |
|---|---|---|---|---|---|
| consulta | 6 | 6 | 100% | US$0.00127 | 2.4 |
| consulta_dificil | 1 | 1 | 100% | US$0.00208 | 1.8 |
| conversacion | 2 | 2 | 100% | US$0.00046 | 1.1 |
| fuera_de_tema | 1 | 1 | 100% | US$0.00047 | 1.4 |
| pedido | 8 | 8 | 100% | US$0.00173 | 2.9 |
| pedido_dificil | 2 | 2 | 100% | US$0.00205 | 2.2 |
| seguridad | 2 | 2 | 100% | US$0.00074 | 2.9 |
| ventas_dificil | 6 | 4 | 67% | US$0.00124 | 2.6 |
| ventas_texto | 12 | 11 | 92% | US$0.00171 | 3.1 |

## Costo

Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM 4.000 (`--trm`).

| Medida | USD | COP |
|---|---|---|
| Total de la corrida | US$0.05856 | $234,2 |
| Por tarea (promedio) | US$0.00146 | $5,9 |
| **Por tarea resuelta** (total ÷ casos que pasaron) | US$0.00158 | $6,3 |
| Por tienda al mes (20 mensajes al día) | US$0.87841 | $3.513,6 |

Promedio por tarea: 1.9 llamadas, 2552 tokens de entrada (1776 en caché) y 56 de salida. Latencia: p50 2.5 s, p95 4.4 s.

## Fallos (3)

| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |
|---|---|---|---|---|
| V12 | ventas_texto | hoy salió harto: 4 cocas de litro, 6 pony, 2 cubetas de huevo, 3 panes y 5 aguas cristal | ventas: esperaba {'GAS-COCA-1L': 4, 'MAL-PONY-330': 6, 'HUE-AAA-30': 2, 'PAN-TAJA-UND': 3, 'AGU-CRIS-600': 5}, quedó {'MAL-PONY-330': 6, 'HUE-AAA-30': 2, 'PAN-TAJA-UND': 3, 'AGU-CRIS-600': 5} | Revisa el resumen de ventas (4 cocas no reconocidas, las demás sí) y pulsa ✅ Confirmar. ¿Qué más? |
| V15 | ventas_dificil | hoy vendí media docena de huevos sueltos | ventas: esperaba ninguna, quedó {'HUE-AAA-30': 6} | Revisé: vendiste 6 huevos AAA. Revisa el resumen y pulsa ✅ Confirmar. |
| V17 | ventas_dificil | vendí coca litro | ventas: esperaba ninguna, quedó {'GAS-COCA-1L': 1}; la respuesta no dice '?' | Vendí 1 Coca-Cola 1 litro. Revisa el resumen y pulsa ✅ Confirmar. |

## Omitidos

- A01: falta el archivo audio_01.ogg
- A02: falta el archivo audio_02.ogg
- A03: falta el archivo audio_03.ogg
- F01: falta el archivo foto_01.jpg
- F02: falta el archivo foto_02.jpg
- F03: falta el archivo foto_03.jpg
