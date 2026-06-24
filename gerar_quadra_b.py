"""
Gera coordenadas poligonais para Quadra B (22 lotes) do Loteamento Santo Antônio.
Produz INSERTs SQL para o banco SQLite.

Configuracao:
- Quadra B: 22 lotes (11 na Rua B + 11 na Rua C)
- L1: 14.7m x 30.09m (canto NW, na Rua B)
- L11: 12.35m x 30.09m (canto NE da fileira Rua B)
- Demais lotes: 10m x 30m
- Largura da Rua B entre Quadra A e Quadra B: 12.6m
- Referencia: canto SW da Quadra A + 12.6m na direcao 226 graus
- Orientacao da testada: ~136 graus do norte (NW->SE), mesma da Quadra A
"""

import math, json, sqlite3, os

# ─── Parametros ────────────────────────────────────────────────
# Canto SW da Quadra A (do GeoJSON)
SW_A_LAT, SW_A_LNG = -11.80238649101106, -42.063718205853306

LARG_RUA_B = 12.6          # metros entre Quadra A e Quadra B
ANGULO_TESTADA = 136        # graus do norte (sentido horario): direcao da rua
PROFUNDIDADE = 30.0         # metros (frente->fundo do lote)
PROF_L1_L11 = 30.09         # profundidade dos lotes especiais
LARG_L1 = 14.7              # metros
LARG_L11 = 12.35            # metros
LARG_PADRAO = 10.0          # metros
N_FRENTE = 11               # lotes na Rua B (L1 a L11)
N_FUNDOS = 11               # lotes na Rua C (L12 a L22)

# Conversao: 1 grau lat = 111000m, 1 grau lng = 108660m (em -11.8)
M_POR_GRAU_LAT = 111000.0
M_POR_GRAU_LNG = 108660.0

def deslocar(lat, lng, ang_graus, dist_m):
    ang_rad = math.radians(ang_graus)
    dlat = math.cos(ang_rad) * dist_m / M_POR_GRAU_LAT
    dlng = math.sin(ang_rad) * dist_m / M_POR_GRAU_LNG
    return (lat + dlat, lng + dlng)

def retangulo_poly(canto_nw, ang_testada, largura_m, profundidade_m, ang_profundidade=None):
    if ang_profundidade is None:
        ang_profundidade = ang_testada + 90
    lat0, lng0 = canto_nw
    ne = deslocar(lat0, lng0, ang_testada, largura_m)
    se = deslocar(ne[0], ne[1], ang_profundidade, profundidade_m)
    sw = deslocar(lat0, lng0, ang_profundidade, profundidade_m)
    return [[lat0, lng0], [ne[0], ne[1]], [se[0], se[1]], [sw[0], sw[1]], [lat0, lng0]]

# ─── Referencia da Quadra B ────────────────────────────────────
# Canto SW da Quadra A + 12.6m na perpendicular (SW = 136+90=226 graus)
ang_perp_saida = ANGULO_TESTADA + 90  # 226 graus
REF_LAT, REF_LNG = deslocar(SW_A_LAT, SW_A_LNG, ang_perp_saida, LARG_RUA_B)
print(f"-> Canto NW da Quadra B (L1, Rua B): ({REF_LAT}, {REF_LNG})")
print()

# ─── Calculos ──────────────────────────────────────────────────
resultados = []

# Largura total da Rua B (11 lotes: L1=14.7, L2-L10=10x9, L11=12.35)
larg_total_frente = LARG_L1 + (N_FRENTE - 2) * LARG_PADRAO + LARG_L11
# = 14.7 + 9*10 + 12.35 = 14.7 + 90 + 12.35 = 117.05m
larg_total_fundos = N_FUNDOS * LARG_PADRAO  # 110m
prof_total = 2 * PROFUNDIDADE  # 60m

# Poligono da Quadra B
quadra_poly = retangulo_poly((REF_LAT, REF_LNG), ANGULO_TESTADA, larg_total_frente, prof_total)
print("-> Poligono da QUADRA B (quarteirao):")
print(json.dumps(quadra_poly, ensure_ascii=False))
print()

