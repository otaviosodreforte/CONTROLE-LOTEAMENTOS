import psycopg2, json
conn = psycopg2.connect('postgresql://postgres:JOTrAQIxKtGFXDxpleXeegkVcgYwpXmn@reseau.proxy.rlwy.net:36883/railway')
cur = conn.cursor()
cur.execute('SELECT numero, polygon_coords FROM lotes WHERE quadra_id=15 ORDER BY CAST(numero AS integer)')
rows = cur.fetchall()
for num, poly_str in rows:
    coords = json.loads(poly_str)
    lats = [p[0] for p in coords]
    max_lat = max(lats)
    lado = 'ESQ' if int(num) <= 8 else 'DIR'
    print(f'G{num:>2} ({lado}): topo lat {max_lat:.6f}')
conn.close()
