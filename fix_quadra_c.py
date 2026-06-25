import psycopg2, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:JOTrAQIxKtGFXDxpleXeegkVcgYwpXmn@reseau.proxy.rlwy.net:36883/railway")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

QID = 16

# 0. Set layout to custom
cur.execute("UPDATE quadras SET layout=%s WHERE id=%s", ("custom", QID))
print("Set layout=custom")

# 1. Delete all lots of Quadra C
cur.execute("DELETE FROM lotes WHERE quadra_id=%s", (QID,))
print(f"Deleted {cur.rowcount} lots")

# 2. Recreate 4 lots with dimensions
lotes_data = [
    (QID, "01", 29.0, 0, 46.25, 37.05),
    (QID, "02", 10.0, 10.21, 37.05, 35.01),
    (QID, "03", 10.0, 10.21, 35.01, 32.97),
    (QID, "04", 10.0, 10.21, 32.97, 30.0),
]

for qid, num, frente, fundo, esq, dir_ in lotes_data:
    cur.execute("""
        INSERT INTO lotes (quadra_id, numero, tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'disponivel')
    """, (qid, num, frente, fundo, esq, dir_))

cur.execute("SELECT COUNT(*) FROM lotes WHERE quadra_id=%s", (QID,))
count = cur.fetchone()[0]
print(f"Inserted {count} lots")

# 3. Run recalculo
from app import recalcular_lotes_por_quadra

class PgWrapper:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=None):
        from psycopg2.extras import RealDictCursor
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql.replace("?", "%s"), params or ())
        return cur

wrapper = PgWrapper(conn)
updated = recalcular_lotes_por_quadra(wrapper, QID)
print(f"Recalculo completed: {updated} lots updated")

# 4. Verify
cur.execute("SELECT numero, polygon_coords FROM lotes WHERE quadra_id=%s ORDER BY CAST(numero AS integer)", (QID,))
print("Lot polygons:")
for num, poly_str in cur.fetchall():
    coords = json.loads(poly_str)
    print(f"  L{num}: {len(coords)} vertices, {json.dumps(coords)}")

conn.close()
print("Done!")
