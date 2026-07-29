#!/usr/bin/env python3
"""fetch_pvpc.py — descarga los precios PVPC (hoy + manana si publicado)
y escribe precio-luz/data.json.

v2: dos fuentes con reintentos.
- Primaria: apidatos.ree.es (REData, JSON limpio)
- Respaldo: api.esios.ree.es/archives/70/download_json (mismo PVPC oficial)

Uso local:  python scripts/fetch_pvpc.py
En Actions: cron diario (ver .github/workflows/pvpc.yml)
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
OUT = Path(__file__).resolve().parent.parent / "precio-luz" / "data.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; cuanteo.com PVPC; hola@cuanteo.com)",
      "Accept": "application/json"}

URL_REDATA = ("https://apidatos.ree.es/es/datos/mercados/"
              "precios-mercados-tiempo-real?start_date={d0}T00:00"
              "&end_date={d1}T23:59&time_trunc=hour&geo_ids=8741")
URL_ESIOS = ("https://api.esios.ree.es/archives/70/download_json"
             "?locale=es&date={d}")


def get_json(url: str, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  intento {i + 1}/{tries} fallo: {type(e).__name__}: {e}")
            time.sleep(4 * (i + 1))
    raise last


def parse_redata(payload: dict) -> dict[str, list]:
    series = None
    for inc in payload.get("included", []):
        if inc.get("type") == "PVPC" or str(inc.get("id")) == "1001":
            series = inc["attributes"]["values"]
            break
    if not series:
        raise RuntimeError("Serie PVPC no encontrada (REData)")
    days: dict[str, list] = {}
    for v in series:
        fecha = v["datetime"][:10]
        hora = int(v["datetime"][11:13])
        days.setdefault(fecha, [None] * 24)
        days[fecha][hora] = round(float(v["value"]) / 1000, 5)
    return _complete(days)


def parse_esios(payload: dict) -> dict[str, list]:
    rows = payload.get("PVPC") or []
    days: dict[str, list] = {}
    for r in rows:
        d, m, y = r["Dia"].split("/")
        fecha = f"{y}-{m}-{d}"
        hora = int(r["Hora"].split("-")[0])
        pcb = float(str(r.get("PCB") or r.get("GEN")).replace(".", "")
                    .replace(",", "."))
        days.setdefault(fecha, [None] * 24)
        days[fecha][hora] = round(pcb / 1000, 5)
    return _complete(days)


def _complete(days: dict[str, list]) -> dict[str, list]:
    """Descarta dias con >1 hueco; tolera 1 (cambio horario)."""
    ok = {}
    for fecha, horas in days.items():
        miss = [i for i, x in enumerate(horas) if x is None]
        if len(miss) <= 1:
            for i in miss:
                horas[i] = horas[i - 1] if i > 0 else horas[i + 1]
            ok[fecha] = horas
    return ok


def main() -> int:
    now = datetime.now(TZ)
    hoy = now.date()
    manana = hoy + timedelta(days=1)
    days: dict[str, list] = {}

    print("Fuente primaria: apidatos.ree.es")
    try:
        days = parse_redata(get_json(
            URL_REDATA.format(d0=hoy.isoformat(), d1=manana.isoformat())))
    except Exception as e:  # noqa: BLE001
        print(f"REData no disponible ({type(e).__name__}); "
              f"probando respaldo ESIOS")
        for d in (hoy, manana):
            try:
                days.update(parse_esios(get_json(
                    URL_ESIOS.format(d=d.isoformat()), tries=2)))
            except Exception as e2:  # noqa: BLE001
                print(f"  ESIOS {d}: {type(e2).__name__}: {e2}")

    if not days:
        print("ERROR: ninguna fuente devolvio datos completos.")
        return 1

    previo = {}
    if OUT.exists():
        try:
            previo = json.load(open(OUT, encoding="utf-8")).get("days", {})
        except Exception:  # noqa: BLE001
            previo = {}
    corte = (hoy - timedelta(days=7)).isoformat()
    merged = {f: h for f, h in previo.items() if f >= corte}
    merged.update(days)

    out = {
        "updated": now.isoformat(timespec="minutes"),
        "unit": "EUR/kWh",
        "source": "REE (PVPC peninsular, termino de energia)",
        "days": dict(sorted(merged.items())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"OK: {', '.join(sorted(days))} escritos "
          f"({len(merged)} dias en fichero)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
