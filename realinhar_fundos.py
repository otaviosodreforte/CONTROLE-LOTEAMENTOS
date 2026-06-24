"""Alinha fileira de fundos (L13-L23) a direita da Quadra A."""
import sqlite3, os, json, math

DB = os.path.join(os.path.dirname(__file__) or ".", "controle_loteamentos.db")
REF_LAT, REF_LNG = -11.802011, -42.063321
ANG = 136
PROF = 30.0
LARG_L1, LARG_P = 11.3, 10.0
MPLAT, MPLNG = 111000.0, 108660.0

def desl(lat, lng, ang, dist):
    r = math.radians(ang)
    return (lat + math.cos(r)*dist/MPLAT, lng + math.sin(r)*dist/MPLNG)

def rect(lat, lng, ang, larg, prof, angp=None):
    if angp is None:
        angp = ang + 90
    ne = desl(lat, lng, ang, larg)
    se = desl(ne[0], ne[1], angp, prof)
    sw = desl(lat, lng, angp, prof)
    return json.dumps([[lat,lng],[ne[0],ne[1]],[se[0],se[1]],[sw[0],sw[1]],[lat,lng]], ensure_ascii=False)

larg_frente = LARG_L1 + 11 * LARG_P  # 121.3
larg_fundos = 11 * LARG_P            # 110
prof_total = 2 * PROF                # 60

conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("UPDATE quadras SET polygon_coords=? WHERE id=1",
          (rect(REF_LAT, REF_LNG, ANG, larg_frente, prof_total),))
c.execute("DELETE FROM lotes WHERE quadra_id=1")

# Frente L1-L12
lat, lng = REF_LAT, REF_LNG
for i in range(12):
    larg = LARG_L1 if i == 0 else LARG_P
    num = str(i+1).zfill(2)
    fundo = 30.12 if i == 0 else PROF
    poly = rect(lat, lng, ANG, larg, PROF)
    c.execute("INSERT INTO lotes (quadra_id,numero,tamanho_frente,tamanho_fundo,status,polygon_coords) VALUES (1,?,?,?,?,?)",
              (num, larg, fundo, "disponivel", poly))
    lat, lng = desl(lat, lng, ANG, larg)

# Fundos L13-L23 alinhados a DIREITA (comecam apos o L1, sem centralizar)
blat, blng = desl(REF_LAT, REF_LNG, ANG+90, prof_total)  # rua de tras
blat, blng = desl(blat, blng, ANG, LARG_L1)  # alinhado ao final do L1
for i in range(11):
    num = str(i+13).zfill(2)
    poly = rect(blat, blng, ANG, LARG_P, PROF, ANG-90)  # NE em direcao a rua da frente
    c.execute("INSERT INTO lotes (quadra_id,numero,tamanho_frente,tamanho_fundo,status,polygon_coords) VALUES (1,?,?,?,?,?)",
              (num, LARG_P, PROF, "disponivel", poly))
    blat, blng = desl(blat, blng, ANG, LARG_P)

conn.commit()

# Verificar
c.execute("SELECT count(*) FROM lotes WHERE quadra_id=1")
print(f"{c.fetchone()[0]} lotes inseridos")

c.execute("SELECT numero, polygon_coords FROM lotes WHERE quadra_id=1 ORDER BY CAST(numero AS INTEGER)")
for r in c.fetchall():
    p = json.loads(r[1])
    clat = sum(v[0] for v in p[:4])/4
    clng = sum(v[1] for v in p[:4])/4
    print(f"  L{r[0]}: centro=({clat:.6f}, {clng:.6f})")

conn.close()
