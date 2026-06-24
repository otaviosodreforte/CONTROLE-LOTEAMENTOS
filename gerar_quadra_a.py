"""
Gera coordenadas poligonais para Quadra A (23 lotes) do Loteamento Santo Antônio.
Produz INSERTs SQL para o banco SQLite.

Configuracao (ajuste se necessario baseado no croqui):
- Quadra A: 23 lotes (12 na rua A + 11 na rua B)
- L1: 11.3m x 30.12m (canto NW), demais: 10m x 30m
- Referencia (canto NW do L1, na rua A): -11.802011, -42.063321
- Orientacao da testada da rua A: ~136 graus do norte (NW->SE)
"""

import math, json, sqlite3, os

# ─── Parametros ────────────────────────────────────────────────
REF_LAT, REF_LNG = -11.802011, -42.063321  # canto NW do L1 (na rua A)
ANGULO_TESTADA = 136  # graus do norte (sentido horario): direcao da rua A
PROFUNDIDADE = 30.0   # metros (frente->fundo do lote)
LARG_L1 = 11.3        # metros
LARG_PADRAO = 10.0    # metros
N_FRENTE = 12         # lotes na rua A
N_FUNDOS = 11         # lotes na rua B

# Conversao: 1 grau lat = 111000m, 1 grau lng = 108660m (em -11.8)
M_POR_GRAU_LAT = 111000.0
M_POR_GRAU_LNG = 108660.0

def deslocar(lat, lng, ang_graus, dist_m):
    """
    Desloca (lat,lng) de `dist_m` metros na direcao `ang_graus` (0=norte, 90=leste).
    ang_graus = bearing (azimute) do norte em sentido horario.
    """
    ang_rad = math.radians(ang_graus)
    dlat = math.cos(ang_rad) * dist_m / M_POR_GRAU_LAT
    dlng = math.sin(ang_rad) * dist_m / M_POR_GRAU_LNG
    return (lat + dlat, lng + dlng)

def retangulo_poly(canto_nw, ang_testada, largura_m, profundidade_m, ang_profundidade=None):
    """
    4 vertices de um retangulo (poligono fechado).
    ang_testada: direcao da testada (0=norte).
    ang_profundidade: direcao da profundidade. Default = ang_testada + 90 (sentido horario).
    """
    if ang_profundidade is None:
        ang_profundidade = ang_testada + 90
    lat0, lng0 = canto_nw
    ne = deslocar(lat0, lng0, ang_testada, largura_m)
    se = deslocar(ne[0], ne[1], ang_profundidade, profundidade_m)
    sw = deslocar(lat0, lng0, ang_profundidade, profundidade_m)
    return [[lat0, lng0], [ne[0], ne[1]], [se[0], se[1]], [sw[0], sw[1]], [lat0, lng0]]

# ─── Calculos ──────────────────────────────────────────────────
resultados = []

# Largura total da rua A (12 lotes)
larg_total_frente = LARG_L1 + (N_FRENTE - 1) * LARG_PADRAO  # 121.3m
# Largura total da rua B (11 lotes)
larg_total_fundos = N_FUNDOS * LARG_PADRAO  # 110m
# Profundidade total do quarteirao: 2 fileiras de 30m
prof_total = 2 * PROFUNDIDADE  # 60m

# Poligono da Quadra A (quarteirao inteiro)
# Canto NW da quadra = canto NW do L1 (na rua A)
quadra_poly = retangulo_poly((REF_LAT, REF_LNG), ANGULO_TESTADA, larg_total_frente, prof_total)

print("→ Poligono da QUADRA A (quarteirao):")
print(json.dumps(quadra_poly, ensure_ascii=False))
print()

# ─── Fileira da RUA A (frente, 12 lotes: L1 a L12) ────────────
print("→ Fileira RUA A (frente):")
canto_atual = (REF_LAT, REF_LNG)
for i in range(N_FRENTE):
    larg = LARG_L1 if i == 0 else LARG_PADRAO
    numero = i + 1
    poly = retangulo_poly(canto_atual, ANGULO_TESTADA, larg, PROFUNDIDADE)
    resultados.append((numero, larg, 30.12 if i == 0 else PROFUNDIDADE, poly))
    print(f"  L{numero:02d} (larg={larg}m) → NW={canto_atual}")
    canto_atual = deslocar(canto_atual[0], canto_atual[1], ANGULO_TESTADA, larg)

# ─── Fileira da RUA B (fundos, 11 lotes: L13 a L23) ───────────
print("→ Fileira RUA B (fundos):")
# Canto (primeiro vertice) da fileira B = REF + 30m na perpendicular (SW) = na rua B
ang_perp_saida = ANGULO_TESTADA + 90  # direcao saindo do quarteirao
canto_fundos_base = deslocar(REF_LAT, REF_LNG, ang_perp_saida, prof_total)

# A rua B tem 110m de testada (11 lotes de 10m), enquanto a rua A tem 121.3m (12 lotes)
# A rua B fica centralizada: diferenca = 121.3 - 110 = 11.3m → deslocamento = 5.65m
ang_profundidade = ANGULO_TESTADA - 90  # NE = para dentro do quarteirao (em direcao a rua A)
desloc_central = (larg_total_frente - larg_total_fundos) / 2
canto_fundos = deslocar(canto_fundos_base[0], canto_fundos_base[1], ANGULO_TESTADA, desloc_central)

for i in range(N_FUNDOS):
    numero = N_FRENTE + i + 1
    poly = retangulo_poly(canto_fundos, ANGULO_TESTADA, LARG_PADRAO, PROFUNDIDADE, ang_profundidade)
    resultados.append((numero, LARG_PADRAO, PROFUNDIDADE, poly))
    print(f"  L{numero:02d} (larg={LARG_PADRAO}m) → NW={canto_fundos}")
    canto_fundos = deslocar(canto_fundos[0], canto_fundos[1], ANGULO_TESTADA, LARG_PADRAO)

# ─── SQL ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SQL para atualizar banco:")
print("=" * 60)

print(f"\n-- polygon_coords da QUADRA A:")
print(f"UPDATE quadras SET polygon_coords = '{json.dumps(quadra_poly, ensure_ascii=False)}' WHERE id = 1;")

print(f"\n-- Deletar lotes existentes da Quadra A:")
print(f"DELETE FROM lotes WHERE quadra_id = 1;")

print(f"\n-- Inserir 23 lotes da Quadra A:")
for num, larg, profund, poly in resultados:
    num_str = str(num).zfill(2)
    poly_str = json.dumps(poly, ensure_ascii=False)
    frente = larg
    fundo = 30.12 if num == 1 else profund
    print(f"INSERT INTO lotes (quadra_id, numero, tamanho_frente, tamanho_fundo, status, polygon_coords)")
    print(f"VALUES (1, '{num_str}', {frente}, {fundo}, 'disponivel', '{poly_str}');")

# ─── GeoJSON para debug ────────────────────────────────────────
geojson = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"tipo": "quadra", "nome": "Quadra A"},
            "geometry": {"type": "Polygon", "coordinates": [quadra_poly]}
        }
    ]
}
for num, larg, profund, poly in resultados:
    geojson["features"].append({
        "type": "Feature",
        "properties": {"tipo": "lote", "numero": num},
        "geometry": {"type": "Polygon", "coordinates": [poly]}
    })

path = os.path.join(os.path.dirname(__file__) or ".", "quadra_a_debug.geojson")
with open(path, "w", encoding="utf-8") as f:
    json.dump(geojson, f, ensure_ascii=False, indent=2)
print(f"\nGeoJSON salvo em: {path}")
