import json
import math
import re
import os
import sys
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from modules.common import get_db, login_required, permissao_required, DB_PATH, RECIBOS_DIR, USING_PG, _init_pg

def gerar_poligono_trapezio(frente, fundo, esquerda, direita, coords_atuais):
    if not coords_atuais or len(coords_atuais) < 4:
        return coords_atuais
    if not (frente and fundo):
        return coords_atuais
    p1, p2, p3, p4 = coords_atuais[:4]
    lat0 = (p1[0] + p2[0] + p3[0] + p4[0]) / 4
    cos_lat = math.cos(math.radians(lat0))
    dlat = p3[0] - p2[0]
    dlng = p3[1] - p2[1]
    depth_m = math.sqrt((dlat * 111000)**2 + (dlng * 111000 * cos_lat)**2)
    if depth_m < 0.001:
        return coords_atuais
    du_lat = dlat / depth_m
    du_lng = dlng / depth_m
    wu_lat = -dlng * cos_lat / depth_m
    wu_lng = dlat / (depth_m * cos_lat)
    esq = esquerda or depth_m
    dire = direita or depth_m
    mf_lat = (p1[0] + p2[0]) / 2
    mf_lng = (p1[1] + p2[1]) / 2
    p1n_lat = mf_lat + wu_lat * (frente / 2)
    p1n_lng = mf_lng + wu_lng * (frente / 2)
    p2n_lat = mf_lat - wu_lat * (frente / 2)
    p2n_lng = mf_lng - wu_lng * (frente / 2)
    p4n_lat = p1n_lat + du_lat * esq
    p4n_lng = p1n_lng + du_lng * esq
    p3n_lat = p2n_lat + du_lat * dire
    p3n_lng = p2n_lng + du_lng * dire
    return [[p1n_lat, p1n_lng], [p2n_lat, p2n_lng], [p3n_lat, p3n_lng], [p4n_lat, p4n_lng]]


def recalcular_lotes_por_quadra(conn, quadra_id):
    q = conn.execute("SELECT polygon_coords, layout FROM quadras WHERE id=?", (quadra_id,)).fetchone()
    if not q:
        return 0
    layout = (q["layout"] or "horizontal").strip().lower()
    coords = json.loads(q["polygon_coords"] or "[]")
    if len(coords) < 4:
        return 0
    tl, tr, br, bl = coords[0], coords[1], coords[2], coords[3]

    def interp(a, b, t):
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]

    lots = conn.execute("SELECT id, numero FROM lotes WHERE quadra_id=? ORDER BY CAST(numero AS INTEGER)", (quadra_id,)).fetchall()
    count = len(lots)
    if count == 0:
        return 0

    if layout == "vertical":
        ct = interp(tl, tr, 0.5)
        cb = interp(bl, br, 0.5)
        left_north, left_south = (bl, tl) if bl[0] > tl[0] else (tl, bl)
        right_north, right_south = (br, tr) if br[0] > tr[0] else (tr, br)
        center_north, center_south = (cb, ct) if cb[0] > ct[0] else (ct, cb)
        left_count = count // 2
        updated = 0
        for i, row in enumerate(lots):
            if i < left_count:
                t = i / left_count if left_count > 0 else 0
                t_next = (i + 1) / left_count if left_count > 0 else 1
                fl = interp(left_north, left_south, t)
                fr = interp(left_north, left_south, t_next)
                bc_l = interp(center_north, center_south, t)
                bc_r = interp(center_north, center_south, t_next)
            else:
                bi = i - left_count
                right_count = count - left_count
                t = bi / right_count if right_count > 0 else 0
                t_next = (bi + 1) / right_count if right_count > 0 else 1
                fl = interp(right_north, right_south, t)
                fr = interp(right_north, right_south, t_next)
                bc_l = interp(center_north, center_south, t)
                bc_r = interp(center_north, center_south, t_next)
            poly = [fl, fr, bc_r, bc_l]
            conn.execute("UPDATE lotes SET polygon_coords=? WHERE id=?", (json.dumps(poly), row["id"]))
            updated += 1
        return updated

    cl = interp(tl, bl, 0.5)
    cr = interp(tr, br, 0.5)
    top_count = (count + 1) // 2
    updated = 0
    for i, row in enumerate(lots):
        if i < top_count:
            t = i / top_count
            t_next = (i + 1) / top_count
            fl = interp(tl, tr, t)
            fr = interp(tl, tr, t_next)
            bc_l = interp(cl, cr, t)
            bc_r = interp(cl, cr, t_next)
        else:
            bi = i - top_count
            bcount = count - top_count
            t = bi / bcount if bcount > 0 else 0
            t_next = (bi + 1) / bcount if bcount > 0 else 1
            fl = interp(bl, br, t)
            fr = interp(bl, br, t_next)
            bc_l = interp(cl, cr, t)
            bc_r = interp(cl, cr, t_next)
        poly = [fl, fr, bc_r, bc_l]
        conn.execute("UPDATE lotes SET polygon_coords=? WHERE id=?", (json.dumps(poly), row["id"]))
        updated += 1
    return updated




app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "controle-loteamentos-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
UPLOAD_FOLDER = os.path.join(app.static_folder, "croquis")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

TELAS = {
    "dashboard": "Dashboard",
    "loteamentos": "Loteamentos",
    "quadras": "Quadras",
    "lotes": "Lotes",
    "permutas": "Permutas",
    "vendas": "Vendas",
    "usuarios": "Usuários",
}

@app.context_processor
def inject_globals():
    loteamento_ativo = None
    loteamentos_usuario = []
    if "usuario_id" in session:
        with get_db() as conn:
            lids = session.get("loteamentos_ids", [])
            if lids:
                placeholders = ",".join("?" for _ in lids)
                loteamentos_usuario = conn.execute(
                    f"SELECT * FROM loteamentos WHERE id IN ({placeholders}) ORDER BY nome", lids
                ).fetchall()
            la_id = session.get("loteamento_ativo")
            if la_id:
                loteamento_ativo = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la_id,)).fetchone()
    return dict(TELAS=TELAS, loteamento_ativo=loteamento_ativo, loteamentos_usuario=loteamentos_usuario)

ENDPOINT_MODULO = {}
for mod, endpoints in [
    ("loteamento", ["loteamentos_lista", "loteamentos_novo", "loteamentos_editar", "loteamentos_excluir",         "loteamentos_mapa", "api_lotes_geo"]),
    ("quadras", ["quadras_lista", "quadras_novo", "quadras_editar", "quadras_excluir"]),
    ("lotes", ["lotes_lista", "lotes_novo", "lotes_editar", "lotes_excluir"]),
    ("permutas", ["permutas_lista", "permutas_novo", "permutas_excluir", "permutas_recibo"]),
    ("vendas", ["vendas_lista", "vendas_nova", "vendas_editar", "vendas_excluir", "vendas_recibo", "pagamentos_lista", "pagamentos_editar", "pagamentos_recibo"]),
    ("usuarios", ["usuarios_lista", "usuarios_novo", "usuarios_editar"]),
    ("pessoas", ["pessoas_lista", "pessoas_novo", "pessoas_editar", "pessoas_excluir", "pessoas_buscar"]),
]:
    for ep in endpoints:
        ENDPOINT_MODULO[ep] = mod




