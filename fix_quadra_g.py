import psycopg2, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:JOTrAQIxKtGFXDxpleXeegkVcgYwpXmn@reseau.proxy.rlwy.net:36883/railway")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

QID = 15

# 0. Add layout column if not exists
try:
    cur.execute("ALTER TABLE quadras ADD COLUMN layout TEXT DEFAULT 'horizontal'")
    print("Added layout column")
except Exception as e:
    print(f"Layout column already exists: {e}")

# 1. Delete all lots of Quadra G
cur.execute("DELETE FROM lotes WHERE quadra_id=%s", (QID,))
print(f"Deleted {cur.rowcount} lots")

# 2. Set layout to vertical
cur.execute("UPDATE quadras SET layout=%s WHERE id=%s", ("vertical", QID))
print("Set layout=vertical")

# 3. Recreate lots with correct numero (with leading zeros)
lotes_data = []
# Left side: 01-08
for i in range(8):
    num = f"{i+1:02d}"
    lotes_data.append((QID, num, 10, 10, 30, 30))
# Right side: 09-17
for i in range(9, 18):
    num = f"{i:02d}"
    lotes_data.append((QID, num, 10, 10, 30, 30))

for qid, num, frente, fundo, esq, dir_ in lotes_data:
    cur.execute("""
        INSERT INTO lotes (quadra_id, numero, tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'disponivel')
    """, (qid, num, frente, fundo, esq, dir_))

cur.execute("SELECT COUNT(*) FROM lotes WHERE quadra_id=%s", (QID,))
count = cur.fetchone()[0]
print(f"Inserted {count} lots")

# 4. Load the recalculo function from app.py
from app import recalcular_lotes_por_quadra

# We need a connection wrapper compatible with the app's get_db()
# The recalculo function uses conn.execute(sql, params) which expects
# a _PgConnection-like wrapper, not a raw psycopg2 connection.

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

# 5. Verify
cur.execute("SELECT numero FROM lotes WHERE quadra_id=%s ORDER BY CAST(numero AS integer)", (QID,))
nums = [r[0] for r in cur.fetchall()]
print(f"Lots in order: {nums}")

cur.execute("SELECT polygon_coords FROM quadras WHERE id=%s", (QID,))
qc = json.loads(cur.fetchone()[0])
print(f"Quadra polygon: {qc}")

cur.execute("SELECT numero, polygon_coords FROM lotes WHERE quadra_id=%s ORDER BY CAST(numero AS integer)", (QID,))
print("Lot positions:")
for num, poly_str in cur.fetchall():
    coords = json.loads(poly_str)
    lats = [p[0] for p in coords]
    lngs = [p[1] for p in coords]
    max_lat = max(lats)
    min_lat = min(lats)
    mean_lat = (max_lat + min_lat) / 2
    print(f"  G{num}: lat_center={mean_lat:.6f}, topo={max_lat:.6f}")

conn.close()
print("Done!")
