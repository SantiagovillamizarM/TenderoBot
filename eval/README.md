# Evaluación propia

Casos para medir qué tan bien funciona el agente y cuánto cuesta cada tarea. Corren contra Grok por la pasarela del reto.

```
python -m scripts.run_eval                     # todos
python -m scripts.run_eval --solo V12,V15      # algunos
python -m scripts.run_eval --categoria pedido  # una categoría
```

Los resultados quedan en `resultados/<fecha>/`:
- `resumen.md`: acierto por categoría, costo por tarea resuelta y lista de fallos.
- `resultados.jsonl`: qué hizo el agente en cada caso.

## Archivos

- `casos.jsonl`: un caso por renglón.
- `tienda.json`: la tienda fija sobre la que corre el agente (catálogo, stock, alertas y pedido sugerido). El agente usa sus herramientas de verdad, pero sobre estos datos y sin base de datos, para que cada corrida se pueda repetir.
- `media/`: audios y fotos reales de los casos `A..` y `F..`. Un caso cuyo archivo no está queda como *omitido* y no cuenta.

## Agregar un caso

```json
{"id": "V19", "categoria": "ventas_texto", "origen": "real",
 "entrada": {"texto": "me compraron 3 pony"},
 "esperado": {"herramientas": ["registrar_ventas"], "ventas": [{"sku": "MAL-PONY-330", "cantidad": 3}]},
 "nota": "Mensaje real de Doña Marta, 5/11"}
```

- `origen`: `real` si salió de un tendero de verdad, `redactado` si lo escribimos nosotros.
- `entrada`: `texto`, `audio` (ruta dentro de `eval/`) o `foto` (más `nota` opcional, como el texto que acompaña la foto).
- `contexto`: `{"pedido_abierto": true}` si el tendero está revisando un pedido.

Qué se puede esperar (todo opcional):

| Campo | Significa |
|---|---|
| `herramientas` | Deben llamarse todas. |
| `herramientas_alguna` | Debe llamarse al menos una. |
| `no_debe_llamar` | No debe llamarse ninguna. |
| `ventas` | Borrador de ventas exacto `[{sku, cantidad}]`. `[]` = no debe quedar ninguna venta. |
| `cambios` | Cambios al pedido exactos `[{sku, accion, cantidad, unidad?}]`. |
| `conteo` | Conteo de la foto `[{sku, cantidad, tolerancia?}]`. |
| `respuesta_contiene` | Textos que debe decir la respuesta. `"a\|b"` = cualquiera. Sin importar tildes ni mayúsculas. |
| `respuesta_no_contiene` | Textos que no debe decir. |

Los casos `A01`–`A03` y `F01`–`F03` ya están en `casos.jsonl`, esperando su archivo real: hay que poner el audio o la foto en `media/` y llenar lo esperado (`ventas`, `cambios` o `conteo`) según lo que diga el audio o se vea en la foto.
