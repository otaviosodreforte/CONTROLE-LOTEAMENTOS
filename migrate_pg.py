"""
Script para migrar dados do SQLite para PostgreSQL.
Uso: python migrate_pg.py

Requer DATABASE_URL configurada no .env ou environment.
"""

import os
import sys
import sqlite3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERRO: Defina DATABASE_URL no .env ou environment")
    print("Exemplo: DATABASE_URL=postgresql://user:senha@host:5432/controle_lotes")
    sys.exit(1)

DB_PATH = os.environ.get("DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "controle_loteamentos.db"
)

if not os.path.exists(DB_PATH):
    print(f"ERRO: Banco SQLite não encontrado em: {DB_PATH}")
    sys.exit(1)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERRO: Instale psycopg2-binary: pip install psycopg2-binary")
    sys.exit(1)


from modules.common import _init_pg, _PgConnection


TABELAS = [
    "usuarios",
    "permissoes",
    "formas_pagamento",
    "pessoas",
    "loteamentos",
    "usuario_loteamentos",
    "quadras",
    "lotes",
    "permutas",
    "permuta_lotes",
    "vendas",
    "pagamentos_venda",
    "pontos",
]


def conectar_sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    print(f"SQLite: {DB_PATH}")
    print(f"PG:     {DATABASE_URL}")
    print()

    print("Criando tabelas no PostgreSQL...")
    pg_raw = psycopg2.connect(DATABASE_URL)
    with _PgConnection(pg_raw) as pg:
        _init_pg(pg)
    print("OK")

    sqlite = conectar_sqlite()
    pg = psycopg2.connect(DATABASE_URL)

    try:
        print("Limpando dados existentes no PostgreSQL...")
        cur = pg.cursor()
        for tabela in reversed(TABELAS):
            cur.execute(f"DELETE FROM {tabela}")
        pg.commit()
        cur.close()
        print("OK")

        print("\nMigrando dados...")
        for tabela in TABELAS:
            cur_sqlite = sqlite.execute(f"SELECT * FROM {tabela}")
            rows = cur_sqlite.fetchall()
            if not rows:
                print(f"  {tabela}: 0 registros (vazia)")
                continue

            colunas = [desc[0] for desc in cur_sqlite.description]
            placeholders = ",".join("%s" for _ in colunas)
            colunas_str = ",".join(colunas)
            sql = f"INSERT INTO {tabela} ({colunas_str}) VALUES ({placeholders})"

            cur_pg = pg.cursor()
            for row in rows:
                valores = [row[col] for col in colunas]
                for i, col in enumerate(colunas):
                    if col in ("data_vencimento", "data_pagamento") and valores[i] == "":
                        valores[i] = None

                try:
                    cur_pg.execute(sql, valores)
                except Exception as e:
                    print(f"  ERRO em {tabela}: {e}")
                    print(f"  Dados: {valores}")
                    pg.rollback()
                    return

            pg.commit()
            cur_pg.close()
            print(f"  {tabela}: {len(rows)} registros migrados")

        print("\nResetando sequences...")
        cur = pg.cursor()
        for tabela in TABELAS:
            try:
                cur.execute(f"SELECT setval(pg_get_serial_sequence('{tabela}', 'id'), COALESCE((SELECT MAX(id) FROM {tabela}), 0))")
            except Exception:
                pass
        pg.commit()
        cur.close()

        print("\nMigração concluída com sucesso!")
    finally:
        sqlite.close()
        pg.close()


if __name__ == "__main__":
    main()
