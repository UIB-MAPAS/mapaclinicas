#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║         ETL Pre-geocodificador — Mapa Clínicas Broker           ║
╠══════════════════════════════════════════════════════════════════╣
║  Lee locales.csv y clinicas.csv, consulta Nominatim (gratis)    ║
║  y agrega columnas Lat / Lng a cada fila.                       ║
║                                                                  ║
║  Uso:                                                            ║
║    python geocodificar.py              ← procesa ambos CSVs     ║
║    python geocodificar.py locales      ← solo locales           ║
║    python geocodificar.py clinicas     ← solo clínicas          ║
║    python geocodificar.py reset-cache  ← borra caché y reprocesa║
╚══════════════════════════════════════════════════════════════════╝
"""

import csv
import json
import time
import os
import sys
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
DELAY_SEG       = 1.25       # Espera entre llamadas a Nominatim (max 1/seg)
CACHE_FILE      = "data/geocode_cache.json"
LOCALES_CSV     = "data/locales.csv"
CLINICAS_CSV    = "data/clinicas.csv"
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
USER_AGENT      = "MapaClinicasBroker/1.0 interno"
TIMEOUT_SEG     = 10
REINTENTOS      = 2          # Veces que reintenta una dirección antes de marcarla como fallida
# Bounding box de Perú (para que Nominatim no devuelva resultados de otro país)
VIEWBOX_PERU    = "-81.4,-18.4,-68.6,-0.0"   # minLng,minLat,maxLng,maxLat

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DE CONSOLA
# ─────────────────────────────────────────────────────────────────────────────
def barra(actual, total, ancho=30):
    lleno = int(ancho * actual / total) if total else 0
    vacio = ancho - lleno
    pct   = int(100 * actual / total) if total else 0
    return f"[{'█' * lleno}{'░' * vacio}] {pct:3d}%"

def eta(inicio, actual, total):
    if actual == 0:
        return "--:--"
    transcurrido = (datetime.now() - inicio).total_seconds()
    restante = transcurrido / actual * (total - actual)
    return str(timedelta(seconds=int(restante)))

def limpiar_linea():
    print("\r" + " " * 100 + "\r", end="")

# ─────────────────────────────────────────────────────────────────────────────
# CACHÉ
# ─────────────────────────────────────────────────────────────────────────────
def cargar_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("  ⚠ Caché corrupta, se reinicia desde cero.")
    return {}

def guardar_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# LIMPIEZA DE TEXTO
# ─────────────────────────────────────────────────────────────────────────────
CHAR_MAP = {"\ufffd": "", "Nº": "N", "N°": "N", "Nro.": "", "Nr.": ""}

def limpiar(texto):
    """Elimina caracteres corruptos y normaliza texto para geocoding."""
    for viejo, nuevo in CHAR_MAP.items():
        texto = texto.replace(viejo, nuevo)
    # Colapsar espacios múltiples
    texto = re.sub(r"\s{2,}", " ", texto)
    return texto.strip()

def simplificar_via(nombre_via):
    """
    Para direcciones compuestas tipo 'AV LOS PINOS - AV LAS FLORES 100-200',
    toma solo el primer segmento antes de ' - ' o ';'.
    """
    for sep in [" - ", ";", "/"]:
        if sep in nombre_via:
            nombre_via = nombre_via.split(sep)[0]
    return nombre_via.strip()

# ─────────────────────────────────────────────────────────────────────────────
# GEOCODIFICACIÓN
# ─────────────────────────────────────────────────────────────────────────────
def _llamar_nominatim(query):
    """Realiza una petición a Nominatim y devuelve (lat, lng) o None."""
    params = urllib.parse.urlencode({
        "q":           query,
        "format":      "json",
        "limit":       1,
        "countrycodes":"pe",
        "viewbox":     VIEWBOX_PERU,
        "bounded":     0,        # permite resultados fuera del viewbox si no hay coincidencia
    })
    url = f"{NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None

def geocodificar(query, cache):
    """
    Intenta geocodificar 'query'. Usa caché para no repetir llamadas.
    Si la query principal falla, intenta una versión simplificada.
    Devuelve (lat, lng) o (None, None).
    """
    key = query.lower().strip()

    # 1. Buscar en caché
    if key in cache:
        cached = cache[key]
        if cached:
            return cached["lat"], cached["lng"]
        return None, None   # ya sabemos que falla

    # 2. Llamar a Nominatim con reintentos
    resultado = None
    for intento in range(REINTENTOS):
        resultado = _llamar_nominatim(query)
        time.sleep(DELAY_SEG)
        if resultado:
            break
        if intento < REINTENTOS - 1:
            time.sleep(DELAY_SEG)   # espera extra antes de reintentar

    # 3. Guardar en caché (éxito o fallo)
    if resultado:
        lat, lng = resultado
        cache[key] = {"lat": lat, "lng": lng}
        guardar_cache(cache)
        return lat, lng
    else:
        cache[key] = None
        guardar_cache(cache)
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# PROCESAMIENTO DE LOCALES
# ─────────────────────────────────────────────────────────────────────────────
def construir_query_local(fila):
    """Construye la query de dirección para un local asegurado."""
    tipo      = limpiar(fila.get("TipoVia", "")).title()
    via       = limpiar(fila.get("NombreVia", "")).title()
    distrito  = limpiar(fila.get("Distrito", "")).title()
    provincia = limpiar(fila.get("Provincia", "Lima")).title()

    via_simple = simplificar_via(via)

    # Query completa primero, simplificada como fallback
    query_principal = f"{tipo} {via_simple}, {distrito}, {provincia}, Peru"
    query_fallback  = f"{distrito}, {provincia}, Peru"
    return query_principal, query_fallback

def procesar_locales(cache):
    print("\n" + "─" * 64)
    print("  📍 LOCALES ASEGURADOS")
    print("─" * 64)

    with open(LOCALES_CSV, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        filas  = list(reader)
        campos = list(reader.fieldnames) if reader.fieldnames else list(filas[0].keys())

    total = len(filas)
    ok = fallo = ya_tenia = 0
    inicio = datetime.now()

    for i, fila in enumerate(filas):
        # ── Ya tiene coordenadas ──────────────────────────────────────
        lat_actual = fila.get("Lat", "").strip()
        lng_actual = fila.get("Lng", "").strip()
        if lat_actual and lng_actual:
            ya_tenia += 1
            print(f"\r  [{i+1:>4}/{total}] ✓ coord. existente: {fila.get('Referencia','')[:45]:<45}", end="")
            continue

        # ── Construir query ───────────────────────────────────────────
        query_principal, query_fallback = construir_query_local(fila)
        ref = fila.get("Referencia", "")[:25]
        print(f"\r  [{i+1:>4}/{total}] {barra(i, total)}  ETA {eta(inicio, i, total)}  {ref:<25}", end="")

        lat, lng = geocodificar(query_principal, cache)

        # ── Fallback a nivel de distrito si falló ─────────────────────
        if lat is None:
            lat, lng = geocodificar(query_fallback, cache)

        if lat is not None:
            fila["Lat"] = f"{lat:.6f}"
            fila["Lng"] = f"{lng:.6f}"
            ok += 1
        else:
            fila["Lat"] = ""
            fila["Lng"] = ""
            fallo += 1

    limpiar_linea()

    # ── Agregar columnas Lat/Lng si no estaban ────────────────────────
    for col in ["Lat", "Lng"]:
        if col not in campos:
            campos.append(col)

    with open(LOCALES_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filas)

    total_con = ok + ya_tenia
    print(f"  Resultado → ✅ {total_con}/{total} con coordenadas  |  ⚠ {fallo} sin geocodificar")
    return ok, fallo, ya_tenia

# ─────────────────────────────────────────────────────────────────────────────
# PROCESAMIENTO DE CLÍNICAS
# ─────────────────────────────────────────────────────────────────────────────
def construir_query_clinica(fila):
    """Construye la query de dirección para una clínica afiliada."""
    dir_raw  = limpiar(fila.get("Direccion", ""))
    distrito = limpiar(fila.get("Distrito", "")).title()

    # La dirección ya suele incluir el distrito, pero añadimos de todas formas
    query_principal = f"{dir_raw}, {distrito}, Peru"
    query_fallback  = f"{distrito}, Lima, Peru"
    return query_principal, query_fallback

def procesar_clinicas(cache):
    print("\n" + "─" * 64)
    print("  🏥 CLÍNICAS AFILIADAS")
    print("─" * 64)

    with open(CLINICAS_CSV, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        filas  = list(reader)
        campos = list(reader.fieldnames) if reader.fieldnames else list(filas[0].keys())

    total = len(filas)
    ok = fallo = ya_tenia = 0
    inicio = datetime.now()

    for i, fila in enumerate(filas):
        lat_actual = fila.get("Lat", "").strip()
        lng_actual = fila.get("Lng", "").strip()
        if lat_actual and lng_actual:
            ya_tenia += 1
            print(f"\r  [{i+1:>4}/{total}] ✓ coord. existente: {fila.get('Nombre','')[:45]:<45}", end="")
            continue

        query_principal, query_fallback = construir_query_clinica(fila)
        nombre = fila.get("Nombre", "")[:30]
        print(f"\r  [{i+1:>4}/{total}] {barra(i, total)}  ETA {eta(inicio, i, total)}  {nombre:<30}", end="")

        lat, lng = geocodificar(query_principal, cache)
        if lat is None:
            lat, lng = geocodificar(query_fallback, cache)

        if lat is not None:
            fila["Lat"] = f"{lat:.6f}"
            fila["Lng"] = f"{lng:.6f}"
            ok += 1
        else:
            fila["Lat"] = ""
            fila["Lng"] = ""
            fallo += 1

    limpiar_linea()

    for col in ["Lat", "Lng"]:
        if col not in campos:
            campos.append(col)

    with open(CLINICAS_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filas)

    total_con = ok + ya_tenia
    print(f"  Resultado → ✅ {total_con}/{total} con coordenadas  |  ⚠ {fallo} sin geocodificar")
    return ok, fallo, ya_tenia

# ─────────────────────────────────────────────────────────────────────────────
# REPORTE DE FALLIDOS
# ─────────────────────────────────────────────────────────────────────────────
def reporte_fallidos():
    """Imprime las filas sin coordenadas para revisión manual."""
    fallidos = []

    for archivo, label in [(LOCALES_CSV, "local"), (CLINICAS_CSV, "clínica")]:
        with open(archivo, encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f, delimiter=";"):
                if not fila.get("Lat") or not fila.get("Lng"):
                    nombre = fila.get("Referencia") or fila.get("Nombre") or "?"
                    via    = f"{fila.get('TipoVia','')} {fila.get('NombreVia','')}".strip()
                    dir_   = fila.get("Direccion", via)
                    dist   = fila.get("Distrito", "")
                    fallidos.append((label, nombre, dir_, dist))

    if not fallidos:
        print("\n  🎉 ¡Todas las filas tienen coordenadas!")
        return

    print(f"\n{'─'*64}")
    print(f"  ⚠  {len(fallidos)} fila(s) SIN geocodificar — revisión manual:")
    print(f"{'─'*64}")
    print(f"  {'Tipo':<8}  {'Nombre/Ref':<28}  {'Dirección':<40}  Distrito")
    print(f"  {'─'*8}  {'─'*28}  {'─'*40}  {'─'*20}")
    for label, nombre, dir_, dist in fallidos:
        print(f"  {label:<8}  {nombre[:28]:<28}  {dir_[:40]:<40}  {dist}")
    print(f"\n  👉 Para corregirlas manualmente:")
    print(f"     1. Busca la dirección en maps.google.com")
    print(f"     2. Clic derecho → copia las coordenadas")
    print(f"     3. Agrégalas en las columnas Lat y Lng del CSV")
    print(f"     4. Vuelve a correr el script (respeta la caché)")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # ── Verificar directorio de trabajo ──────────────────────────────
    if not os.path.isdir("data"):
        print("\n  ⛔ Error: no se encontró la carpeta 'data/'.")
        print("     Ejecuta este script desde la carpeta raíz del proyecto:")
        print("     cd ruta/a/mapa_clinicas && python geocodificar.py\n")
        sys.exit(1)

    # ── Argumento de línea de comandos ────────────────────────────────
    modo = sys.argv[1].lower() if len(sys.argv) > 1 else "ambos"

    # ── Cabecera ──────────────────────────────────────────────────────
    print("\n" + "═" * 64)
    print("  ETL Pre-geocodificador   —   Mapa Clínicas Broker")
    print(f"  {datetime.now().strftime('%d/%m/%Y  %H:%M:%S')}")
    print("═" * 64)

    # ── Caché ─────────────────────────────────────────────────────────
    if modo == "reset-cache":
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            print("\n  🗑  Caché eliminada. Se geocodificará todo desde cero.")
        else:
            print("\n  ℹ  No había caché. Continuando normalmente.")
        modo = "ambos"

    cache = cargar_cache()
    print(f"\n  Caché cargada: {len(cache)} direcciones ya procesadas")

    # ── Estimar tiempo ─────────────────────────────────────────────────
    pendientes = 0
    for archivo in [LOCALES_CSV, CLINICAS_CSV]:
        if not os.path.exists(archivo):
            continue
        with open(archivo, encoding="utf-8-sig", errors="replace", newline="") as f:
            for fila in csv.DictReader(f, delimiter=";"):
                key = ""
                if "TipoVia" in fila:   # local
                    via = simplificar_via(limpiar(fila.get("NombreVia", ""))).title()
                    key = f"{limpiar(fila.get('TipoVia','')).title()} {via}, {limpiar(fila.get('Distrito','')).title()}, {limpiar(fila.get('Provincia','Lima')).title()}, Peru".lower()
                else:                   # clínica
                    key = f"{limpiar(fila.get('Direccion',''))}, {limpiar(fila.get('Distrito','')).title()}, Peru".lower()
                if not fila.get("Lat") and key not in cache:
                    pendientes += 1

    if pendientes > 0:
        mins = pendientes * DELAY_SEG / 60
        print(f"  Pendientes de geocodificar: {pendientes}  (~{mins:.1f} min)")
    else:
        print("  ✅ Todo ya está en caché. La ejecución será instantánea.")

    # ── Procesar ───────────────────────────────────────────────────────
    try:
        if modo in ("ambos", "locales"):
            if not os.path.exists(LOCALES_CSV):
                print(f"\n  ⚠ No se encontró {LOCALES_CSV}, saltando.")
            else:
                procesar_locales(cache)

        if modo in ("ambos", "clinicas"):
            if not os.path.exists(CLINICAS_CSV):
                print(f"\n  ⚠ No se encontró {CLINICAS_CSV}, saltando.")
            else:
                procesar_clinicas(cache)

    except KeyboardInterrupt:
        print("\n\n  ⏸  Interrumpido por el usuario. El progreso fue guardado en caché.")
        print("      Vuelve a correr el script para retomar donde se quedó.\n")
        sys.exit(0)

    # ── Resumen y fallidos ─────────────────────────────────────────────
    reporte_fallidos()

    print("\n" + "═" * 64)
    print("  ✅ Proceso completado.")
    print("     Los CSVs ya tienen columnas Lat y Lng.")
    print("     La app cargará el mapa en menos de 2 segundos.")
    print("═" * 64 + "\n")

if __name__ == "__main__":
    main()