def init_db():
    with get_db() as conn:
        if USING_PG:
            _init_pg(conn)
        else:
            from modules.common import _init_sqlite
            _init_sqlite(conn)

        admin = conn.execute("SELECT id FROM usuarios WHERE username=?", ("admin",)).fetchone()
        if not admin:
            conn.execute("INSERT INTO usuarios (username, password_hash) VALUES (?, ?) RETURNING id",
                         ("admin", generate_password_hash("admin")))
            admin = conn.execute("SELECT id FROM usuarios WHERE username=?", ("admin",)).fetchone()
        conn.execute("DELETE FROM permissoes WHERE usuario_id=?", (admin["id"],))
        conn.execute("INSERT INTO permissoes (usuario_id, modulo) VALUES (?, ?) ON CONFLICT DO NOTHING", (admin["id"], "usuarios"))
        conn.execute("INSERT INTO permissoes (usuario_id, modulo) VALUES (?, ?) ON CONFLICT DO NOTHING", (admin["id"], "loteamentos_admin"))
        conn.execute("INSERT INTO permissoes (usuario_id, modulo) VALUES (?, ?) ON CONFLICT DO NOTHING", (admin["id"], "pessoas"))
        for lot in conn.execute("SELECT id FROM loteamentos").fetchall():
            conn.execute("INSERT INTO usuario_loteamentos (usuario_id, loteamento_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (admin["id"], lot["id"]))
        for fp in ("À vista", "Parcelado", "Financiamento", "Permuta"):
            conn.execute("INSERT INTO formas_pagamento (nome) VALUES (?) ON CONFLICT DO NOTHING", (fp,))
        try:
            conn.execute("ALTER TABLE lotes ADD COLUMN tamanho_esquerda DOUBLE PRECISION DEFAULT 0")
        except Exception as e:
            print(f"[migracao] tamanho_esquerda ignorado: {e}")
        try:
            conn.execute("ALTER TABLE lotes ADD COLUMN tamanho_direita DOUBLE PRECISION DEFAULT 0")
        except Exception as e:
            print(f"[migracao] tamanho_direita ignorado: {e}")
        try:
            conn.execute("ALTER TABLE quadras ADD COLUMN layout TEXT DEFAULT 'horizontal'")
        except Exception as e:
            print(f"[migracao] layout ignorado: {e}")



init_db()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM usuarios WHERE username=?", (username,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session["usuario_id"] = user["id"]
                session["usuario_nome"] = user["username"]
                perms = conn.execute("SELECT modulo FROM permissoes WHERE usuario_id=?", (user["id"],)).fetchall()
                session["permissoes"] = [p["modulo"] for p in perms]
                lot_ids = conn.execute("SELECT loteamento_id FROM usuario_loteamentos WHERE usuario_id=?", (user["id"],)).fetchall()
                session["loteamentos_ids"] = [r["loteamento_id"] for r in lot_ids]
                session["loteamento_ativo"] = None
                return redirect(url_for("selecionar_loteamento"))
        return render_template("login.html", erro="Usuário ou senha inválidos.")
    return render_template("login.html", erro=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.before_request
def verificar_acesso():
    if request.endpoint and request.endpoint != "static":
        modulo = ENDPOINT_MODULO.get(request.endpoint)
        if modulo:
            if "usuario_id" not in session:
                return redirect(url_for("login"))
            if modulo == "usuarios":
                if "usuarios" not in session.get("permissoes", []):
                    flash("Acesso negado.")
                    return redirect(url_for("index"))
            elif modulo == "loteamento":
                tem_acesso = bool(session.get("loteamentos_ids"))
                tem_admin = "loteamentos_admin" in session.get("permissoes", [])
                if not tem_acesso and not tem_admin:
                    flash("Acesso negado.")
                    return redirect(url_for("index"))
            elif modulo in ("quadras", "lotes", "permutas", "vendas"):
                if not session.get("loteamento_ativo"):
                    return redirect(url_for("selecionar_loteamento"))


@app.route("/")
@login_required
def index():
    if session.get("loteamento_ativo"):
        return redirect(url_for("dashboard_loteamento", id=session["loteamento_ativo"]))
    return redirect(url_for("selecionar_loteamento"))


@app.route("/selecionar")
@login_required
def selecionar_loteamento():
    lids = session.get("loteamentos_ids", [])
    tem_admin = "loteamentos_admin" in session.get("permissoes", [])
    with get_db() as conn:
        if tem_admin:
            loteamentos = conn.execute("SELECT * FROM loteamentos ORDER BY nome").fetchall()
        elif lids:
            placeholders = ",".join("?" for _ in lids)
            loteamentos = conn.execute(f"SELECT * FROM loteamentos WHERE id IN ({placeholders}) ORDER BY nome", lids).fetchall()
        else:
            loteamentos = []
    return render_template("selecionar.html", loteamentos=loteamentos)


@app.route("/selecionar/<int:id>")
@login_required
def set_loteamento(id):
    lids = session.get("loteamentos_ids", [])
    tem_admin = "loteamentos_admin" in session.get("permissoes", [])
    if tem_admin or id in lids:
        session["loteamento_ativo"] = id
        return redirect(url_for("dashboard_loteamento", id=id))
    flash("Acesso negado a este loteamento.")
    return redirect(url_for("selecionar_loteamento"))


@app.route("/loteamento/<int:id>")
@login_required
def dashboard_loteamento(id):
    lids = session.get("loteamentos_ids", [])
    tem_admin = "loteamentos_admin" in session.get("permissoes", [])
    if not tem_admin and id not in lids:
        flash("Acesso negado.")
        return redirect(url_for("selecionar_loteamento"))
    session["loteamento_ativo"] = id
    with get_db() as conn:
        loteamento = conn.execute("SELECT * FROM loteamentos WHERE id=?", (id,)).fetchone()
        if not loteamento:
            flash("Loteamento não encontrado.")
            return redirect(url_for("selecionar_loteamento"))
        total_quadras = conn.execute("SELECT COUNT(*) as c FROM quadras WHERE loteamento_id=?", (id,)).fetchone()["c"]
        total_lotes = conn.execute("""SELECT COUNT(*) as c FROM lotes l
                                      JOIN quadras q ON q.id=l.quadra_id
                                      WHERE q.loteamento_id=?""", (id,)).fetchone()["c"]
        lotes_disponiveis = conn.execute("""SELECT COUNT(*) as c FROM lotes l
                                            JOIN quadras q ON q.id=l.quadra_id
                                            WHERE q.loteamento_id=? AND l.status='disponivel'""", (id,)).fetchone()["c"]
        lotes_vendidos = conn.execute("""SELECT COUNT(*) as c FROM lotes l
                                         JOIN quadras q ON q.id=l.quadra_id
                                         WHERE q.loteamento_id=? AND l.status='vendido'""", (id,)).fetchone()["c"]
        lotes_permutados = conn.execute("""SELECT COUNT(*) as c FROM lotes l
                                           JOIN quadras q ON q.id=l.quadra_id
                                           WHERE q.loteamento_id=? AND l.status='permutado'""", (id,)).fetchone()["c"]
        total_vendas = conn.execute("""SELECT COUNT(*) as c FROM vendas v
                                       JOIN lotes l ON l.id=v.lote_id
                                       JOIN quadras q ON q.id=l.quadra_id
                                       WHERE q.loteamento_id=?""", (id,)).fetchone()["c"]
    return render_template("dashboard_loteamento.html", **locals())


@app.template_filter("fmt_data")
def fmt_data(val):
    if not val:
        return "\u2014"
    try:
        d = datetime.strptime(val, "%Y-%m-%d")
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return val


@app.template_filter("fmt_brl")
def fmt_brl(val):
    if val is None:
        return "\u2014"
    return f"R$ {val:_.2f}".replace(".", ",").replace("_", ".")


@app.template_filter("fmt_cpf")
def fmt_cpf(val):
    if not val or len(val) != 11:
        return val or "\u2014"
    return f"{val[:3]}.{val[3:6]}.{val[6:9]}-{val[9:]}"




# ─── LOTEAMENTOS ──────────────────────────────────────────────

@app.route("/loteamentos")
@login_required
def loteamentos_lista():
    busca = request.args.get("busca", "")
    lids = session.get("loteamentos_ids", [])
    tem_admin = "loteamentos_admin" in session.get("permissoes", [])
    with get_db() as conn:
        filtro = ""
        params = []
        if busca:
            filtro = "WHERE l.nome LIKE ?"
            params.append(f"%{busca}%")
        if not tem_admin and lids:
            placeholders = ",".join("?" for _ in lids)
            cond = f"l.id IN ({placeholders})"
            params.extend(lids)
            if filtro:
                filtro += f" AND {cond}"
            else:
                filtro = f"WHERE {cond}"
        rows = conn.execute(f"""
            SELECT l.*,
                (SELECT COUNT(*) FROM quadras WHERE loteamento_id=l.id) as total_quadras,
                (SELECT COUNT(*) FROM lotes WHERE quadra_id IN (SELECT id FROM quadras WHERE loteamento_id=l.id)) as total_lotes
            FROM loteamentos l {filtro} ORDER BY l.nome
        """, params).fetchall()
    return render_template("loteamentos/lista.html", registros=rows, busca=busca)


@app.route("/loteamentos/novo", methods=["GET", "POST"])
@login_required
def loteamentos_novo():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        endereco = request.form.get("endereco", "").strip()
        lat = request.form.get("lat") or None
        lng = request.form.get("lng") or None
        if not nome:
            return render_template("loteamentos/form.html", registro=None, erro="Nome é obrigatório.")
        with get_db() as conn:
            lot_id = conn.execute("INSERT INTO loteamentos (nome, endereco, lat, lng) VALUES (?, ?, ?, ?) RETURNING id",
                         (nome, endereco, lat, lng)).fetchone()["id"]
            conn.execute("INSERT INTO usuario_loteamentos (usuario_id, loteamento_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                         (session["usuario_id"], lot_id))
            lids = session.get("loteamentos_ids", [])
            lids.append(lot_id)
            session["loteamentos_ids"] = lids
        return redirect(url_for("loteamentos_lista"))
    return render_template("loteamentos/form.html", registro=None, erro=None)


@app.route("/loteamentos/editar/<int:id>", methods=["GET", "POST"])
@login_required
def loteamentos_editar(id):
    with get_db() as conn:
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            endereco = request.form.get("endereco", "").strip()
            lat = request.form.get("lat") or None
            lng = request.form.get("lng") or None
            if not nome:
                reg = conn.execute("SELECT * FROM loteamentos WHERE id=?", (id,)).fetchone()
                return render_template("loteamentos/form.html", registro=reg, erro="Nome é obrigatório.")
            conn.execute("UPDATE loteamentos SET nome=?, endereco=?, lat=?, lng=? WHERE id=?",
                         (nome, endereco, lat, lng, id))
            return redirect(url_for("loteamentos_lista"))
        reg = conn.execute("SELECT * FROM loteamentos WHERE id=?", (id,)).fetchone()
    return render_template("loteamentos/form.html", registro=reg, erro=None)


@app.route("/loteamentos/excluir/<int:id>")
@login_required
def loteamentos_excluir(id):
    with get_db() as conn:
        conn.execute("DELETE FROM loteamentos WHERE id=?", (id,))
    return redirect(url_for("loteamentos_lista"))


@app.route("/loteamentos/mapa/<int:id>")
@login_required
def loteamentos_mapa(id):
    lids = session.get("loteamentos_ids", [])
    tem_admin = "loteamentos_admin" in session.get("permissoes", [])
    if not tem_admin and id not in lids:
        flash("Acesso negado.")
        return redirect(url_for("selecionar_loteamento"))
    session["loteamento_ativo"] = id
    with get_db() as conn:
        loteamento = conn.execute("SELECT * FROM loteamentos WHERE id=?", (id,)).fetchone()
        quadras = conn.execute("SELECT * FROM quadras WHERE loteamento_id=? ORDER BY identificacao", (id,)).fetchall()
        lotes = conn.execute("""
            SELECT l.*, q.identificacao as quadra_nome
            FROM lotes l JOIN quadras q ON q.id = l.quadra_id
            WHERE q.loteamento_id=? ORDER BY q.identificacao, CAST(l.numero AS INTEGER)
        """, (id,)).fetchall()
    return render_template("loteamentos/mapa.html", loteamento=loteamento, quadras=quadras, lotes=lotes)


# ─── API MAPA ─────────────────────────────────────────────────

@app.route("/api/quadras/nova", methods=["POST"])
@login_required
def api_quadras_nova():
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    identificacao = data.get("identificacao", f"Quadra {uuid.uuid4().hex[:6]}").strip()
    polygon_coords = json.dumps(data.get("polygon_coords", []))
    with get_db() as conn:
        qid = conn.execute("INSERT INTO quadras (loteamento_id, identificacao, polygon_coords) VALUES (?, ?, ?) RETURNING id",
                     (la, identificacao, polygon_coords)).fetchone()["id"]
        conn.execute("UPDATE loteamentos SET qtd_quadras=(SELECT COUNT(*) FROM quadras WHERE loteamento_id=?) WHERE id=?", (la, la))
        quadra = conn.execute("SELECT * FROM quadras WHERE id=?", (qid,)).fetchone()
    return jsonify({"id": qid, "identificacao": quadra["identificacao"], "polygon_coords": quadra["polygon_coords"]})


@app.route("/api/quadras/<int:id>/polygon", methods=["GET", "PUT"])
@login_required
def api_quadras_polygon(id):
    if request.method == "GET":
        with get_db() as conn:
            q = conn.execute("SELECT polygon_coords FROM quadras WHERE id=?", (id,)).fetchone()
            if not q:
                return jsonify({"erro": "Quadra não encontrada"}), 404
            return jsonify({"polygon_coords": q["polygon_coords"] or "[]"})
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    polygon_coords = json.dumps(data.get("polygon_coords", []))
    with get_db() as conn:
        conn.execute("UPDATE quadras SET polygon_coords=? WHERE id=? AND loteamento_id=?", (polygon_coords, id, la))
        updated = recalcular_lotes_por_quadra(conn, id)
    return jsonify({"ok": True, "lotes_atualizados": updated})


@app.route("/api/quadras/<int:id>/recalcular-lotes", methods=["POST"])
@login_required
def api_quadras_recalcular_lotes(id):
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    with get_db() as conn:
        updated = recalcular_lotes_por_quadra(conn, id)
    return jsonify({"ok": True, "atualizados": updated})


@app.route("/api/quadras/<int:id>/mover", methods=["PUT"])
@login_required
def api_quadras_mover(id):
    """Move toda a quadra + seus lotes por (dlat, dlng)."""
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    dlat, dlng = float(data.get("dlat", 0)), float(data.get("dlng", 0))
    if dlat == 0 and dlng == 0:
        return jsonify({"ok": True, "atualizados": 0})
    with get_db() as conn:
        q = conn.execute("SELECT polygon_coords FROM quadras WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if not q:
            return jsonify({"erro": "Quadra nao encontrada"}), 404
        qcoords = json.loads(q["polygon_coords"] or "[]")
        qcoords = [[p[0] + dlat, p[1] + dlng] for p in qcoords]
        conn.execute("UPDATE quadras SET polygon_coords=? WHERE id=?", (json.dumps(qcoords), id))
        lotes = conn.execute("SELECT id, polygon_coords FROM lotes WHERE quadra_id=?", (id,)).fetchall()
        for l in lotes:
            lcoords = json.loads(l["polygon_coords"] or "[]")
            lcoords = [[p[0] + dlat, p[1] + dlng] for p in lcoords]
            conn.execute("UPDATE lotes SET polygon_coords=? WHERE id=?", (json.dumps(lcoords), l["id"]))
    return jsonify({"ok": True, "dlat": dlat, "dlng": dlng, "atualizados": 1 + len(lotes)})


@app.route("/api/quadras/<int:id>/rotacionar", methods=["PUT"])
@login_required
def api_quadras_rotacionar(id):
    """Rotaciona quadra + lotes em torno do centro por `angulo` graus."""
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    ang_deg = float(data.get("angulo", 0))
    if abs(ang_deg) < 0.01:
        return jsonify({"ok": True, "atualizados": 0})
    ang_rad = math.radians(ang_deg)
    cos_a, sin_a = math.cos(ang_rad), math.sin(ang_rad)
    m_lat, m_lng = 111000.0, 108660.0  # correcao -11.8 deg

    def rotacionar(coords, cx_lat, cx_lng):
        novos = []
        for p in coords:
            dx = (p[1] - cx_lng) * m_lng
            dy = (p[0] - cx_lat) * m_lat
            nx = dx * cos_a - dy * sin_a
            ny = dx * sin_a + dy * cos_a
            novos.append([cx_lat + ny / m_lat, cx_lng + nx / m_lng])
        return novos

    with get_db() as conn:
        q = conn.execute("SELECT polygon_coords FROM quadras WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if not q:
            return jsonify({"erro": "Quadra nao encontrada"}), 404
        qcoords = json.loads(q["polygon_coords"] or "[]")
        if not qcoords:
            return jsonify({"erro": "Quadra sem poligono"}), 400
        cx_lat = sum(p[0] for p in qcoords[:4]) / 4
        cx_lng = sum(p[1] for p in qcoords[:4]) / 4
        qcoords = rotacionar(qcoords, cx_lat, cx_lng)
        conn.execute("UPDATE quadras SET polygon_coords=? WHERE id=?", (json.dumps(qcoords), id))
        lotes = conn.execute("SELECT id, polygon_coords FROM lotes WHERE quadra_id=?", (id,)).fetchall()
        for l in lotes:
            lcoords = json.loads(l["polygon_coords"] or "[]")
            if lcoords:
                lcoords = rotacionar(lcoords, cx_lat, cx_lng)
                conn.execute("UPDATE lotes SET polygon_coords=? WHERE id=?", (json.dumps(lcoords), l["id"]))
    return jsonify({"ok": True, "angulo": ang_deg, "atualizados": 1 + len(lotes)})


@app.route("/api/lotes/novo", methods=["POST"])
@login_required
def api_lotes_novo():
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    quadra_id = data.get("quadra_id")
    polygon_coords = json.dumps(data.get("polygon_coords", []))
    if not quadra_id:
        return jsonify({"erro": "quadra_id é obrigatório"}), 400
    with get_db() as conn:
        q = conn.execute("SELECT id FROM quadras WHERE id=? AND loteamento_id=?", (quadra_id, la)).fetchone()
        if not q:
            return jsonify({"erro": "Quadra não encontrada"}), 404
        lid = conn.execute("INSERT INTO lotes (quadra_id, polygon_coords) VALUES (?, ?) RETURNING id", (quadra_id, polygon_coords)).fetchone()["id"]
        lote = conn.execute("SELECT * FROM lotes WHERE id=?", (lid,)).fetchone()
    return jsonify({"id": lid, "quadra_id": lote["quadra_id"], "polygon_coords": lote["polygon_coords"]})


@app.route("/api/lotes/<int:id>/polygon", methods=["PUT"])
@login_required
def api_lotes_polygon(id):
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    polygon_coords = json.dumps(data.get("polygon_coords", []))
    with get_db() as conn:
        conn.execute("""
            UPDATE lotes SET polygon_coords=? WHERE id=?
            AND quadra_id IN (SELECT id FROM quadras WHERE loteamento_id=?)
        """, (polygon_coords, id, la))
    return jsonify({"ok": True})


@app.route("/api/mapa/dados")
@login_required
def api_mapa_dados():
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"quadras": [], "lotes": []})
    with get_db() as conn:
        quadras = conn.execute("""
            SELECT q.*, COUNT(l.id) as total_lotes,
                   COALESCE(SUM(CASE WHEN l.status='disponivel' THEN 1 ELSE 0 END), 0) as disponiveis,
                   COALESCE(SUM(CASE WHEN l.status='vendido' THEN 1 ELSE 0 END), 0) as vendidos
            FROM quadras q
            LEFT JOIN lotes l ON l.quadra_id=q.id
            WHERE q.loteamento_id=?
            GROUP BY q.id ORDER BY q.identificacao
        """, (la,)).fetchall()
        lotes = conn.execute("""
            SELECT l.*, q.identificacao as quadra_nome, p.referencia_marcacao as dono_ref
            FROM lotes l
            JOIN quadras q ON q.id=l.quadra_id
            LEFT JOIN pessoas p ON p.id=l.dono_pessoa_id
            WHERE q.loteamento_id=?
            ORDER BY q.identificacao, CAST(l.numero AS INTEGER)
        """, (la,)).fetchall()
    return jsonify({
        "quadras": [dict(q) for q in quadras],
        "lotes": [dict(l) for l in lotes]
    })


# ─── QUADRAS ──────────────────────────────────────────────────

@app.route("/quadras")
@login_required
def quadras_lista():
    busca = request.args.get("busca", "")
    la = session["loteamento_ativo"]
    with get_db() as conn:
        sql = "SELECT q.*, l.nome as loteamento_nome FROM quadras q JOIN loteamentos l ON l.id=q.loteamento_id WHERE q.loteamento_id=? AND q.polygon_coords!='[]' AND q.polygon_coords IS NOT NULL"
        params = [la]
        if busca:
            sql += " AND q.identificacao LIKE ?"
            params.append(f"%{busca}%")
        sql += " ORDER BY q.identificacao"
        rows = conn.execute(sql, params).fetchall()
        loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=? ORDER BY nome", (la,)).fetchall()
    return render_template("quadras/lista.html", registros=rows, busca=busca, loteamento_id=la, loteamentos=loteamentos)


@app.route("/quadras/novo", methods=["GET", "POST"])
@login_required
def quadras_novo():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        if request.method == "POST":
            loteamento_id = la
            identificacao = request.form.get("identificacao", "").strip()
            qtd_lotes = request.form.get("qtd_lotes", 0, type=int)
            rua_norte = request.form.get("rua_norte", "").strip()
            rua_sul = request.form.get("rua_sul", "").strip()
            rua_leste = request.form.get("rua_leste", "").strip()
            rua_oeste = request.form.get("rua_oeste", "").strip()
            if not identificacao:
                loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
                return render_template("quadras/form.html", registro=None, loteamentos=loteamentos, erro="Identificação é obrigatória.")
            polygon_coords = request.form.get("polygon_coords", "[]")
            layout = request.form.get("layout", "horizontal")
            conn.execute("""
                INSERT INTO quadras (loteamento_id, identificacao, qtd_lotes, rua_norte, rua_sul, rua_leste, rua_oeste, polygon_coords, layout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (loteamento_id, identificacao, qtd_lotes, rua_norte, rua_sul, rua_leste, rua_oeste, polygon_coords, layout))
            conn.execute("UPDATE loteamentos SET qtd_quadras=(SELECT COUNT(*) FROM quadras WHERE loteamento_id=?) WHERE id=?", (loteamento_id, loteamento_id))
            return redirect(url_for("quadras_lista"))
        loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
    return render_template("quadras/form.html", registro=None, loteamentos=loteamentos, erro=None)


@app.route("/quadras/editar/<int:id>", methods=["GET", "POST"])
@login_required
def quadras_editar(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        if request.method == "POST":
            identificacao = request.form.get("identificacao", "").strip()
            qtd_lotes = request.form.get("qtd_lotes", 0, type=int)
            rua_norte = request.form.get("rua_norte", "").strip()
            rua_sul = request.form.get("rua_sul", "").strip()
            rua_leste = request.form.get("rua_leste", "").strip()
            rua_oeste = request.form.get("rua_oeste", "").strip()
            polygon_coords = request.form.get("polygon_coords", "[]")
            layout = request.form.get("layout", "horizontal")
            conn.execute("""
                UPDATE quadras SET loteamento_id=?, identificacao=?, qtd_lotes=?, rua_norte=?, rua_sul=?, rua_leste=?, rua_oeste=?, polygon_coords=?, layout=?
                WHERE id=?
            """, (la, identificacao, qtd_lotes, rua_norte, rua_sul, rua_leste, rua_oeste, polygon_coords, layout, id))
            recalcular_lotes_por_quadra(conn, id)
            return redirect(url_for("quadras_lista"))
        reg = conn.execute("SELECT * FROM quadras WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if not reg:
            flash("Quadra não encontrada.")
            return redirect(url_for("quadras_lista"))
        loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
    return render_template("quadras/form.html", registro=reg, loteamentos=loteamentos, erro=None)


@app.route("/quadras/excluir/<int:id>")
@login_required
def quadras_excluir(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        q = conn.execute("SELECT loteamento_id FROM quadras WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if q:
            conn.execute("DELETE FROM quadras WHERE id=?", (id,))
            conn.execute("UPDATE loteamentos SET qtd_quadras=(SELECT COUNT(*) FROM quadras WHERE loteamento_id=?) WHERE id=?", (la, la))
    return redirect(url_for("quadras_lista"))

# ─── LOTES ─────────────────────────────────────────────────────

@app.route("/lotes")
@login_required
def lotes_lista():
    busca = request.args.get("busca", "")
    quadra_id = request.args.get("quadra_id", "")
    la = session["loteamento_ativo"]
    with get_db() as conn:
        params = [la]
        sql = """SELECT l.*, q.identificacao as quadra_nome, lotea.nome as loteamento_nome
                 FROM lotes l
                 JOIN quadras q ON q.id=l.quadra_id
                 JOIN loteamentos lotea ON lotea.id=q.loteamento_id
                 WHERE q.loteamento_id=?"""
        if busca:
            sql += " AND (l.numero LIKE ? OR l.dono_nome LIKE ?)"
            params.extend([f"%{busca}%", f"%{busca}%"])
        if quadra_id:
            sql += " AND l.quadra_id=?"
            params.append(quadra_id)
        sql += " ORDER BY q.identificacao, CAST(l.numero AS INTEGER)"
        rows = conn.execute(sql, params).fetchall()
        quadras = conn.execute("""SELECT q.*, l.nome as loteamento_nome
                                  FROM quadras q
                                  JOIN loteamentos l ON l.id=q.loteamento_id
                                  WHERE q.loteamento_id=?
                                  ORDER BY q.identificacao""", (la,)).fetchall()
    return render_template("lotes/lista.html", registros=rows, busca=busca, quadra_id=quadra_id, quadras=quadras)


@app.route("/lotes/novo", methods=["GET", "POST"])
@login_required
def lotes_novo():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
        if request.method == "POST":
            quadra_id = request.form.get("quadra_id")
            numero = request.form.get("numero", "").strip()
            tamanho_frente = request.form.get("tamanho_frente", 0, type=float)
            tamanho_fundo = request.form.get("tamanho_fundo", 0, type=float)
            tamanho_esquerda = request.form.get("tamanho_esquerda", 0, type=float)
            tamanho_direita = request.form.get("tamanho_direita", 0, type=float)
            dono_pessoa_id = request.form.get("dono_pessoa_id") or None
            dono_nome = request.form.get("dono_nome", "").strip()
            dono_cpf = re.sub(r"\D", "", request.form.get("dono_cpf", ""))
            dono_contato = request.form.get("dono_contato", "").strip()
            polygon_coords = request.form.get("polygon_coords", "[]")
            if not quadra_id:
                quadras = conn.execute("""SELECT q.*, l.nome as loteamento_nome
                                          FROM quadras q JOIN loteamentos l ON l.id=q.loteamento_id
                                          WHERE q.loteamento_id=?
                                          ORDER BY q.identificacao""", (la,)).fetchall()
                loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
                return render_template("lotes/form.html", registro=None, quadras=quadras, pessoas=pessoas, loteamentos=loteamentos, erro="Quadra é obrigatória.")
            if (not polygon_coords or polygon_coords == "[]") and tamanho_frente and tamanho_fundo:
                ref = conn.execute("""SELECT polygon_coords FROM lotes
                                      WHERE quadra_id=? AND polygon_coords!='[]' AND polygon_coords IS NOT NULL
                                      ORDER BY numero LIMIT 1""", (quadra_id,)).fetchone()
                if ref:
                    coords_ref = json.loads(ref["polygon_coords"])
                    polygon_coords = json.dumps(gerar_poligono_trapezio(tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, coords_ref))
            conn.execute("""
                INSERT INTO lotes (quadra_id, numero, tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, dono_pessoa_id, dono_nome, dono_cpf, dono_contato, polygon_coords)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (quadra_id, numero, tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, dono_pessoa_id, dono_nome, dono_cpf, dono_contato, polygon_coords))
            return redirect(url_for("lotes_lista"))
        quadras = conn.execute("""SELECT q.*, l.nome as loteamento_nome
                                  FROM quadras q JOIN loteamentos l ON l.id=q.loteamento_id
                                  WHERE q.loteamento_id=?
                                  ORDER BY q.identificacao""", (la,)).fetchall()
        loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
    return render_template("lotes/form.html", registro=None, quadras=quadras, pessoas=pessoas, loteamentos=loteamentos, erro=None)


@app.route("/lotes/editar/<int:id>", methods=["GET", "POST"])
@login_required
def lotes_editar(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
        if request.method == "POST":
            quadra_id = request.form.get("quadra_id")
            numero = request.form.get("numero", "").strip()
            tamanho_frente = request.form.get("tamanho_frente", 0, type=float)
            tamanho_fundo = request.form.get("tamanho_fundo", 0, type=float)
            tamanho_esquerda = request.form.get("tamanho_esquerda", 0, type=float)
            tamanho_direita = request.form.get("tamanho_direita", 0, type=float)
            dono_pessoa_id = request.form.get("dono_pessoa_id") or None
            dono_nome = request.form.get("dono_nome", "").strip()
            dono_cpf = re.sub(r"\D", "", request.form.get("dono_cpf", ""))
            dono_contato = request.form.get("dono_contato", "").strip()
            lotes_limitrofes = json.dumps(request.form.getlist("lotes_limitrofes"))
            status = request.form.get("status", "disponivel")
            polygon_coords = request.form.get("polygon_coords", "[]")
            if (not polygon_coords or polygon_coords == "[]") and tamanho_frente and tamanho_fundo:
                reg_poly = conn.execute("SELECT polygon_coords FROM lotes WHERE id=?", (id,)).fetchone()
                if reg_poly:
                    coords_atuais = json.loads(reg_poly["polygon_coords"] or "[]")
                    if len(coords_atuais) >= 4:
                        polygon_coords = json.dumps(gerar_poligono_trapezio(tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, coords_atuais))
            conn.execute("""
                UPDATE lotes SET quadra_id=?, numero=?, tamanho_frente=?, tamanho_fundo=?, tamanho_esquerda=?, tamanho_direita=?,
                dono_pessoa_id=?, dono_nome=?, dono_cpf=?, dono_contato=?, lotes_limitrofes=?, status=?, polygon_coords=?
                WHERE id=?
            """, (quadra_id, numero, tamanho_frente, tamanho_fundo, tamanho_esquerda, tamanho_direita, dono_pessoa_id, dono_nome, dono_cpf, dono_contato, lotes_limitrofes, status, polygon_coords, id))
            return redirect(url_for("lotes_lista"))
        reg = conn.execute("""SELECT l.* FROM lotes l
                              JOIN quadras q ON q.id=l.quadra_id
                              WHERE l.id=? AND q.loteamento_id=?""", (id, la)).fetchone()
        if not reg:
            flash("Lote não encontrado.")
            return redirect(url_for("lotes_lista"))
        quadras = conn.execute("""SELECT q.*, l.nome as loteamento_nome
                                  FROM quadras q JOIN loteamentos l ON l.id=q.loteamento_id
                                  WHERE q.loteamento_id=?
                                  ORDER BY q.identificacao""", (la,)).fetchall()
        todos_lotes = conn.execute("""SELECT l.id, l.numero, q.identificacao as quadra_nome
                                      FROM lotes l
                                      JOIN quadras q ON q.id=l.quadra_id
                                      WHERE q.loteamento_id=? AND l.id!=?
                                      ORDER BY q.identificacao, CAST(l.numero AS INTEGER)""", (la, id)).fetchall()
        limitrofes_ids = json.loads(reg["lotes_limitrofes"] or "[]")
        loteamentos = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchall()
    return render_template("lotes/form.html", registro=reg, quadras=quadras, todos_lotes=todos_lotes, pessoas=pessoas, limitrofes_ids=limitrofes_ids, loteamentos=loteamentos, erro=None)


@app.route("/lotes/excluir/<int:id>")
@login_required
def lotes_excluir(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        conn.execute("""DELETE FROM lotes WHERE id=? AND quadra_id IN
                        (SELECT id FROM quadras WHERE loteamento_id=?)""", (id, la))
    return redirect(url_for("lotes_lista"))


@app.route("/api/lotes/<int:loteamento_id>")
@login_required
def api_lotes_geo(loteamento_id):
    with get_db() as conn:
        lotes = conn.execute("""
            SELECT l.id, l.numero, l.polygon_coords, l.status, l.dono_nome,
                   l.tamanho_frente, l.tamanho_fundo, l.tamanho_esquerda, l.tamanho_direita, q.identificacao as quadra_nome
            FROM lotes l JOIN quadras q ON q.id=l.quadra_id
            WHERE q.loteamento_id=?
        """, (loteamento_id,)).fetchall()
    return jsonify([dict(l) for l in lotes])

# ─── PERMUTAS ─────────────────────────────────────────────────

@app.route("/permutas")
@login_required
def permutas_lista():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.*, STRING_AGG(l.numero, '", "') as lotes_nums
            FROM permutas p
            LEFT JOIN permuta_lotes pl ON pl.permuta_id=p.id
            LEFT JOIN lotes l ON l.id=pl.lote_id
            LEFT JOIN quadras q ON q.id=l.quadra_id
            WHERE q.loteamento_id=?
            GROUP BY p.id ORDER BY p.data DESC
        """, (la,)).fetchall()
    return render_template("permutas/lista.html", registros=rows)


@app.route("/permutas/novo", methods=["GET", "POST"])
@login_required
def permutas_novo():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
        if request.method == "POST":
            data = request.form.get("data", datetime.now().strftime("%Y-%m-%d"))
            dono_anterior_pessoa_id = request.form.get("dono_anterior_pessoa_id") or None
            dono_anterior_nome = request.form.get("dono_anterior_nome", "").strip()
            dono_anterior_cpf = re.sub(r"\D", "", request.form.get("dono_anterior_cpf", ""))
            dono_posterior_pessoa_id = request.form.get("dono_posterior_pessoa_id") or None
            dono_posterior_nome = request.form.get("dono_posterior_nome", "").strip()
            dono_posterior_cpf = re.sub(r"\D", "", request.form.get("dono_posterior_cpf", ""))
            observacao = request.form.get("observacao", "").strip()
            lote_ids = request.form.getlist("lote_ids")
            if not dono_anterior_nome or not dono_posterior_nome or not lote_ids:
                lotes = conn.execute("""SELECT l.*, q.identificacao as quadra_nome
                                        FROM lotes l JOIN quadras q ON q.id=l.quadra_id
                                        WHERE q.loteamento_id=?
                                        ORDER BY q.identificacao, CAST(l.numero AS INTEGER)""", (la,)).fetchall()
                return render_template("permutas/form.html", registro=None, lotes=lotes, pessoas=pessoas, erro="Preencha todos os campos obrigatórios.")
            permuta_id = conn.execute("""
                INSERT INTO permutas (data, dono_anterior_pessoa_id, dono_anterior_nome, dono_anterior_cpf, dono_posterior_pessoa_id, dono_posterior_nome, dono_posterior_cpf, observacao)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """, (data, dono_anterior_pessoa_id, dono_anterior_nome, dono_anterior_cpf, dono_posterior_pessoa_id, dono_posterior_nome, dono_posterior_cpf, observacao)).fetchone()["id"]
            for lid in lote_ids:
                conn.execute("INSERT INTO permuta_lotes (permuta_id, lote_id) VALUES (?, ?)", (permuta_id, lid))
                conn.execute("UPDATE lotes SET status='permutado', dono_pessoa_id=?, dono_nome=?, dono_cpf=? WHERE id=?", (dono_posterior_pessoa_id, dono_posterior_nome, dono_posterior_cpf, lid))
            _gerar_recibo_permuta(conn, permuta_id)
            return redirect(url_for("permutas_lista"))
        lotes = conn.execute("""SELECT l.*, q.identificacao as quadra_nome
                                FROM lotes l JOIN quadras q ON q.id=l.quadra_id
                                WHERE q.loteamento_id=?
                                ORDER BY q.identificacao, CAST(l.numero AS INTEGER)""", (la,)).fetchall()
    return render_template("permutas/form.html", registro=None, lotes=lotes, pessoas=pessoas, erro=None)


@app.route("/permutas/excluir/<int:id>")
@login_required
def permutas_excluir(id):
    with get_db() as conn:
        conn.execute("DELETE FROM permutas WHERE id=?", (id,))
    return redirect(url_for("permutas_lista"))


@app.route("/permutas/recibo/<int:id>")
@login_required
def permutas_recibo(id):
    path = os.path.join(RECIBOS_DIR, f"permuta_{id}.pdf")
    if os.path.exists(path):
        return send_file(path, mimetype="application/pdf")
    return "Recibo não encontrado.", 404


def _gerar_recibo_permuta(conn, permuta_id):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    permuta = conn.execute("SELECT * FROM permutas WHERE id=?", (permuta_id,)).fetchone()
    lotes = conn.execute("""
        SELECT l.*, q.identificacao as quadra_nome, lotea.nome as loteamento_nome
        FROM permuta_lotes pl JOIN lotes l ON l.id=pl.lote_id
        JOIN quadras q ON q.id=l.quadra_id
        JOIN loteamentos lotea ON lotea.id=q.loteamento_id
        WHERE pl.permuta_id=?
    """, (permuta_id,)).fetchall()
    path = os.path.join(RECIBOS_DIR, f"permuta_{permuta_id}.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("RECIBO DE PERMUTA DE LOTES", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"<b>Data:</b> {permuta['data']}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Dados da Permuta:</b>", styles["Heading2"]))
    elements.append(Paragraph(f"<b>Doador/Anterior:</b> {permuta['dono_anterior_nome']} (CPF: {permuta['dono_anterior_cpf'] or '---'})", styles["Normal"]))
    elements.append(Paragraph(f"<b>Recebedor/Posterior:</b> {permuta['dono_posterior_nome']} (CPF: {permuta['dono_posterior_cpf'] or '---'})", styles["Normal"]))
    elements.append(Spacer(1, 6))
    if permuta["observacao"]:
        elements.append(Paragraph(f"<b>Observação:</b> {permuta['observacao']}", styles["Normal"]))
        elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Lotes envolvidos:</b>", styles["Heading2"]))
    data = [["Loteamento", "Quadra", "Lote", "Frente", "Fundo", "Esquerda", "Direita", "Área"]]
    for l in lotes:
        f = l["tamanho_frente"] or 0; fu = l["tamanho_fundo"] or 0
        e = l["tamanho_esquerda"] or 0; d = l["tamanho_direita"] or 0
        area = ((f + fu) / 2) * ((e + d) / 2)
        data.append([l["loteamento_nome"], l["quadra_nome"], l["numero"],
                     f"{f:.2f}", f"{fu:.2f}", f"{e:.2f}", f"{d:.2f}", f"{area:.2f}"])
    t = Table(data)
    t.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.5, colors.grey),
                           ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#4a90d9")),
                           ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                           ("ALIGN", (3,0), (-1,-1), "CENTER")]))
    elements.append(t)
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Assinatura do Doador/Anterior", styles["Normal"]))
    elements.append(Spacer(1, 15))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Assinatura do Recebedor/Posterior", styles["Normal"]))
    doc.build(elements)
    conn.execute("UPDATE permutas SET recibo_pdf=? WHERE id=?", (f"permuta_{permuta_id}.pdf", permuta_id))

# ─── VENDAS ───────────────────────────────────────────────────

@app.route("/vendas")
@login_required
def vendas_lista():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        rows = conn.execute("""
            SELECT v.*, l.numero as lote_numero, q.identificacao as quadra_nome,
                   lotea.nome as loteamento_nome, fp.nome as forma_pagamento_nome
            FROM vendas v
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            JOIN loteamentos lotea ON lotea.id=q.loteamento_id
            LEFT JOIN formas_pagamento fp ON fp.id=v.forma_pagamento_id
            WHERE q.loteamento_id=?
            ORDER BY v.data DESC
        """, (la,)).fetchall()
    return render_template("vendas/lista.html", registros=rows)


@app.route("/vendas/nova", methods=["GET", "POST"])
@login_required
def vendas_nova():
    la = session["loteamento_ativo"]
    with get_db() as conn:
        pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
        if request.method == "POST":
            data = request.form.get("data", datetime.now().strftime("%Y-%m-%d"))
            lote_id = request.form.get("lote_id")
            vendedor_pessoa_id = request.form.get("vendedor_pessoa_id") or None
            vendedor_nome = request.form.get("vendedor_nome", "").strip()
            vendedor_cpf = re.sub(r"\D", "", request.form.get("vendedor_cpf", ""))
            vendedor_contato = request.form.get("vendedor_contato", "").strip()
            comprador_pessoa_id = request.form.get("comprador_pessoa_id") or None
            comprador_nome = request.form.get("comprador_nome", "").strip()
            comprador_cpf = re.sub(r"\D", "", request.form.get("comprador_cpf", ""))
            comprador_contato = request.form.get("comprador_contato", "").strip()
            valor_total = request.form.get("valor_total", 0, type=float)
            forma_pagamento_id = request.form.get("forma_pagamento_id") or None
            numero_parcelas = request.form.get("numero_parcelas", 1, type=int)
            observacao = request.form.get("observacao", "").strip()
            if not lote_id or not vendedor_nome or not comprador_nome:
                lotes = conn.execute("""SELECT l.*, q.identificacao as quadra_nome
                                        FROM lotes l JOIN quadras q ON q.id=l.quadra_id
                                        WHERE q.loteamento_id=? AND l.status='disponivel'
                                        ORDER BY q.identificacao, CAST(l.numero AS INTEGER)""", (la,)).fetchall()
                formas = conn.execute("SELECT * FROM formas_pagamento ORDER BY nome").fetchall()
                return render_template("vendas/form.html", registro=None, lotes=lotes, formas=formas, pessoas=pessoas, erro="Preencha todos os campos obrigatórios.")
            venda_id = conn.execute("""
                INSERT INTO vendas (data, lote_id, vendedor_pessoa_id, vendedor_nome, vendedor_cpf, vendedor_contato,
                    comprador_pessoa_id, comprador_nome, comprador_cpf, comprador_contato, valor_total,
                    forma_pagamento_id, numero_parcelas, observacao)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """, (data, lote_id, vendedor_pessoa_id, vendedor_nome, vendedor_cpf, vendedor_contato,
                  comprador_pessoa_id, comprador_nome, comprador_cpf, comprador_contato, valor_total,
                  forma_pagamento_id, numero_parcelas, observacao)).fetchone()["id"]
            conn.execute("UPDATE lotes SET status='vendido', dono_pessoa_id=?, dono_nome=?, dono_cpf=? WHERE id=?", (comprador_pessoa_id, comprador_nome, comprador_cpf, lote_id))
            if numero_parcelas > 1:
                valor_parcela = round(valor_total / numero_parcelas, 2)
                for i in range(1, numero_parcelas + 1):
                    conn.execute("""
                        INSERT INTO pagamentos_venda (venda_id, numero_parcela, data_vencimento, valor)
                        VALUES (?, ?, ?, ?)
                    """, (venda_id, i, "", valor_parcela))
            _gerar_recibo_venda(conn, venda_id)
            return redirect(url_for("vendas_lista"))
        lotes = conn.execute("""SELECT l.*, q.identificacao as quadra_nome
                                FROM lotes l JOIN quadras q ON q.id=l.quadra_id
                                WHERE q.loteamento_id=? AND l.status='disponivel'
                                ORDER BY q.identificacao, CAST(l.numero AS INTEGER)""", (la,)).fetchall()
        formas = conn.execute("SELECT * FROM formas_pagamento ORDER BY nome").fetchall()
    return render_template("vendas/form.html", registro=None, lotes=lotes, formas=formas, pessoas=pessoas, erro=None)


@app.route("/vendas/editar/<int:id>", methods=["GET", "POST"])
@login_required
def vendas_editar(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
        if request.method == "POST":
            data = request.form.get("data", "")
            vendedor_pessoa_id = request.form.get("vendedor_pessoa_id") or None
            vendedor_nome = request.form.get("vendedor_nome", "").strip()
            vendedor_cpf = re.sub(r"\D", "", request.form.get("vendedor_cpf", ""))
            vendedor_contato = request.form.get("vendedor_contato", "").strip()
            comprador_pessoa_id = request.form.get("comprador_pessoa_id") or None
            comprador_nome = request.form.get("comprador_nome", "").strip()
            comprador_cpf = re.sub(r"\D", "", request.form.get("comprador_cpf", ""))
            comprador_contato = request.form.get("comprador_contato", "").strip()
            valor_total = request.form.get("valor_total", 0, type=float)
            forma_pagamento_id = request.form.get("forma_pagamento_id") or None
            numero_parcelas = request.form.get("numero_parcelas", 1, type=int)
            observacao = request.form.get("observacao", "").strip()
            conn.execute("""
                UPDATE vendas SET data=?, vendedor_pessoa_id=?, vendedor_nome=?, vendedor_cpf=?, vendedor_contato=?,
                    comprador_pessoa_id=?, comprador_nome=?, comprador_cpf=?, comprador_contato=?,
                    valor_total=?, forma_pagamento_id=?, numero_parcelas=?, observacao=?
                WHERE id=?
            """, (data, vendedor_pessoa_id, vendedor_nome, vendedor_cpf, vendedor_contato,
                  comprador_pessoa_id, comprador_nome, comprador_cpf, comprador_contato,
                  valor_total, forma_pagamento_id, numero_parcelas, observacao, id))
            return redirect(url_for("vendas_lista"))
        reg = conn.execute("""
            SELECT v.* FROM vendas v
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            WHERE v.id=? AND q.loteamento_id=?
        """, (id, la)).fetchone()
        if not reg:
            flash("Venda não encontrada.")
            return redirect(url_for("vendas_lista"))
        formas = conn.execute("SELECT * FROM formas_pagamento ORDER BY nome").fetchall()
    return render_template("vendas/form.html", registro=reg, formas=formas, pessoas=pessoas, erro=None, lotes=None)


@app.route("/vendas/excluir/<int:id>")
@login_required
def vendas_excluir(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        v = conn.execute("""
            SELECT v.lote_id FROM vendas v
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            WHERE v.id=? AND q.loteamento_id=?
        """, (id, la)).fetchone()
        if v:
            conn.execute("DELETE FROM vendas WHERE id=?", (id,))
            conn.execute("UPDATE lotes SET status='disponivel', dono_nome='', dono_cpf='' WHERE id=?", (v["lote_id"],))
    return redirect(url_for("vendas_lista"))


@app.route("/vendas/recibo/<int:id>")
@login_required
def vendas_recibo(id):
    path = os.path.join(RECIBOS_DIR, f"venda_{id}.pdf")
    if os.path.exists(path):
        return send_file(path, mimetype="application/pdf")
    return "Recibo não encontrado.", 404


def _gerar_recibo_venda(conn, venda_id):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    venda = conn.execute("""
        SELECT v.*, l.numero as lote_numero, l.tamanho_frente, l.tamanho_fundo, l.tamanho_esquerda, l.tamanho_direita,
               q.identificacao as quadra_nome, lotea.nome as loteamento_nome,
               fp.nome as forma_pagamento_nome
        FROM vendas v JOIN lotes l ON l.id=v.lote_id
        JOIN quadras q ON q.id=l.quadra_id
        JOIN loteamentos lotea ON lotea.id=q.loteamento_id
        LEFT JOIN formas_pagamento fp ON fp.id=v.forma_pagamento_id
        WHERE v.id=?
    """, (venda_id,)).fetchone()
    path = os.path.join(RECIBOS_DIR, f"venda_{venda_id}.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("RECIBO DE VENDA DE LOTE", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"<b>Data:</b> {venda['data']}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Dados do Lote:</b>", styles["Heading2"]))
    f = venda["tamanho_frente"] or 0; fu = venda["tamanho_fundo"] or 0
    e = venda["tamanho_esquerda"] or 0; d = venda["tamanho_direita"] or 0
    area = ((f + fu) / 2) * ((e + d) / 2)
    elements.append(Paragraph(f"Loteamento: {venda['loteamento_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"Quadra: {venda['quadra_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"Lote: {venda['lote_numero']}", styles["Normal"]))
    elements.append(Paragraph(f"Frente: {f:.2f} m | Fundo: {fu:.2f} m | Esquerda: {e:.2f} m | Direita: {d:.2f} m", styles["Normal"]))
    elements.append(Paragraph(f"Área: {area:.2f} m²", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Vendedor:</b>", styles["Heading2"]))
    elements.append(Paragraph(f"Nome: {venda['vendedor_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"CPF: {venda['vendedor_cpf'] or '---'}", styles["Normal"]))
    elements.append(Paragraph(f"Contato: {venda['vendedor_contato'] or '---'}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Comprador:</b>", styles["Heading2"]))
    elements.append(Paragraph(f"Nome: {venda['comprador_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"CPF: {venda['comprador_cpf'] or '---'}", styles["Normal"]))
    elements.append(Paragraph(f"Contato: {venda['comprador_contato'] or '---'}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Valor Total:</b> R$ {venda['valor_total']:_.2f}".replace(".", ",").replace("_", "."), styles["Normal"]))
    elements.append(Paragraph(f"<b>Forma de Pagamento:</b> {venda['forma_pagamento_nome'] or '---'}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Parcelas:</b> {venda['numero_parcelas']}x", styles["Normal"]))
    if venda["observacao"]:
        elements.append(Paragraph(f"<b>Observação:</b> {venda['observacao']}", styles["Normal"]))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Assinatura do Vendedor", styles["Normal"]))
    elements.append(Spacer(1, 15))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Assinatura do Comprador", styles["Normal"]))
    doc.build(elements)
    conn.execute("UPDATE vendas SET recibo_pdf=? WHERE id=?", (f"venda_{venda_id}.pdf", venda_id))

# ─── PAGAMENTOS ───────────────────────────────────────────────

@app.route("/pagamentos")
@login_required
def pagamentos_lista():
    la = session["loteamento_ativo"]
    venda_id = request.args.get("venda_id", "")
    with get_db() as conn:
        if venda_id:
            rows = conn.execute("""
                SELECT p.*, v.valor_total, v.numero_parcelas,
                       l.numero as lote_numero, q.identificacao as quadra_nome,
                       lotea.nome as loteamento_nome, v.comprador_nome
                FROM pagamentos_venda p
                JOIN vendas v ON v.id=p.venda_id
                JOIN lotes l ON l.id=v.lote_id
                JOIN quadras q ON q.id=l.quadra_id
                JOIN loteamentos lotea ON lotea.id=q.loteamento_id
                WHERE p.venda_id=? AND q.loteamento_id=?
                ORDER BY p.numero_parcela
            """, (venda_id, la)).fetchall()
        else:
            rows = conn.execute("""
                SELECT p.*, v.valor_total, v.numero_parcelas,
                       l.numero as lote_numero, q.identificacao as quadra_nome,
                       lotea.nome as loteamento_nome, v.comprador_nome
                FROM pagamentos_venda p
                JOIN vendas v ON v.id=p.venda_id
                JOIN lotes l ON l.id=v.lote_id
                JOIN quadras q ON q.id=l.quadra_id
                JOIN loteamentos lotea ON lotea.id=q.loteamento_id
                WHERE q.loteamento_id=?
                ORDER BY p.venda_id, p.numero_parcela
            """, (la,)).fetchall()
        vendas = conn.execute("""SELECT v.id, l.numero as lote_numero, v.comprador_nome
                                 FROM vendas v
                                 JOIN lotes l ON l.id=v.lote_id
                                 JOIN quadras q ON q.id=l.quadra_id
                                 WHERE q.loteamento_id=?
                                 ORDER BY v.data DESC""", (la,)).fetchall()
    return render_template("vendas/pagamentos.html", registros=rows, venda_id=venda_id, vendas=vendas)


@app.route("/pagamentos/editar/<int:id>", methods=["POST"])
@login_required
def pagamentos_editar(id):
    la = session["loteamento_ativo"]
    with get_db() as conn:
        data_pagamento = request.form.get("data_pagamento", datetime.now().strftime("%Y-%m-%d"))
        pago = 1 if request.form.get("pago") else 0
        valor = request.form.get("valor", 0, type=float)
        pag = conn.execute("""
            SELECT p.venda_id FROM pagamentos_venda p
            JOIN vendas v ON v.id=p.venda_id
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            WHERE p.id=? AND q.loteamento_id=?
        """, (id, la)).fetchone()
        if pag:
            conn.execute("UPDATE pagamentos_venda SET data_pagamento=?, pago=?, valor=? WHERE id=?", (data_pagamento, pago, valor, id))
            _gerar_recibo_pagamento(conn, id)
        return redirect(url_for("pagamentos_lista", venda_id=pag["venda_id"] if pag else None))


@app.route("/pagamentos/recibo/<int:id>")
@login_required
def pagamentos_recibo(id):
    path = os.path.join(RECIBOS_DIR, f"pagamento_{id}.pdf")
    if os.path.exists(path):
        return send_file(path, mimetype="application/pdf")
    return "Recibo não encontrado.", 404


def _gerar_recibo_pagamento(conn, pagamento_id):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    pag = conn.execute("""
        SELECT p.*, v.valor_total, v.numero_parcelas, v.comprador_nome, v.vendedor_nome,
               l.numero as lote_numero, q.identificacao as quadra_nome,
               lotea.nome as loteamento_nome
        FROM pagamentos_venda p
        JOIN vendas v ON v.id=p.venda_id
        JOIN lotes l ON l.id=v.lote_id
        JOIN quadras q ON q.id=l.quadra_id
        JOIN loteamentos lotea ON lotea.id=q.loteamento_id
        WHERE p.id=?
    """, (pagamento_id,)).fetchone()
    path = os.path.join(RECIBOS_DIR, f"pagamento_{pagamento_id}.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("RECIBO DE PAGAMENTO", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"<b>Loteamento:</b> {pag['loteamento_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Quadra:</b> {pag['quadra_nome']} - <b>Lote:</b> {pag['lote_numero']}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Vendedor:</b> {pag['vendedor_nome']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Comprador:</b> {pag['comprador_nome']}", styles["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Parcela:</b> {pag['numero_parcela']}/{pag['numero_parcela']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Valor:</b> R$ {pag['valor']:_.2f}".replace(".", ",").replace("_", "."), styles["Normal"]))
    elements.append(Paragraph(f"<b>Data Pagamento:</b> {pag['data_pagamento'] or '---'}", styles["Normal"]))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Paragraph(f"{'__'*40}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Assinatura do Recebedor", styles["Normal"]))
    doc.build(elements)
    conn.execute("UPDATE pagamentos_venda SET recibo_pdf=? WHERE id=?", (f"pagamento_{pagamento_id}.pdf", pagamento_id))

# ─── USUÁRIOS ─────────────────────────────────────────────────

@app.route("/usuarios")
@login_required
def usuarios_lista():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.*, STRING_AGG(p.modulo, '", "') as permissoes_lista
            FROM usuarios u LEFT JOIN permissoes p ON p.usuario_id=u.id GROUP BY u.id
        """).fetchall()
        todos_loteamentos = conn.execute("SELECT * FROM loteamentos ORDER BY nome").fetchall()
        registros = []
        for r in rows:
            d = dict(r)
            lots = conn.execute("""SELECT l.nome FROM usuario_loteamentos ul
                                   JOIN loteamentos l ON l.id=ul.loteamento_id
                                   WHERE ul.usuario_id=?""", (r["id"],)).fetchall()
            d["loteamentos_nomes"] = ", ".join(l["nome"] for l in lots) if lots else "—"
            registros.append(d)
    return render_template("usuarios/lista.html", registros=registros, todos_loteamentos=todos_loteamentos)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
def usuarios_novo():
    with get_db() as conn:
        todos_loteamentos = conn.execute("SELECT * FROM loteamentos ORDER BY nome").fetchall()
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                return render_template("usuarios/form.html", registro=None, erro="Usuário e senha são obrigatórios.", todos_loteamentos=todos_loteamentos)
            try:
                conn.execute("INSERT INTO usuarios (username, password_hash) VALUES (?, ?)",
                             (username, generate_password_hash(password)))
                user = conn.execute("SELECT id FROM usuarios WHERE username=?", (username,)).fetchone()
                for mod in request.form.getlist("permissoes"):
                    conn.execute("INSERT INTO permissoes (usuario_id, modulo) VALUES (?, ?)", (user["id"], mod))
                for lid in request.form.getlist("loteamentos"):
                    conn.execute("INSERT INTO usuario_loteamentos (usuario_id, loteamento_id) VALUES (?, ?)", (user["id"], lid))
            except Exception:
                return render_template("usuarios/form.html", registro=None, erro="Usuário já existe.", todos_loteamentos=todos_loteamentos)
            return redirect(url_for("usuarios_lista"))
    return render_template("usuarios/form.html", registro=None, erro=None, todos_loteamentos=todos_loteamentos)


@app.route("/usuarios/editar/<int:id>", methods=["GET", "POST"])
@login_required
def usuarios_editar(id):
    with get_db() as conn:
        todos_loteamentos = conn.execute("SELECT * FROM loteamentos ORDER BY nome").fetchall()
        if request.method == "POST":
            password = request.form.get("password", "")
            if password:
                conn.execute("UPDATE usuarios SET password_hash=? WHERE id=?", (generate_password_hash(password), id))
            conn.execute("DELETE FROM permissoes WHERE usuario_id=?", (id,))
            for mod in request.form.getlist("permissoes"):
                conn.execute("INSERT INTO permissoes (usuario_id, modulo) VALUES (?, ?)", (id, mod))
            conn.execute("DELETE FROM usuario_loteamentos WHERE usuario_id=?", (id,))
            for lid in request.form.getlist("loteamentos"):
                conn.execute("INSERT INTO usuario_loteamentos (usuario_id, loteamento_id) VALUES (?, ?)", (id, lid))
            return redirect(url_for("usuarios_lista"))
        reg = conn.execute("SELECT * FROM usuarios WHERE id=?", (id,)).fetchone()
        user_perms = conn.execute("SELECT modulo FROM permissoes WHERE usuario_id=?", (id,)).fetchall()
        user_perms = [p["modulo"] for p in user_perms]
        user_lots = conn.execute("SELECT loteamento_id FROM usuario_loteamentos WHERE usuario_id=?", (id,)).fetchall()
        user_lots_ids = [r["loteamento_id"] for r in user_lots]
    return render_template("usuarios/form.html", registro=reg, erro=None, todos_loteamentos=todos_loteamentos, user_perms=user_perms, user_lots_ids=user_lots_ids)


# ─── FORMAS DE PAGAMENTO ──────────────────────────────────────

@app.route("/formas_pagamento")
@login_required
def formas_pagamento_lista():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM formas_pagamento ORDER BY nome").fetchall()
    return render_template("formas_pagamento/lista.html", registros=rows)


@app.route("/formas_pagamento/novo", methods=["POST"])
@login_required
def formas_pagamento_novo():
    nome = request.form.get("nome", "").strip()
    if nome:
        with get_db() as conn:
            try:
                conn.execute("INSERT INTO formas_pagamento (nome) VALUES (?)", (nome,))
            except Exception:
                pass
    return redirect(url_for("formas_pagamento_lista"))


@app.route("/formas_pagamento/excluir/<int:id>")
@login_required
def formas_pagamento_excluir(id):
    with get_db() as conn:
        conn.execute("DELETE FROM formas_pagamento WHERE id=?", (id,))
    return redirect(url_for("formas_pagamento_lista"))


# ─── PESSOAS ──────────────────────────────────────────────────

@app.route("/pessoas")
@login_required
def pessoas_lista():
    busca = request.args.get("busca", "")
    with get_db() as conn:
        if busca:
            rows = conn.execute("""
                SELECT * FROM pessoas
                WHERE nome LIKE ? OR cpf LIKE ? OR contato LIKE ?
                ORDER BY nome
            """, (f"%{busca}%", f"%{busca}%", f"%{busca}%")).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
    return render_template("pessoas/lista.html", registros=rows, busca=busca)


@app.route("/pessoas/novo", methods=["GET", "POST"])
@login_required
def pessoas_novo():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        rg = request.form.get("rg", "").strip()
        contato = request.form.get("contato", "").strip()
        endereco = request.form.get("endereco", "").strip()
        observacao = request.form.get("observacao", "").strip()
        if not nome:
            return render_template("pessoas/form.html", registro=None, erro="Nome é obrigatório.")
        with get_db() as conn:
            conn.execute("INSERT INTO pessoas (nome, cpf, rg, contato, endereco, observacao, referencia_marcacao) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (nome, cpf, rg, contato, endereco, observacao, request.form.get("referencia_marcacao", "").strip().upper()[:1]))
        return redirect(url_for("pessoas_lista"))
    return render_template("pessoas/form.html", registro=None, erro=None)


@app.route("/pessoas/editar/<int:id>", methods=["GET", "POST"])
@login_required
def pessoas_editar(id):
    with get_db() as conn:
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            cpf = request.form.get("cpf", "").strip()
            rg = request.form.get("rg", "").strip()
            contato = request.form.get("contato", "").strip()
            endereco = request.form.get("endereco", "").strip()
            observacao = request.form.get("observacao", "").strip()
            if not nome:
                reg = conn.execute("SELECT * FROM pessoas WHERE id=?", (id,)).fetchone()
                return render_template("pessoas/form.html", registro=reg, erro="Nome é obrigatório.")
            conn.execute("UPDATE pessoas SET nome=?, cpf=?, rg=?, contato=?, endereco=?, observacao=?, referencia_marcacao=? WHERE id=?",
                         (nome, cpf, rg, contato, endereco, observacao, request.form.get("referencia_marcacao", "").strip().upper()[:1], id))
            return redirect(url_for("pessoas_lista"))
        reg = conn.execute("SELECT * FROM pessoas WHERE id=?", (id,)).fetchone()
    return render_template("pessoas/form.html", registro=reg, erro=None)


@app.route("/pessoas/excluir/<int:id>")
@login_required
def pessoas_excluir(id):
    with get_db() as conn:
        conn.execute("DELETE FROM pessoas WHERE id=?", (id,))
    return redirect(url_for("pessoas_lista"))


@app.route("/pessoas/buscar")
@login_required
def pessoas_buscar():
    q = request.args.get("q", "")
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, nome, cpf, contato, referencia_marcacao FROM pessoas
            WHERE nome LIKE ? OR cpf LIKE ?
            ORDER BY nome LIMIT 20
        """, (f"%{q}%", f"%{q}%")).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── RELATÓRIOS ───────────────────────────────────────────────

@app.route("/relatorios/quadras")
@login_required
def relatorios_quadras():
    la = session.get("loteamento_ativo")
    if not la:
        return redirect(url_for("selecionar_loteamento"))
    with get_db() as conn:
        quadras_data = []
        quadras_rows = conn.execute("""
            SELECT q.*,
                   COUNT(l.id) as total_lotes,
                   SUM(CASE WHEN l.status='disponivel' THEN 1 ELSE 0 END) as disponiveis,
                   SUM(CASE WHEN l.status='vendido' THEN 1 ELSE 0 END) as vendidos,
                   SUM(CASE WHEN l.status='permutado' THEN 1 ELSE 0 END) as permutados,
                   COALESCE(SUM(((l.tamanho_frente + l.tamanho_fundo) / 2.0) * ((l.tamanho_esquerda + l.tamanho_direita) / 2.0)), 0) as area_total
            FROM quadras q
            LEFT JOIN lotes l ON l.quadra_id=q.id
            WHERE q.loteamento_id=?
            GROUP BY q.id
            ORDER BY q.identificacao
        """, (la,)).fetchall()
        for q in quadras_rows:
            lotes_q = conn.execute("""
                SELECT * FROM lotes WHERE quadra_id=? ORDER BY numero
            """, (q["id"],)).fetchall()
            quadras_data.append({"quadra": q, "lotes": lotes_q})
        loteamento = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchone()
    return render_template("relatorios/quadras.html", quadras_data=quadras_data, loteamento=loteamento)


@app.route("/relatorios/pessoa")
@login_required
def relatorios_pessoa_selecionar():
    la = session.get("loteamento_ativo")
    if not la:
        return redirect(url_for("selecionar_loteamento"))
    busca = request.args.get("busca", "")
    with get_db() as conn:
        if busca:
            pessoas = conn.execute("""
                SELECT * FROM pessoas
                WHERE nome LIKE ? OR cpf LIKE ?
                ORDER BY nome
            """, (f"%{busca}%", f"%{busca}%")).fetchall()
        else:
            pessoas = conn.execute("SELECT * FROM pessoas ORDER BY nome").fetchall()
    return render_template("relatorios/pessoa_selecionar.html", pessoas=pessoas, busca=busca)


@app.route("/relatorios/pessoa/<int:id>")
@login_required
def relatorios_pessoa(id):
    la = session.get("loteamento_ativo")
    if not la:
        return redirect(url_for("selecionar_loteamento"))
    with get_db() as conn:
        pessoa = conn.execute("SELECT * FROM pessoas WHERE id=?", (id,)).fetchone()
        if not pessoa:
            flash("Pessoa não encontrada.")
            return redirect(url_for("relatorios_pessoa_selecionar"))
        lotes_dono = conn.execute("""
            SELECT l.*, q.identificacao as quadra_nome, lotea.nome as loteamento_nome
            FROM lotes l
            JOIN quadras q ON q.id=l.quadra_id
            JOIN loteamentos lotea ON lotea.id=q.loteamento_id
            WHERE l.dono_pessoa_id=? AND q.loteamento_id=?
            ORDER BY q.identificacao, CAST(l.numero AS INTEGER)
        """, (id, la)).fetchall()
        vendas_vendedor = conn.execute("""
            SELECT v.*, l.numero as lote_numero, q.identificacao as quadra_nome
            FROM vendas v
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            WHERE v.vendedor_pessoa_id=? AND q.loteamento_id=?
            ORDER BY v.data DESC
        """, (id, la)).fetchall()
        vendas_comprador = conn.execute("""
            SELECT v.*, l.numero as lote_numero, q.identificacao as quadra_nome
            FROM vendas v
            JOIN lotes l ON l.id=v.lote_id
            JOIN quadras q ON q.id=l.quadra_id
            WHERE v.comprador_pessoa_id=? AND q.loteamento_id=?
            ORDER BY v.data DESC
        """, (id, la)).fetchall()
        permutas_anterior = conn.execute("""
            SELECT p.*, STRING_AGG(l.numero, ', ') as lotes_nums
            FROM permutas p
            LEFT JOIN permuta_lotes pl ON pl.permuta_id=p.id
            LEFT JOIN lotes l ON l.id=pl.lote_id
            LEFT JOIN quadras q ON q.id=l.quadra_id
            WHERE p.dono_anterior_pessoa_id=? AND q.loteamento_id=?
            GROUP BY p.id ORDER BY p.data DESC
        """, (id, la)).fetchall()
        permutas_posterior = conn.execute("""
            SELECT p.*, STRING_AGG(l.numero, ', ') as lotes_nums
            FROM permutas p
            LEFT JOIN permuta_lotes pl ON pl.permuta_id=p.id
            LEFT JOIN lotes l ON l.id=pl.lote_id
            LEFT JOIN quadras q ON q.id=l.quadra_id
            WHERE p.dono_posterior_pessoa_id=? AND q.loteamento_id=?
            GROUP BY p.id ORDER BY p.data DESC
        """, (id, la)).fetchall()
        loteamento = conn.execute("SELECT * FROM loteamentos WHERE id=?", (la,)).fetchone()
    return render_template("relatorios/pessoa.html",
                           pessoa=pessoa, lotes_dono=lotes_dono,
                           vendas_vendedor=vendas_vendedor, vendas_comprador=vendas_comprador,
                           permutas_anterior=permutas_anterior, permutas_posterior=permutas_posterior,
                           loteamento=loteamento)


# ─── API: PONTOS (marcacoes no mapa) ──────────────────────────

@app.route("/api/pontos")
@login_required
def api_pontos_listar():
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify([])
    with get_db() as conn:
        pontos = conn.execute("SELECT * FROM pontos WHERE loteamento_id=? ORDER BY nome", (la,)).fetchall()
    return jsonify([dict(p) for p in pontos])


@app.route("/api/pontos/novo", methods=["POST"])
@login_required
def api_pontos_novo():
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    lat = float(data.get("lat", 0))
    lng = float(data.get("lng", 0))
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"erro": "Nome é obrigatório"}), 400
    tipo = data.get("tipo", "ponto")
    cor = data.get("cor", "#2196f3")
    descricao = data.get("descricao", "")
    icone = data.get("icone", "📍")
    with get_db() as conn:
        pid = conn.execute("""
            INSERT INTO pontos (loteamento_id, lat, lng, nome, tipo, cor, descricao, icone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
        """, (la, lat, lng, nome, tipo, cor, descricao, icone)).fetchone()["id"]
        ponto = conn.execute("SELECT * FROM pontos WHERE id=?", (pid,)).fetchone()
    return jsonify(dict(ponto))


@app.route("/api/pontos/<int:id>", methods=["PUT"])
@login_required
def api_pontos_atualizar(id):
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    data = request.get_json(force=True)
    with get_db() as conn:
        existente = conn.execute("SELECT id FROM pontos WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if not existente:
            return jsonify({"erro": "Ponto não encontrado"}), 404
        campos = []
        vals = []
        for chave in ("nome", "tipo", "cor", "descricao", "icone", "lat", "lng"):
            if chave in data:
                campos.append(f"{chave}=?")
                vals.append(data[chave])
        if campos:
            vals.append(id)
            conn.execute(f"UPDATE pontos SET {', '.join(campos)} WHERE id=?", vals)
        ponto = conn.execute("SELECT * FROM pontos WHERE id=?", (id,)).fetchone()
    return jsonify(dict(ponto))


@app.route("/api/pontos/<int:id>", methods=["DELETE"])
@login_required
def api_pontos_excluir(id):
    la = session.get("loteamento_ativo")
    if not la:
        return jsonify({"erro": "Nenhum loteamento ativo"}), 400
    with get_db() as conn:
        existente = conn.execute("SELECT id FROM pontos WHERE id=? AND loteamento_id=?", (id, la)).fetchone()
        if not existente:
            return jsonify({"erro": "Ponto não encontrado"}), 404
        conn.execute("DELETE FROM pontos WHERE id=?", (id,))
    return jsonify({"ok": True})


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