# ─── Fileira da RUA B (frente, 11 lotes: L1 a L11) ────────────
print("-> Fileira RUA B (frente):")
canto_atual = (REF_LAT, REF_LNG)
for i in range(N_FRENTE):
    if i == 0:
        larg = LARG_L1
        prof = PROF_L1_L11
    elif i == N_FRENTE - 1:
        larg = LARG_L11
        prof = PROF_L1_L11
    else:
        larg = LARG_PADRAO
        prof = PROFUNDIDADE
    numero = i + 1
    poly = retangulo_poly(canto_atual, ANGULO_TESTADA, larg, prof)
    resultados.append((numero, larg, prof, poly))
    print(f"  L{numero:02d} (larg={larg}m, prof={prof}m) -> NW=({canto_atual[0]:.9f}, {canto_atual[1]:.9f})")
    canto_atual = deslocar(canto_atual[0], canto_atual[1], ANGULO_TESTADA, larg)

# ─── Fileira da RUA C (fundos, 11 lotes: L12 a L22) ───────────
print("-> Fileira RUA C (fundos):")
# Canto na Rua C = REF + 60m na perpendicular (SW)
canto_fundos_base = deslocar(REF_LAT, REF_LNG, ang_perp_saida, prof_total)

# A Rua C tem 110m de testada (11 lotes de 10m), a Rua B tem 117.05m
# Centralizar: diferenca = 7.05m -> deslocamento = 3.525m
ang_profundidade = ANGULO_TESTADA - 90  # 46 graus: NE = para dentro do quarteirao
desloc_central = (larg_total_frente - larg_total_fundos) / 2  # 3.525m
canto_fundos = deslocar(canto_fundos_base[0], canto_fundos_base[1], ANGULO_TESTADA, desloc_central)

for i in range(N_FUNDOS):
    numero = N_FRENTE + i + 1  # L12 a L22
    poly = retangulo_poly(canto_fundos, ANGULO_TESTADA, LARG_PADRAO, PROFUNDIDADE, ang_profundidade)
    resultados.append((numero, LARG_PADRAO, PROFUNDIDADE, poly))
    print(f"  L{numero:02d} (larg={LARG_PADRAO}m) -> NW=({canto_fundos[0]:.9f}, {canto_fundos[1]:.9f})")
    canto_fundos = deslocar(canto_fundos[0], canto_fundos[1], ANGULO_TESTADA, LARG_PADRAO)

# ─── SQL ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SQL para atualizar banco:")
print("=" * 60)

print(f"\n-- polygon_coords da QUADRA B:")
print(f"UPDATE quadras SET polygon_coords = '{json.dumps(quadra_poly, ensure_ascii=False)}' WHERE id = 2;")

print(f"\n-- Deletar lotes existentes da Quadra B:")
print(f"DELETE FROM lotes WHERE quadra_id = 2;")

print(f"\n-- Inserir 22 lotes da Quadra B:")
for num, larg, prof, poly in resultados:
    num_str = str(num).zfill(2)
    poly_str = json.dumps(poly, ensure_ascii=False)
    print(f"INSERT INTO lotes (quadra_id, numero, tamanho_frente, tamanho_fundo, status, polygon_coords)")
    print(f"VALUES (2, '{num_str}', {larg}, {prof}, 'disponivel', '{poly_str}');")

# ─── GeoJSON para debug ────────────────────────────────────────
geojson = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"tipo": "quadra", "nome": "Quadra B"},
            "geometry": {"type": "Polygon", "coordinates": [quadra_poly]}
        }
    ]
}
for num, larg, prof, poly in resultados:
    geojson["features"].append({
        "type": "Feature",
        "properties": {"tipo": "lote", "numero": num, "largura": larg, "profundidade": prof},
        "geometry": {"type": "Polygon", "coordinates": [poly]}
    })

path = os.path.join(os.path.dirname(__file__) or ".", "quadra_b_debug.geojson")
with open(path, "w", encoding="utf-8") as f:
    json.dump(geojson, f, ensure_ascii=False, indent=2)
print(f"\nGeoJSON salvo em: {path}")
