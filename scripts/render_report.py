"""Genera el PDF de un reporte técnico desde su HTML, con encabezado y 'Página X de Y'.

    python -m scripts.render_report docs/reportes/avance3.html "TenderoBot - Avance 3 - Reporte tecnico.pdf"

Usa Chrome sin ventana (protocolo DevTools). Otra ruta de Chrome: variable CHROME_PATH.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9333
STYLE = "font-family: Segoe UI, Arial, sans-serif; font-size: 8px; color: #8b867d; width: 100%; padding: 0 18mm;"


async def render(html: Path, pdf: Path) -> None:
    title = re.search(r"<title>(.*?)</title>", html.read_text(encoding="utf-8"), re.S).group(1).strip()
    profile = tempfile.mkdtemp(prefix="chrome-pdf-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--no-first-run", "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json").json()
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("Chrome no arrancó")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            counter = 0

            async def call(method, **params):
                nonlocal counter
                counter += 1
                await ws.send(json.dumps({"id": counter, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == counter:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg.get("result", {})

            await call("Page.enable")
            await call("Page.navigate", url=html.resolve().as_uri())
            await asyncio.sleep(1.5)
            result = await call(
                "Page.printToPDF",
                printBackground=True,
                preferCSSPageSize=True,
                displayHeaderFooter=True,
                headerTemplate=f'<div style="{STYLE}">{title}</div>',
                footerTemplate=f'<div style="{STYLE} text-align: right;">Página <span class="pageNumber"></span> de '
                '<span class="totalPages"></span></div>',
            )
            pdf.write_bytes(base64.b64decode(result["data"]))
    finally:
        proc.terminate()


if __name__ == "__main__":
    asyncio.run(render(Path(sys.argv[1]), Path(sys.argv[2])))
    print("ok", sys.argv[2])
