"""
geocodificar_v2.py — ETL de geocodificación para clínicas (sin Google Maps)
============================================================================

ESTRATEGIA (en orden de precisión):
  1. Consulta RUC en SUNAT (via apis.net.pe) → dirección oficial del establecimiento
  2. Geocodifica la dirección con OpenCage / Geoapify / Nominatim
  3. Valida que el resultado esté dentro de Perú
  4. Si falla, reintenta con dirección simplificada

MODOS DE USO:

  a) Arreglar coords erróneas del clinicas.csv existente:
       python geocodificar_v2.py fix

  b) Importar Excel y geocodificar desde cero:
       python geocodificar_v2.py import CLINICAS_CSV.xlsx

  c) Solo consultar SUNAT para obtener direcciones limpias (no geocodifica):
       python geocodificar_v2.py sunat

  d) Generar prompt para pegar en ChatGPT/Gemini (opción 100% offline):
       python geocodificar_v2.py prompt

FLAGS OPCIONALES (añadir a cualquier modo):
  --opencage-key  KEY   API key de OpenCage   (opencagedata.com — gratis 2500/día, solo email)
  --geoapify-key  KEY   API key de Geoapify   (geoapify.com — gratis 3000/día, solo email)
  --sunat-key     KEY   Token de apis.net.pe  (apis.net.pe — gratis, solo email)

INSTALAR DEPENDENCIAS:
  pip install requests pandas openpyxl tqdm

REGISTRO SIN TARJETA DE CRÉDITO:
  OpenCage  → https://opencagedata.com/users/sign_up
  Geoapify  → https://myprojects.geoapify.com
  apis.net.pe → https://apis.net.pe  (para RUC de SUNAT)
"""

import csv, json, time, sys, os, re, shutil
import requests

# ── Configuración ─────────────────────────────────────────────────────────────
INPUT_CSV  = 'data/clinicas.csv'
OUTPUT_CSV = 'data/clinicas.csv'
DELAY_NOM  = 1.2   # Nominatim: máx 1 req/seg (obligatorio)
DELAY_API  = 0.3   # APIs comerciales: más rápido

# ── Bounds de validación ──────────────────────────────────────────────────────
PERU_BOUNDS = dict(lat_min=-18.5, lat_max=-0.0, lng_min=-81.5, lng_max=-68.0)

