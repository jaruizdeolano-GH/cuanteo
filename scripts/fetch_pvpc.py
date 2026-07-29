#!/usr/bin/env python3
"""fetch_pvpc.py — descarga los precios PVPC (hoy + manana si publicado)
desde la API publica de REE y escribe precio-luz/data.json.

Se ejecuta desde GitHub Actions (cron diario) o a mano:
    python scripts/fetch_pvpc.py

Sin dependencias externas: urllib + zoneinfo (stdlib).
Fuente: https://apidatos.ree.es (REData, sin token). El PVPC del dia
siguiente se publica ~20:15 hora peninsular.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
OUT = Path(__file__).resolve().parent.parent / "precio-luz" / "data.json"
URL = ("https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
       "?start_date={d0}T00:00&end_date={d1}T23:59&time_trunc=hour"
       "&geo_ids=8741")
UA = {"User-Agent": "cuanteo.com PVPC fetcher (hola@cuanteo.com)",
      "Accept": "application/json"}


def fetch(d0: str, d1: str) -> dict:
    req = urllib.request.Request(URL.format(d0=d0, d1=d1), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def parse_pvpc(payload: dict) -> dict[str, list]:
    """Devuelve {fecha: [24 precios en EUR/kWh]} a partir de la respuesta."""
    series = None
    for inc in payload.get("included", []):
        if inc.get("type") == "PVPC" or inc.get("id") == "1001":
            series = inc["attributes"]["values"]
            break
    if not series:
        raise RuntimeError("Serie PVPC no encontrada en la respuesta")
    days: dict[str, list] = {}
    for v in series:
        fecha = v["datetime"][:10]
        hora = int(v["datetime"][11:13])
        days.setdefault(fecha, [None] * 24)
        # EUR/MWh -> EUR/kWh
        days[fecha][hora] = round(float(v["value"]) / 1000, 5)
    # descartar dias incompletos (salvo cambio horario: 23/25h se tolera
    # rellenando el hueco con el valor vecino)
    ok = {}
    for fecha, horas in days.items():
        missing = [i for i, x in enumerate(horas) if x is None]
        if len(missing) <= 1:
            for i in missing:
                vecino = horas[i - 1] if i > 0 else horas[i + 1]
                horas[i] = vecino
            ok[fecha] = horas
    return ok


def main() -> int:
    now = datetime.now(TZ)
    hoy = now.date()
    manana = hoy + timedelta(days=1)
    try:
        payload = fetch(hoy.isoformat(), manana.isoformat())
        days = parse_pvpc(payload)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR al obtener PVPC: {type(e).__name__}: {e}")
        return 1
    if not days:
        print("Sin dias completos en la respuesta; no se escribe nada.")
        return 1

    # conservar el historico reciente ya guardado (ultimos 7 dias)
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
        "source": "REE (apidatos.ree.es) - PVPC peninsular, termino de energia",
        "days": dict(sorted(merged.items())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"OK: {', '.join(sorted(days))} escritos en {OUT.name} "
          f"({len(merged)} dias en fichero)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
