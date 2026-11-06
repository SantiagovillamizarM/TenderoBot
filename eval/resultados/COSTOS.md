# Costo por tarea

Corridas usadas: `20261106-081523`, `20261106-081619`, `20261106-081646` (configuración final, 3 repeticiones).
Costo reportado por la pasarela del reto en cada respuesta. Pesos con TRM $3.182,99 (3 de noviembre de 2026; cambiar con `--trm`).

**Costo por tarea resuelta** = todo lo gastado en ese tipo de tarea, también en los intentos que fallaron, dividido por las tareas que salieron bien.

| Tipo de tarea | Tareas | Acierto | Costo promedio | Costo máximo | Costo por tarea resuelta (USD) | (COP) | Llamadas | Tokens entrada (caché) / salida | Segundos |
|---|---|---|---|---|---|---|---|---|---|
| ventas | 78 | 96% | US$0,00128 | US$0,00270 | US$0,00133 | $4,23 | 2,0 | 3.150 (2.721) / 78 | 2,4 |
| pedido | 30 | 100% | US$0,00119 | US$0,00283 | US$0,00119 | $3,78 | 2,0 | 3.143 (2.726) / 49 | 2,0 |
| consulta | 21 | 100% | US$0,00117 | US$0,00241 | US$0,00117 | $3,71 | 2,0 | 3.186 (2.786) / 43 | 1,9 |
| conversacion | 15 | 100% | US$0,00068 | US$0,00186 | US$0,00068 | $2,17 | 1,2 | 1.863 (1.638) / 30 | 1,3 |
| foto | 3 | solo costo | US$0,00060 | US$0,00060 | US$0,00060 | $1,90 | 1,0 | 2.613 (2.560) / 7 | 1,7 |

## Proyección por tienda

Mezcla supuesta de mensajes por tienda al día (se reemplaza por la real cuando la bitácora `llm_usage` tenga uso de tiendas de verdad):

| Tipo | Mensajes al día |
|---|---|
| ventas | 12 |
| consulta | 3 |
| pedido | 2 |
| conversacion | 2 |
| foto | 1 |

| | USD | COP |
|---|---|---|
| Por tienda al día | US$0,02377 | $75,67 |
| **Por tienda al mes** (30 días) | **US$0,71322** | **$2.270** |
| 100 tiendas al mes | US$71,32 | $227.018 |
| 1.000 tiendas al mes | US$713,22 | $2.270.176 |

## Lo que no está en estos números

- **Transcripción de audio:** la pasarela no reporta su costo ni lo publica en `/models`. Cada mensaje de voz cuesta lo de su texto más una transcripción sin costear.
- **Foto:** costo medido con una imagen generada de 1280×960 (el tamaño de Telegram). El costo de la imagen depende de la resolución, no del contenido; la respuesta de una foto real con muchos productos trae más tokens de salida. El acierto con fotos se medirá con los casos F01–F03 reales.
- **Caché:** el costo bajo depende de que la pasarela tenga las instrucciones en caché (más del 80% de los tokens de entrada). Con poco tráfico la caché se pierde y cada tarea cuesta hasta unas 3 veces más: es la diferencia entre el costo promedio y el máximo de la tabla.