# ── Mapeo categorías del Excel → campo Tipo del CSV ───────────────────────────
CATEGORIA_MAP = {
    # Lima
    'CLINICA LIMA':                         'CLINICA_GENERAL',
    'CLINICA LIMA-RED QUEMADURA':           'CLINICA_QUEMADURA',
    'MED FIS. Y REAH':                      'MED_FISIOTERAPIA',
    'MEDICINA FISICA Y REHABILITACION':     'MED_FISIOTERAPIA',
    'APOYO AL DX':                          'APOYO_DX',
    'ODONTOLOGIA LIMA':                     'ODONTOLOGIA',
    'AMD LIMA':                             'AMD',
    # Provincia
    'CLINICA PROVINCIA':                    'CLINICA_GENERAL',
    'AMD PROVINCIA':                        'AMD',
    'APOYO AL DX PROVINCIA':               'APOYO_DX',
    'MEDICINA FISICA Y REHABILITACION P':  'MED_FISIOTERAPIA',
    'ODONTOLOGIA PROVINCIA':               'ODONTOLOGIA',
    'OFTALMOLOGIA PROVINCIA':              'OFTALMOLOGIA',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def in_peru(lat, lng):
    return (PERU_BOUNDS['lat_min'] <= lat <= PERU_BOUNDS['lat_max'] and
            PERU_BOUNDS['lng_min'] <= lng <= PERU_BOUNDS['lng_max'])

def clean_address(addr, distrito=''):
    """Normaliza una dirección peruana para mejorar geocoding."""
    addr = re.sub(r'\bN[°º]?\s*', 'N° ', addr, flags=re.IGNORECASE)
    addr = re.sub(r'\s+', ' ', addr).strip()
    # Expandir abreviaturas comunes
    addr = re.sub(r'\bAv\.?\b', 'Avenida', addr, flags=re.IGNORECASE)
    addr = re.sub(r'\bCl\.?\b', 'Calle', addr, flags=re.IGNORECASE)
    addr = re.sub(r'\bJr\.?\b', 'Jirón', addr, flags=re.IGNORECASE)
    addr = re.sub(r'\bUrb\.?\b', 'Urbanización', addr, flags=re.IGNORECASE)
    if distrito and distrito.upper() not in addr.upper():
        addr = f"{addr}, {distrito}"
    return addr

def delay(is_nominatim=False):
    time.sleep(DELAY_NOM if is_nominatim else DELAY_API)


# ── Backends de geocoding ─────────────────────────────────────────────────────

def geocode_opencage(query, api_key):
    """OpenCage Geocoding API — gratis 2500/día, solo email, sin tarjeta."""
    url = 'https://api.opencagedata.com/geocode/v1/json'
    params = dict(q=query, key=api_key, countrycode='pe', language='es',
                  limit=1, no_annotations=1)
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results:
            loc = results[0]['geometry']
            return loc['lat'], loc['lng']
    except Exception as e:
        print(f"    [OpenCage error] {e}")
    return None, None

def geocode_geoapify(query, api_key):
    """Geoapify Geocoding API — gratis 3000/día, solo email, sin tarjeta."""
    url = 'https://api.geoapify.com/v1/geocode/search'
    params = dict(text=query, apiKey=api_key, filter='countrycode:pe',
                  lang='es', limit=1)
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        features = r.json().get('features', [])
        if features:
            coords = features[0]['geometry']['coordinates']
            return coords[1], coords[0]  # [lng, lat] → lat, lng
    except Exception as e:
        print(f"    [Geoapify error] {e}")
    return None, None

def geocode_nominatim(query):
    """Nominatim (OpenStreetMap) — completamente gratis, sin registro."""
    url = 'https://nominatim.openstreetmap.org/search'
    params = dict(q=query, format='json', limit=1, countrycodes='pe')
    headers = {'Accept-Language': 'es', 'User-Agent': 'MapaClinicasBroker/2.0'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f"    [Nominatim error] {e}")
    delay(is_nominatim=True)
    return None, None

def geocode_nominatim_full(query):
    """
    Como geocode_nominatim pero devuelve el resultado completo (dict con class, type, etc.)
    para poder verificar la precisión del resultado.
    """
    url = 'https://nominatim.openstreetmap.org/search'
    params = dict(q=query, format='json', limit=1, countrycodes='pe')
    headers = {'Accept-Language': 'es', 'User-Agent': 'MapaClinicasBroker/2.0'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            delay(is_nominatim=True)
            return data[0]
    except Exception as e:
        print(f"    [Nominatim error] {e}")
    delay(is_nominatim=True)
    return None

# Tipos de resultado Nominatim que indican bajo nivel de precisión (ciudad/estado/país)
_IMPRECISOS = {
    ('place', 'city'), ('place', 'town'), ('place', 'village'),
    ('place', 'country'), ('place', 'state'), ('place', 'region'),
    ('boundary', 'administrative'),
}

def es_preciso(nominatim_result):
    """Devuelve True si el resultado es al menos a nivel de calle/barrio."""
    if not nominatim_result:
        return False
    cls = nominatim_result.get('class', '')
    typ = nominatim_result.get('type', '')
    return (cls, typ) not in _IMPRECISOS

def geocode(query, opencage_key=None, geoapify_key=None):
    """Intenta geocodificar en orden de calidad."""
    if opencage_key:
        lat, lng = geocode_opencage(query, opencage_key)
        delay()
        if lat and in_peru(lat, lng):
            return lat, lng
    if geoapify_key:
        lat, lng = geocode_geoapify(query, geoapify_key)
        delay()
        if lat and in_peru(lat, lng):
            return lat, lng
    # Fallback: Nominatim
    lat, lng = geocode_nominatim(query)
    if lat and in_peru(lat, lng):
        return lat, lng
    return None, None


# ── SUNAT via apis.net.pe ─────────────────────────────────────────────────────

def sunat_lookup(ruc, sunat_key):
    """
    Consulta la dirección oficial registrada en SUNAT para un RUC.
    Usa la API gratuita de apis.net.pe (requiere token, sin tarjeta).
    Registro: https://apis.net.pe
    """
    if not sunat_key or not ruc or len(str(ruc).strip()) != 11:
        return None
    ruc = str(ruc).strip()
    url = f'https://api.apis.net.pe/v2/sunat/ruc?numero={ruc}'
    headers = {'Authorization': f'Bearer {sunat_key}', 'Accept': 'application/json'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Campos: direccion, ubigeo, departamento, provincia, distrito
            direccion = data.get('direccion', '')
            distrito  = data.get('distrito', '')
            return {'direccion': direccion, 'distrito': distrito, 'raw': data}
        else:
            print(f"    [SUNAT] RUC {ruc} → status {r.status_code}")
    except Exception as e:
        print(f"    [SUNAT error] {e}")
    return None

ZONA_CIUDAD = {
    'LIMA':                   'Lima',
    'LIMA CENTRO':            'Lima',
    'LIMA NORTE':             'Lima',
    'LIMA SUR':               'Lima',
    'LIMA ESTE':              'Lima',
    'LIMA PROVINCIA':         'Lima',
    'CALLAO':                 'Callao',
    'CALLAO - LIMA CERCADO':  'Lima',
    'LIMA - NO QUEMADURA':    'Lima',
}

def ciudad_de_zona(zona):
    """Devuelve la ciudad/región para armar la query. Si no está en el mapa, usa la zona tal cual."""
    if not zona:
        return 'Lima'
    return ZONA_CIUDAD.get(zona.strip().upper(), zona.strip().title())

def build_queries(nombre, direccion, distrito, sunat_data=None, zona=''):
    """
    Genera una lista de queries de geocoding de mejor a peor,
    usando datos SUNAT si están disponibles y la Zona para soporte provincial.
    """
    ciudad = ciudad_de_zona(zona)
    queries = []
    if sunat_data:
        d    = sunat_data.get('direccion', '')
        dist = sunat_data.get('distrito', distrito)
        if d:
            queries.append(f"{d}, {dist}, {ciudad}, Peru")
            queries.append(f"{d}, Peru")
    addr_clean = clean_address(direccion, distrito)
    queries.append(f"{addr_clean}, {ciudad}, Peru")
    queries.append(f"{addr_clean}, Peru")
    queries.append(f"{nombre}, {distrito}, {ciudad}, Peru")
    return queries


# ── Geocodificación con múltiples intentos ────────────────────────────────────

def geocode_one(nombre, ruc, direccion, distrito, zona='',
                sunat_key=None, opencage_key=None, geoapify_key=None):
    """Intenta todas las estrategias hasta conseguir coordenadas válidas."""
    sunat_data = None
    if sunat_key and ruc:
        print(f"    Consultando SUNAT (RUC {ruc})...")
        sunat_data = sunat_lookup(ruc, sunat_key)
        if sunat_data:
            print(f"    SUNAT dir: {sunat_data.get('direccion', '?')}")
        delay()

    queries = build_queries(nombre, direccion, distrito, sunat_data, zona)

    for q in queries:
        print(f"    Geocodificando: {q}")
        lat, lng = geocode(q, opencage_key, geoapify_key)
        if lat:
            print(f"    OK {lat:.6f}, {lng:.6f}")
            return lat, lng, sunat_data

    print(f"    FALLO: {nombre}")
    return None, None, sunat_data


# ── Comando: fix (re-geocodifica erróneos) ───────────────────────────────────

def cmd_fix(sunat_key=None, opencage_key=None, geoapify_key=None):
    """Re-geocodifica filas con coordenadas ausentes o fuera de Perú."""
    rows = []
    with open(INPUT_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    # Detectar si hay columna RUC
    has_ruc = 'RUC' in (fieldnames or [])

    bad = []
    for row in rows:
        lat_s = row.get('Lat', '').strip()
        lng_s = row.get('Lng', '').strip()
        if not lat_s or not lng_s:
            bad.append(row)
            continue
        try:
            if not in_peru(float(lat_s), float(lng_s)):
                bad.append(row)
                print(f"[INVÁLIDO] {row['Nombre']}: {lat_s}, {lng_s}")
        except ValueError:
            bad.append(row)

    print(f"\n{len(bad)} filas a re-geocodificar de {len(rows)} totales.\n")

    fixed = 0
    for row in bad:
        ruc = row.get('RUC', '').strip() if has_ruc else ''
        print(f"\n[GEOCODING] {row['Nombre']}")
        lat, lng, _ = geocode_one(
            nombre=row.get('Nombre', ''),
            ruc=ruc,
            direccion=row.get('Direccion', ''),
            distrito=row.get('Distrito', ''),
            zona=row.get('Zona', ''),
            sunat_key=sunat_key,
            opencage_key=opencage_key,
            geoapify_key=geoapify_key,
        )
        if lat:
            row['Lat'] = f"{lat:.6f}"
            row['Lng'] = f"{lng:.6f}"
            fixed += 1

    backup = INPUT_CSV.replace('.csv', '_backup.csv')
    shutil.copy2(INPUT_CSV, backup)
    print(f"\nBackup → {backup}")

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)

    print(f"✓ {fixed}/{len(bad)} filas re-geocodificadas → {OUTPUT_CSV}")


# ── Comando: import (desde Excel) ─────────────────────────────────────────────

def cmd_import(excel_path, sunat_key=None, opencage_key=None, geoapify_key=None):
    """
    Importa el Excel de clínicas MAPFRE y genera clinicas_import.csv geocodificado.

    Columnas esperadas en el Excel (el script las detecta automáticamente):
      Categoria | RUC | Nombre | Zona | Direccion | Distrito | Telefono
    """
    try:
        import pandas as pd
    except ImportError:
        print("Instala pandas: pip install pandas openpyxl")
        sys.exit(1)

    print(f"Leyendo {excel_path}...")
    # Prueba varias hojas
    try:
        df = pd.read_excel(excel_path, header=0)
    except Exception as e:
        print(f"Error leyendo Excel: {e}")
        sys.exit(1)

    print(f"Columnas: {list(df.columns)}")

    # Detectar columnas por nombre aproximado
    col = {}
    for c in df.columns:
        cu = str(c).upper().strip()
        if 'CATEG' in cu or ('TIPO' in cu and 'RUC' not in cu):  col['categoria'] = c
        elif 'RUC' in cu:                                          col['ruc'] = c
        elif 'NOMBRE' in cu or 'CLINICA' in cu:                   col['nombre'] = c
        elif 'ZONA' in cu:                                         col['zona'] = c
        elif 'DIRECC' in cu:                                       col['direccion'] = c
        elif 'DISTR' in cu:                                        col['distrito'] = c
        elif 'TEL' in cu or 'FONO' in cu:                         col['telefono'] = c

    print(f"Mapeo detectado: {col}\n")

    rows_out = []
    for idx, row in df.iterrows():
        categoria = str(row.get(col.get('categoria', ''), '')).strip()
        ruc       = str(row.get(col.get('ruc', ''), '')).strip()
        nombre    = str(row.get(col.get('nombre', ''), '')).strip()
        direccion = str(row.get(col.get('direccion', ''), '')).strip()
        distrito  = str(row.get(col.get('distrito', ''), '')).strip()
        telefono  = str(row.get(col.get('telefono', ''), '')).strip()
        tipo      = CATEGORIA_MAP.get(categoria.upper(), 'CLINICA_GENERAL')

        if not nombre or nombre == 'nan' or not direccion or direccion == 'nan':
            continue

        zona = str(row.get(col.get('zona', ''), '')).strip()
        print(f"\n[{idx+1}] {nombre} ({tipo}) — zona: {zona or '-'}")
        lat, lng, sunat = geocode_one(
            nombre=nombre, ruc=ruc, direccion=direccion, distrito=distrito, zona=zona,
            sunat_key=sunat_key, opencage_key=opencage_key, geoapify_key=geoapify_key,
        )

        # Si SUNAT devolvió una dirección más limpia, usarla
        final_dir = sunat.get('direccion', direccion) if sunat and sunat.get('direccion') else direccion
        final_dis = sunat.get('distrito', distrito)   if sunat and sunat.get('distrito')  else distrito

        rows_out.append({
            'Nombre':    nombre,
            'Tipo':      tipo,
            'RUC':       ruc,
            'Direccion': final_dir or direccion,
            'Distrito':  final_dis or distrito,
            'Telefono':  telefono,
            'Lat':       f"{lat:.6f}" if lat else '',
            'Lng':       f"{lng:.6f}" if lng else '',
        })

    out_path = 'data/clinicas_import.csv'
    fieldnames = ['Nombre', 'Tipo', 'RUC', 'Direccion', 'Distrito', 'Telefono', 'Lat', 'Lng']
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows_out)

    ok  = sum(1 for r in rows_out if r['Lat'])
    bad = len(rows_out) - ok
    print(f"\n✓ {ok} con coords · {bad} sin coords → {out_path}")
    if bad:
        print("  Las filas sin coords las podés corregir con:")
        print("  python geocodificar_v2.py fix")


# ── Comando: sunat (solo consulta SUNAT, sin geocodificar) ────────────────────

def cmd_sunat(sunat_key):
    """Enriquece el CSV con las direcciones oficiales de SUNAT usando el RUC."""
    if not sunat_key:
        print("Necesitás --sunat-key. Registrate gratis en https://apis.net.pe")
        sys.exit(1)

    rows = []
    with open(INPUT_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        fieldnames = list(reader.fieldnames)
        if 'DireccionSUNAT' not in fieldnames:
            fieldnames.insert(fieldnames.index('Direccion') + 1, 'DireccionSUNAT')
        for row in reader:
            rows.append(row)

    if 'RUC' not in fieldnames:
        print("El CSV no tiene columna RUC. Usá primero 'python geocodificar_v2.py import ARCHIVO.xlsx'")
        sys.exit(1)

    for row in rows:
        ruc = row.get('RUC', '').strip()
        if not ruc or row.get('DireccionSUNAT'):
            continue
        print(f"SUNAT {ruc} — {row['Nombre']}")
        data = sunat_lookup(ruc, sunat_key)
        if data:
            row['DireccionSUNAT'] = data.get('direccion', '')
        delay()

    shutil.copy2(INPUT_CSV, INPUT_CSV.replace('.csv', '_backup.csv'))
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)
    print(f"✓ Guardado en {OUTPUT_CSV}")


# ── Comando: prompt (para ChatGPT/Gemini) ─────────────────────────────────────

def cmd_prompt():
    """
    Genera un archivo de texto listo para pegar en ChatGPT o Gemini.
    Ideal si no querés usar ninguna API.
    """
    bad = []
    with open(INPUT_CSV, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter=';'):
            lat_s = row.get('Lat', '').strip()
            lng_s = row.get('Lng', '').strip()
            if not lat_s or not lng_s:
                bad.append(row); continue
            try:
                if not in_peru(float(lat_s), float(lng_s)):
                    bad.append(row)
            except ValueError:
                bad.append(row)

    if not bad:
        print("✓ Todas las clínicas tienen coordenadas válidas.")
        return

    lines = ["Nombre | Dirección completa | Distrito"]
    for r in bad:
        lines.append(f"{r['Nombre']} | {r['Direccion']} | {r['Distrito']}")

    prompt = (
        "Eres un asistente de geocodificación para Perú.\n"
        "Para cada clínica de la lista, devuelve las coordenadas GPS exactas\n"
        "(latitud y longitud) basándote en la dirección y el distrito.\n"
        "Todas están en Lima, Perú (o ciudades peruanas).\n"
        "RESPONDE SOLO en formato CSV, sin explicaciones, con estas columnas:\n"
        "Nombre;Lat;Lng\n\n"
        + "\n".join(lines)
    )

    with open('geocode_prompt.txt', 'w', encoding='utf-8') as f:
        f.write(prompt)

    print(f"✓ Prompt generado en geocode_prompt.txt ({len(bad)} clínicas)")
    print()
    print("Pasos:")
    print("  1. Abrí geocode_prompt.txt y copiá todo el contenido")
    print("  2. Pegalo en ChatGPT (chat.openai.com) o Gemini (gemini.google.com)")
    print("  3. Copiá la respuesta CSV")
    print("  4. Abrí data/clinicas.csv con Excel/Notepad y actualizá las columnas Lat y Lng")
    print()
    print("IMPORTANTE — decile al LLM antes de pegar:")
    print('  "Responde solo con el CSV, sin texto adicional ni bloques de código"')


# ── Comando: locales (geocodifica locales.csv con filtro de precisión) ────────

LOCALES_CSV  = 'data/locales.csv'

def cmd_locales():
    """
    Geocodifica data/locales.csv usando Nominatim (gratis, sin API key).
    Solo guarda coordenadas para los locales con precisión a nivel de calle o barrio.
    Los locales imprecisos quedan con Lat/Lng vacíos → el mapa no los muestra.

    Uso:
        python geocodificar_v2.py locales
    """
    rows = []
    with open(LOCALES_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            rows.append(row)

    # Asegurar columnas Lat, Lng
    if 'Lat' not in fieldnames: fieldnames.append('Lat')
    if 'Lng' not in fieldnames: fieldnames.append('Lng')

    to_geocode = [r for r in rows if not r.get('Lat', '').strip()]
    ya_tienen  = len(rows) - len(to_geocode)
    print(f"{len(rows)} locales · {ya_tienen} ya geocodificados · {len(to_geocode)} a procesar\n")

    geocoded = 0
    skipped  = 0

    for i, row in enumerate(to_geocode, 1):
        referencia = row.get('Referencia', '').strip()
        tipo_via   = row.get('TipoVia', '').strip()
        nombre_via = row.get('NombreVia', '').strip()
        distrito   = row.get('Distrito', '').strip()
        provincia  = row.get('Provincia', '').strip()

        query = ', '.join(filter(None, [
            f"{tipo_via} {nombre_via}".strip(),
            distrito,
            provincia,
            'Peru'
        ]))

        print(f"[{i}/{len(to_geocode)}] {referencia} — {query}")
        resultado = geocode_nominatim_full(query)

        if resultado and in_peru(float(resultado['lat']), float(resultado['lon'])):
            if es_preciso(resultado):
                row['Lat'] = f"{float(resultado['lat']):.6f}"
                row['Lng'] = f"{float(resultado['lon']):.6f}"
                print(f"  OK  {row['Lat']}, {row['Lng']}  [{resultado.get('class','?')}/{resultado.get('type','?')}]")
                geocoded += 1
            else:
                row['Lat'] = ''
                row['Lng'] = ''
                print(f"  IMPRECISO ({resultado.get('class','?')}/{resultado.get('type','?')}) — no se muestra en mapa")
                skipped += 1
        else:
            row['Lat'] = ''
            row['Lng'] = ''
            print(f"  SIN RESULTADO")
            skipped += 1

        # Guardar cada 50 para no perder progreso
        if i % 50 == 0:
            shutil.copy2(LOCALES_CSV, LOCALES_CSV.replace('.csv', '_backup.csv'))
            with open(LOCALES_CSV, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                writer.writeheader()
                writer.writerows(rows)
            print(f"  [Guardado parcial: {i}/{len(to_geocode)}]")

    shutil.copy2(LOCALES_CSV, LOCALES_CSV.replace('.csv', '_backup.csv'))
    with open(LOCALES_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{'='*50}")
    print(f"Geocodificados (precisos) : {geocoded}")
    print(f"Sin mostrar (imprecisos)  : {skipped}")
    print(f"Total locales             : {len(rows)}")
    print(f"Guardado en {LOCALES_CSV}")


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    args = sys.argv[1:]
    flags = {}
    positional = []
    i = 0
    while i < len(args):
        if args[i].startswith('--') and i + 1 < len(args):
            flags[args[i][2:].replace('-', '_')] = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1
    return positional, flags

if __name__ == '__main__':
    positional, flags = parse_args()
    sunat_key    = flags.get('sunat_key')
    opencage_key = flags.get('opencage_key')
    geoapify_key = flags.get('geoapify_key')

    cmd = positional[0] if positional else 'fix'

    print(f"\n{'='*60}")
    print(f"  Geocodificador de Clínicas — modo: {cmd}")
    apis = []
    if sunat_key:    apis.append('SUNAT(apis.net.pe)')
    if opencage_key: apis.append('OpenCage')
    if geoapify_key: apis.append('Geoapify')
    apis.append('Nominatim(fallback)')
    print(f"  APIs activas: {' → '.join(apis)}")
    print(f"{'='*60}\n")

    if cmd == 'fix':
        cmd_fix(sunat_key, opencage_key, geoapify_key)
    elif cmd == 'import' and len(positional) >= 2:
        cmd_import(positional[1], sunat_key, opencage_key, geoapify_key)
    elif cmd == 'sunat':
        cmd_sunat(sunat_key)
    elif cmd == 'prompt':
        cmd_prompt()
    elif cmd == 'locales':
        cmd_locales()
    else:
        print(__doc__)
