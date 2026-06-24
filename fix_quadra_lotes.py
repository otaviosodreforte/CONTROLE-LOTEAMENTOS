"""Fix Quadra A lot positions - insert 23 lotes with correct geometry."""
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
    if angp is None: angp = ang + 90
    ne = desl(lat, lng, ang, larg)
    se = desl(ne[0], ne[1], angp, prof)
    sw = desl(lat, lng, angp, prof)
    return json.dumps([[lat,lng],[ne[0],ne[1]],[se[0],se[1]],[sw[0],sw[1]],[lat,lng]], ensure_ascii=False)

larg_frente = LARG_L1 + 11*LARG_P
larg_fundos = 11*LARG_P
prof_total = 2*PROF

conn = sqlite3.connect(DB)
c = conn.cursor()

# Quadra polygon
c.execute("UPDATE quadras SET polygon_coords = ? WHERE id = 1",
          (rect(REF_LAT, REF_LNG, ANG, larg_frente, prof_total),))
c.execute("DELETE FROM lotes WHERE quadra_id = 1")

# Frente (L1-L12)
lat, lng = REF_LAT, REF_LNG
for i in range(12):
    larg = LARG_L1 if i == 0 else LARG_P
    num = f"{i+1:02d}"
    fundo = 30.12 if i == 0 else PROF
    poly = rect(lat, lng, ANG, larg, PROF)
    c.execute("INSERT INTO lotes (quadra_id,numero,tamanho_frente,tamanho_fundo,status,polygon_coords) VALUES (1,?,?,?,?,?)",
              (num, larg, fundo, "disponivel", poly))
    lat, lng = desl(lat, lng, ANG, larg)

# Fundos (L13-L23) - 60m da rua da frente, estendendo NE em direcao a rua da frente
blat, blng = desl(REF_LAT, REF_LNG, ANG+90, prof_total)
offset = (larg_frente - larg_fundos) / 2
blat, blng = desl(blat, blng, ANG, offset)
for i in range(11):
    num = f"{i+13:02d}"
    poly = rect(blat, blng, ANG, LARG_P, PROF, ANG-90)  # NE direction
    c.execute("INSERT INTO lotes (quadra_id,numero,tamanho_frente,tamanho_fundo,status,polygon_coords) VALUES (1,?,?,?,?,?)",
              (num, LARG_P, PROF, "disponivel", poly))
    blat, blng = desl(blat, blng, ANG, LARG_P)

conn.commit()

# Verify
c.execute("SELECT count(*) FROM lotes WHERE quadra_id=1")
print(f"{c.fetchone()[0]} lotes inseridos")

c.execute("SELECT numero, polygon_coords FROM lotes WHERE quadra_id=1 AND numero IN ('01','12','13','23') ORDER BY numero")
for r in c.fetchall():
    p = json.loads(r[1])
    clat = sum(v[0] for v in p[:4]) / 4
    clng = sum(v[1] for v in p[:4]) / 4
    print(f"  L{r[0]}: centro=({clat:.6f}, {clng:.6f})")

conn.close()
