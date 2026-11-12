import argparse
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("TECHSTOCK_SECRET_KEY", "dev-key-change-me"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
DATABASE_PATH = Path(
    os.environ.get("TECHSTOCK_DATABASE", str(Path(__file__).with_name("techstock.db")))
)
PAYMENT_METHODS = {"PIX", "CARTAO_CREDITO", "BOLETO_FATURADO"}
MAX_CLIENT_NAME_LENGTH = 50
MAX_QUANTITY_PER_PRODUCT = 250
T = TypeVar("T", bound=Callable[..., Any])


class CheckoutError(Exception):
    """Expected business validation failure during checkout."""


def documento_cliente_valido(documento: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:[0-9]{11}|[0-9]{14}|[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}"
            r"|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})",
            documento.strip(),
        )
    )


def obter_conexao() -> sqlite3.Connection:
    conexao = sqlite3.connect(DATABASE_PATH, timeout=10)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 10000")
    return conexao


def criar_tabelas() -> None:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fornecedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                razao_social TEXT NOT NULL,
                cnpj TEXT NOT NULL UNIQUE,
                telefone TEXT NOT NULL,
                email TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                nome_comercial TEXT NOT NULL,
                fabricante TEXT NOT NULL,
                classificacao TEXT NOT NULL,
                especificacao_tecnica TEXT,
                modalidade_licenciamento TEXT DEFAULT 'N/A',
                valor_unitario REAL NOT NULL,
                volume_estoque INTEGER NOT NULL,
                ponto_reposicao INTEGER NOT NULL,
                localizacao TEXT NOT NULL,
                categoria_id INTEGER,
                fornecedor_id INTEGER,
                FOREIGN KEY (categoria_id) REFERENCES categorias(id),
                FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo_operacao TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                justificativa TEXT NOT NULL,
                produto_id INTEGER,
                FOREIGN KEY (produto_id) REFERENCES produtos(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_completo TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL,
                perfil_acesso TEXT NOT NULL
                    CHECK (perfil_acesso IN ('OPERADOR', 'GERENTE')),
                status TEXT NOT NULL DEFAULT 'ATIVO'
                    CHECK (status IN ('ATIVO', 'INATIVO'))
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS vendas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_pedido TEXT NOT NULL UNIQUE,
                data_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                cliente_nome TEXT NOT NULL,
                cliente_documento TEXT NOT NULL,
                forma_pagamento TEXT NOT NULL CHECK (
                    forma_pagamento IN ('PIX', 'CARTAO_CREDITO', 'BOLETO_FATURADO')
                ),
                valor_total REAL NOT NULL CHECK (valor_total >= 0),
                status_venda TEXT NOT NULL DEFAULT 'CONCLUIDA'
                    CHECK (status_venda IN ('CONCLUIDA', 'CANCELADA')),
                chave_autenticidade TEXT NOT NULL,
                usuario_id INTEGER NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
            """
        )
        venda_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(vendas)")
        }
        if "chave_autenticidade" not in venda_columns:
            cursor.execute("ALTER TABLE vendas ADD COLUMN chave_autenticidade TEXT")
        cursor.execute(
            """
            SELECT id FROM vendas
            WHERE chave_autenticidade IS NULL OR chave_autenticidade = ''
            """
        )
        for venda in cursor.fetchall():
            cursor.execute(
                "UPDATE vendas SET chave_autenticidade = ? WHERE id = ?",
                (secrets.token_urlsafe(32), venda["id"]),
            )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vendas_chave_autenticidade
            ON vendas (chave_autenticidade)
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_vendas_chave_autenticidade_insert
            BEFORE INSERT ON vendas
            WHEN NEW.chave_autenticidade IS NULL
                 OR trim(NEW.chave_autenticidade) = ''
            BEGIN
                SELECT RAISE(ABORT, 'Chave de autenticidade obrigatória.');
            END
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_vendas_chave_autenticidade_update
            BEFORE UPDATE OF chave_autenticidade ON vendas
            WHEN NEW.chave_autenticidade IS NULL
                 OR trim(NEW.chave_autenticidade) = ''
            BEGIN
                SELECT RAISE(ABORT, 'Chave de autenticidade obrigatória.');
            END
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS itens_venda (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venda_id INTEGER NOT NULL,
                produto_id INTEGER NOT NULL,
                quantidade_vendida INTEGER NOT NULL CHECK (quantidade_vendida > 0),
                preco_unitario_praticado REAL NOT NULL
                    CHECK (preco_unitario_praticado > 0),
                subtotal_item REAL NOT NULL CHECK (subtotal_item >= 0),
                FOREIGN KEY (venda_id) REFERENCES vendas(id),
                FOREIGN KEY (produto_id) REFERENCES produtos(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS auditoria_precos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER,
                sku TEXT NOT NULL,
                usuario_id INTEGER NOT NULL,
                data_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                valor_anterior REAL NOT NULL CHECK (valor_anterior > 0),
                valor_novo REAL NOT NULL CHECK (valor_novo > 0),
                FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE SET NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
            """
        )

        cursor.execute("INSERT OR IGNORE INTO categorias (nome) VALUES ('Hardware')")
        cursor.execute("INSERT OR IGNORE INTO categorias (nome) VALUES ('Software')")
        cursor.execute("INSERT OR IGNORE INTO categorias (nome) VALUES ('Periféricos')")
        cursor.execute(
            """
            INSERT OR IGNORE INTO fornecedores
                (razao_social, cnpj, telefone, email)
            VALUES
                ('TechStore Distribuição', '12.345.678/0001-90',
                 '(11) 98765-4321', 'contato@techstore.com')
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO fornecedores
                (razao_social, cnpj, telefone, email)
            VALUES
                ('Global Softwares Corp', '98.765.432/0001-12',
                 '(21) 91234-5678', 'vendas@globalsoft.com')
            """
        )

        sample_products = (
            (
                "PROC-I7-147K",
                "Processador Intel Core i7-14700K",
                "Intel",
                "Hardware",
                "LGA1700, 3.4GHz",
                "N/A",
                2200.00,
                8,
                3,
                "Prateleira A1",
                "Hardware",
                "12.345.678/0001-90",
            ),
            (
                "SSD-NVME-1TB",
                "SSD NVMe M.2 1TB Kingston",
                "Kingston",
                "Hardware",
                "Leitura 3500MB/s",
                "N/A",
                450.00,
                2,
                5,
                "Prateleira B2",
                "Hardware",
                "12.345.678/0001-90",
            ),
            (
                "M365-BUS-ANN",
                "Microsoft 365 Business Standard",
                "Microsoft",
                "Software",
                "Licenciamento corporativo cloud",
                "Assinatura Anual",
                600.00,
                50,
                10,
                "Cofre Digital",
                "Software",
                "98.765.432/0001-12",
            ),
        )
        for product in sample_products:
            cursor.execute(
                """
                INSERT OR IGNORE INTO produtos (
                    sku, nome_comercial, fabricante, classificacao,
                    especificacao_tecnica, modalidade_licenciamento,
                    valor_unitario, volume_estoque, ponto_reposicao,
                    localizacao, categoria_id, fornecedor_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    (SELECT id FROM categorias WHERE nome = ?),
                    (SELECT id FROM fornecedores WHERE cnpj = ?))
                """,
                product,
            )

        demo_users = (
            (
                "Operador TechStore",
                "operador@techstore.com",
                generate_password_hash("Operador123!"),
                "OPERADOR",
            ),
            (
                "Gerente TechStore",
                "gerente@techstore.com",
                generate_password_hash("Gerente123!"),
                "GERENTE",
            ),
        )
        cursor.executemany(
            """
            INSERT OR IGNORE INTO usuarios
                (nome_completo, email, senha_hash, perfil_acesso, status)
            VALUES (?, ?, ?, ?, 'ATIVO')
            """,
            demo_users,
        )
        conexao.commit()
    except sqlite3.Error:
        conexao.rollback()
        raise
    finally:
        cursor.close()
        conexao.close()


def usuario_atual() -> sqlite3.Row | None:
    return cast(sqlite3.Row | None, g.get("usuario"))


def exige_login(func: T) -> T:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if usuario_atual() is None:
            flash("Entre na sua conta para continuar.", "warning")
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)

    return cast(T, wrapper)


def exige_perfil(*perfis: str) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            usuario = usuario_atual()
            if usuario is None:
                flash("Entre na sua conta para continuar.", "warning")
                return redirect(url_for("login", next=request.path))
            if usuario["perfil_acesso"] not in perfis:
                abort(403)
            return func(*args, **kwargs)

        return cast(T, wrapper)

    return decorator


@app.before_request
def carregar_usuario_e_validar_csrf() -> None:
    usuario_id = session.get("usuario_id")
    g.usuario = None
    if usuario_id is not None:
        conexao = obter_conexao()
        cursor = conexao.cursor()
        try:
            cursor.execute(
                """
                SELECT id, nome_completo, email, perfil_acesso, status
                FROM usuarios
                WHERE id = ?
                """,
                (usuario_id,),
            )
            usuario = cursor.fetchone()
        finally:
            cursor.close()
            conexao.close()
        if usuario is None or usuario["status"] != "ATIVO":
            session.clear()
        else:
            g.usuario = usuario

    if request.method == "POST":
        token = session.get("_csrf_token")
        submitted_token = request.form.get("_csrf_token", "")
        if not token or not secrets.compare_digest(token, submitted_token):
            abort(400, description="Token de segurança inválido ou ausente.")


@app.context_processor
def disponibilizar_contexto() -> dict[str, Any]:
    def csrf_token() -> str:
        token = session.get("_csrf_token")
        if token is None:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return str(token)

    return {"current_user": usuario_atual(), "csrf_token": csrf_token}


@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if usuario_atual() is not None:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        conexao = obter_conexao()
        cursor = conexao.cursor()
        try:
            cursor.execute(
                """
                SELECT id, senha_hash, status
                FROM usuarios
                WHERE email = ?
                """,
                (email,),
            )
            usuario = cursor.fetchone()
        finally:
            cursor.close()
            conexao.close()

        if (
            usuario is None
            or usuario["status"] != "ATIVO"
            or not check_password_hash(usuario["senha_hash"], senha)
        ):
            flash("E-mail ou senha inválidos.", "danger")
            return render_template("login.html"), 401

        session.clear()
        session["usuario_id"] = usuario["id"]
        session.permanent = True
        destination = request.args.get("next", "")
        if not destination.startswith("/") or destination.startswith("//"):
            destination = url_for("dashboard")
        return redirect(destination)
    return render_template("login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastrar_conta() -> Any:
    if usuario_atual() is not None:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        nome_completo = request.form.get("nome_completo", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        if (
            len(nome_completo) < 2
            or "@" not in email
            or len(senha) < 8
            or senha != confirmar_senha
        ):
            flash(
                "Informe nome e e-mail válidos, uma senha com pelo menos "
                "8 caracteres e confirme a senha.",
                "danger",
            )
            return render_template("cadastro_conta.html"), 400

        conexao = obter_conexao()
        cursor = conexao.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO usuarios
                    (nome_completo, email, senha_hash, perfil_acesso, status)
                VALUES (?, ?, ?, 'OPERADOR', 'ATIVO')
                """,
                (nome_completo, email, generate_password_hash(senha)),
            )
            conexao.commit()
        except sqlite3.IntegrityError:
            conexao.rollback()
            flash("Já existe uma conta cadastrada com esse e-mail.", "danger")
            return render_template("cadastro_conta.html"), 409
        except sqlite3.Error:
            conexao.rollback()
            app.logger.exception("Falha ao criar conta de operador.")
            raise
        finally:
            cursor.close()
            conexao.close()

        flash("Conta criada. Entre com seu e-mail e senha.", "success")
        return redirect(url_for("login"))
    return render_template("cadastro_conta.html")


@app.route("/logout", methods=["GET", "POST"])
def logout() -> Any:
    session.clear()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("login"))


@app.route("/")
@exige_login
def dashboard() -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT
                p.id, p.sku, p.nome_comercial AS ativo, p.fabricante,
                c.nome AS categoria, f.razao_social AS fornecedor,
                p.valor_unitario, p.volume_estoque, p.ponto_reposicao,
                p.localizacao, p.modalidade_licenciamento
            FROM produtos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            LEFT JOIN fornecedores f ON p.fornecedor_id = f.id
            ORDER BY p.nome_comercial
            """
        )
        produtos = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template("index.html", produtos=produtos)


@app.route("/novo")
@exige_perfil("GERENTE")
def novo() -> Any:
    return redirect(url_for("cadastrar_produto"))


@app.route("/cadastrar", methods=["GET", "POST"])
@exige_perfil("GERENTE")
def cadastrar_produto() -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    if request.method == "POST":
        try:
            valor_unitario = Decimal(request.form.get("valor_unitario", ""))
            volume_estoque = int(request.form.get("volume_estoque", ""))
            ponto_reposicao = int(request.form.get("ponto_reposicao", ""))
            if valor_unitario <= 0 or volume_estoque < 0 or ponto_reposicao < 0:
                raise ValueError
            cursor.execute(
                """
                INSERT INTO produtos (
                    sku, nome_comercial, fabricante, classificacao,
                    especificacao_tecnica, modalidade_licenciamento,
                    valor_unitario, volume_estoque, ponto_reposicao,
                    localizacao, categoria_id, fornecedor_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.form.get("sku", "").strip(),
                    request.form.get("nome_comercial", "").strip(),
                    request.form.get("fabricante", "").strip(),
                    request.form.get("classificacao", "").strip(),
                    request.form.get("especificacao_tecnica", "").strip(),
                    request.form.get("modalidade_licenciamento", "N/A").strip(),
                    float(valor_unitario),
                    volume_estoque,
                    ponto_reposicao,
                    request.form.get("localizacao", "").strip(),
                    request.form.get("categoria_id"),
                    request.form.get("fornecedor_id"),
                ),
            )
            conexao.commit()
        except (InvalidOperation, ValueError, sqlite3.IntegrityError):
            conexao.rollback()
            flash(
                "Não foi possível cadastrar o produto. Confira os campos e valores.",
                "danger",
            )
            return redirect(url_for("cadastrar_produto"))
        finally:
            cursor.close()
            conexao.close()
        flash("Produto cadastrado.", "success")
        return redirect(url_for("dashboard"))

    try:
        cursor.execute("SELECT * FROM categorias ORDER BY nome")
        categorias = cursor.fetchall()
        cursor.execute("SELECT * FROM fornecedores ORDER BY razao_social")
        fornecedores = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template(
        "cadastrar.html", categorias=categorias, fornecedores=fornecedores
    )


@app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@exige_perfil("GERENTE")
def editar_produto(produto_id: int) -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,))
        produto = cursor.fetchone()
        if produto is None:
            abort(404)

        if request.method == "POST":
            try:
                sku = request.form.get("sku", "").strip()
                nome_comercial = request.form.get("nome_comercial", "").strip()
                fabricante = request.form.get("fabricante", "").strip()
                classificacao = request.form.get("classificacao", "")
                especificacao_tecnica = request.form.get(
                    "especificacao_tecnica", ""
                ).strip()
                modalidade_licenciamento = (
                    request.form.get("modalidade_licenciamento", "N/A").strip()
                    or "N/A"
                )
                valor_unitario = Decimal(
                    request.form.get("valor_unitario", "")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                ponto_reposicao = int(request.form.get("ponto_reposicao", ""))
                localizacao = request.form.get("localizacao", "").strip()
                categoria_id = int(request.form.get("categoria_id", ""))
                fornecedor_id = int(request.form.get("fornecedor_id", ""))
                if (
                    not sku
                    or not nome_comercial
                    or not fabricante
                    or classificacao not in {"Hardware", "Software", "Periférico"}
                    or not localizacao
                    or valor_unitario <= 0
                    or ponto_reposicao < 0
                    or categoria_id <= 0
                    or fornecedor_id <= 0
                ):
                    raise ValueError
            except (InvalidOperation, ValueError):
                flash("Confira os campos e valores informados.", "danger")
                return redirect(url_for("editar_produto", produto_id=produto_id))

            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    """
                    SELECT valor_unitario FROM produtos WHERE id = ?
                    """,
                    (produto_id,),
                )
                produto_atual = cursor.fetchone()
                if produto_atual is None:
                    abort(404)
                valor_anterior = Decimal(str(produto_atual["valor_unitario"]))
                cursor.execute(
                    """
                    UPDATE produtos
                    SET sku = ?, nome_comercial = ?, fabricante = ?,
                        classificacao = ?, especificacao_tecnica = ?,
                        modalidade_licenciamento = ?, valor_unitario = ?,
                        ponto_reposicao = ?, localizacao = ?,
                        categoria_id = ?, fornecedor_id = ?
                    WHERE id = ?
                    """,
                    (
                        sku,
                        nome_comercial,
                        fabricante,
                        classificacao,
                        especificacao_tecnica,
                        modalidade_licenciamento,
                        float(valor_unitario),
                        ponto_reposicao,
                        localizacao,
                        categoria_id,
                        fornecedor_id,
                        produto_id,
                    ),
                )
                if valor_anterior != valor_unitario:
                    usuario = usuario_atual()
                    if usuario is None:
                        abort(401)
                    cursor.execute(
                        """
                        INSERT INTO auditoria_precos (
                            produto_id, sku, usuario_id, valor_anterior, valor_novo
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            produto_id,
                            sku,
                            usuario["id"],
                            float(valor_anterior),
                            float(valor_unitario),
                        ),
                    )
                conexao.commit()
            except sqlite3.IntegrityError:
                if conexao.in_transaction:
                    conexao.rollback()
                flash(
                    "Não foi possível salvar. Verifique se o SKU e os vínculos "
                    "com categoria e fornecedor são válidos.",
                    "danger",
                )
                return redirect(url_for("editar_produto", produto_id=produto_id))
            except sqlite3.Error:
                if conexao.in_transaction:
                    conexao.rollback()
                app.logger.exception("Falha ao atualizar produto %s.", produto_id)
                raise
            flash("Produto atualizado.", "success")
            return redirect(url_for("dashboard"))

        cursor.execute("SELECT * FROM categorias ORDER BY nome")
        categorias = cursor.fetchall()
        cursor.execute("SELECT * FROM fornecedores ORDER BY razao_social")
        fornecedores = cursor.fetchall()
        return render_template(
            "cadastrar.html",
            produto=produto,
            categorias=categorias,
            fornecedores=fornecedores,
        )
    finally:
        cursor.close()
        conexao.close()


@app.route("/produtos/<int:produto_id>/excluir", methods=["POST"])
@exige_perfil("GERENTE")
def excluir_produto(produto_id: int) -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT sku FROM produtos WHERE id = ?", (produto_id,))
        produto = cursor.fetchone()
        if produto is None:
            conexao.rollback()
            flash("Produto não encontrado.", "warning")
            return redirect(url_for("dashboard"))
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM movimentacoes WHERE produto_id = ?
                UNION ALL
                SELECT 1 FROM itens_venda WHERE produto_id = ?
            )
            """,
            (produto_id, produto_id),
        )
        if cursor.fetchone()[0]:
            conexao.rollback()
            flash(
                "Este produto possui histórico de estoque ou vendas e não pode "
                "ser excluído.",
                "danger",
            )
            return redirect(url_for("dashboard"))
        cursor.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        conexao.commit()
    except sqlite3.Error:
        if conexao.in_transaction:
            conexao.rollback()
        app.logger.exception("Falha ao excluir produto %s.", produto_id)
        raise
    finally:
        cursor.close()
        conexao.close()
    flash(f"Produto {produto['sku']} excluído.", "success")
    return redirect(url_for("dashboard"))


@app.route("/movimentar", methods=["GET", "POST"])
@exige_perfil("GERENTE")
def movimentar() -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    if request.method == "POST":
        try:
            produto_id = int(request.form.get("produto_id", ""))
            quantidade = int(request.form.get("quantidade", ""))
            tipo_operacao = request.form.get("tipo_operacao", "")
            justificativa = request.form.get("justificativa", "").strip()
            if (
                quantidade <= 0
                or tipo_operacao not in {"Entrada", "Saída"}
                or not justificativa
            ):
                raise ValueError
            cursor.execute(
                "SELECT volume_estoque FROM produtos WHERE id = ?", (produto_id,)
            )
            produto = cursor.fetchone()
            if produto is None:
                raise ValueError
            if tipo_operacao == "Saída" and produto["volume_estoque"] < quantidade:
                flash("Estoque insuficiente para essa movimentação.", "danger")
                return redirect(url_for("movimentar"))
            delta = quantidade if tipo_operacao == "Entrada" else -quantidade
            cursor.execute(
                "UPDATE produtos SET volume_estoque = volume_estoque + ? WHERE id = ?",
                (delta, produto_id),
            )
            cursor.execute(
                """
                INSERT INTO movimentacoes
                    (tipo_operacao, quantidade, justificativa, produto_id)
                VALUES (?, ?, ?, ?)
                """,
                (tipo_operacao, quantidade, justificativa, produto_id),
            )
            conexao.commit()
        except (ValueError, sqlite3.IntegrityError):
            conexao.rollback()
            flash("Não foi possível registrar a movimentação.", "danger")
            return redirect(url_for("movimentar"))
        finally:
            cursor.close()
            conexao.close()
        flash("Movimentação registrada.", "success")
        return redirect(url_for("dashboard"))

    try:
        cursor.execute("SELECT * FROM produtos ORDER BY nome_comercial")
        produtos = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template("movimentar.html", produtos=produtos)


@app.route("/vendas/nova")
@exige_perfil("OPERADOR", "GERENTE")
def nova_venda() -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT id, sku, nome_comercial, classificacao, modalidade_licenciamento,
                   valor_unitario, volume_estoque
            FROM produtos
            ORDER BY nome_comercial
            """
        )
        produtos = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template(
        "pdv.html", produtos=[dict(produto) for produto in produtos], cart_items=[]
    )


def renderizar_pdv_com_erro(
    mensagem: str, cart_items: list[dict[str, int]] | None = None
) -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT id, sku, nome_comercial, classificacao, modalidade_licenciamento,
                   valor_unitario, volume_estoque
            FROM produtos
            ORDER BY nome_comercial
            """
        )
        produtos = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return (
        render_template(
            "pdv.html",
            produtos=[dict(produto) for produto in produtos],
            cart_items=cart_items or [],
            error=mensagem,
        ),
        400,
    )


def criar_numero_pedido(cursor: sqlite3.Cursor) -> str:
    ano = datetime.now().year
    prefixo = f"PED-{ano}-"
    cursor.execute(
        """
        SELECT COALESCE(MAX(CAST(SUBSTR(numero_pedido, ?) AS INTEGER)), 0) + 1
        FROM vendas
        WHERE numero_pedido LIKE ?
        """,
        (len(prefixo) + 1, f"{prefixo}%"),
    )
    sequencia = int(cursor.fetchone()[0])
    return f"{prefixo}{sequencia:04d}"


def executar_checkout(
    itens: dict[int, int],
    cliente_nome: str,
    cliente_documento: str,
    forma_pagamento: str,
    usuario_id: int,
) -> tuple[int, str, Decimal]:
    if not itens or forma_pagamento not in PAYMENT_METHODS:
        raise CheckoutError("Carrinho ou forma de pagamento inválidos.")
    if not cliente_nome.strip() or len(cliente_nome.strip()) > MAX_CLIENT_NAME_LENGTH:
        raise CheckoutError(
            f"O nome do cliente deve ter até {MAX_CLIENT_NAME_LENGTH} caracteres."
        )
    if not documento_cliente_valido(cliente_documento):
        raise CheckoutError("Informe um CPF com 11 ou CNPJ com 14 dígitos.")
    if any(
        produto_id <= 0 or quantidade <= 0
        for produto_id, quantidade in itens.items()
    ):
        raise CheckoutError("As quantidades devem ser números inteiros positivos.")
    if any(quantidade > MAX_QUANTITY_PER_PRODUCT for quantidade in itens.values()):
        raise CheckoutError(
            f"A quantidade máxima permitida por produto é "
            f"{MAX_QUANTITY_PER_PRODUCT} unidades."
        )

    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        produtos: dict[int, sqlite3.Row] = {}
        for produto_id, quantidade in itens.items():
            cursor.execute(
                """
                SELECT id, sku, nome_comercial, valor_unitario, volume_estoque
                FROM produtos
                WHERE id = ?
                """,
                (produto_id,),
            )
            produto = cursor.fetchone()
            if produto is None:
                raise CheckoutError(f"Produto {produto_id} não encontrado.")
            if produto["volume_estoque"] < quantidade:
                raise CheckoutError(
                    f"Estoque insuficiente para {produto['nome_comercial']}: "
                    f"solicitado {quantidade}, disponível {produto['volume_estoque']}."
                )
            produtos[produto_id] = produto

        valores: dict[int, tuple[Decimal, Decimal]] = {}
        total = Decimal("0.00")
        for produto_id, quantidade in itens.items():
            preco = Decimal(str(produtos[produto_id]["valor_unitario"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            subtotal = (preco * quantidade).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            valores[produto_id] = (preco, subtotal)
            total += subtotal

        numero_pedido = criar_numero_pedido(cursor)
        chave_autenticidade = secrets.token_urlsafe(32)
        cursor.execute(
            """
            INSERT INTO vendas (
                numero_pedido, cliente_nome, cliente_documento, forma_pagamento,
                valor_total, status_venda, chave_autenticidade, usuario_id
            )
            VALUES (?, ?, ?, ?, ?, 'CONCLUIDA', ?, ?)
            """,
            (
                numero_pedido,
                cliente_nome,
                cliente_documento,
                forma_pagamento,
                float(total),
                chave_autenticidade,
                usuario_id,
            ),
        )
        venda_id = int(cursor.lastrowid)

        for produto_id, quantidade in itens.items():
            produto = produtos[produto_id]
            preco, subtotal = valores[produto_id]
            cursor.execute(
                """
                INSERT INTO itens_venda (
                    venda_id, produto_id, quantidade_vendida,
                    preco_unitario_praticado, subtotal_item
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (venda_id, produto_id, quantidade, float(preco), float(subtotal)),
            )
            cursor.execute(
                """
                UPDATE produtos
                SET volume_estoque = volume_estoque - ?
                WHERE id = ? AND volume_estoque >= ?
                """,
                (quantidade, produto_id, quantidade),
            )
            if cursor.rowcount != 1:
                raise CheckoutError(
                    f"Estoque insuficiente para {produto['nome_comercial']}."
                )
            cursor.execute(
                """
                INSERT INTO movimentacoes
                    (tipo_operacao, quantidade, justificativa, produto_id)
                VALUES ('Saída', ?, ?, ?)
                """,
                (quantidade, f"Venda {numero_pedido}", produto_id),
            )
        conexao.commit()
        return venda_id, numero_pedido, total
    except CheckoutError:
        if conexao.in_transaction:
            conexao.rollback()
        raise
    except sqlite3.Error:
        if conexao.in_transaction:
            conexao.rollback()
        app.logger.exception("Falha ao processar checkout.")
        raise
    finally:
        cursor.close()
        conexao.close()


@app.route("/vendas/checkout", methods=["POST"])
@exige_perfil("OPERADOR", "GERENTE")
def checkout() -> Any:
    usuario = usuario_atual()
    if usuario is None:
        abort(401)

    produto_ids = request.form.getlist("produto_id")
    quantidades = request.form.getlist("quantidade")
    if not produto_ids or len(produto_ids) != len(quantidades):
        return renderizar_pdv_com_erro("Adicione ao menos um item válido ao carrinho.")
    try:
        itens: dict[int, int] = {}
        for raw_id, raw_quantity in zip(produto_ids, quantidades):
            produto_id = int(raw_id)
            quantidade = int(raw_quantity)
            if produto_id <= 0 or quantidade <= 0:
                raise ValueError
            itens[produto_id] = itens.get(produto_id, 0) + quantidade
            if itens[produto_id] > MAX_QUANTITY_PER_PRODUCT:
                cart_items = [
                    {"id": item_id, "quantidade": item_quantity}
                    for item_id, item_quantity in itens.items()
                ]
                return renderizar_pdv_com_erro(
                    f"A quantidade máxima permitida por produto é "
                    f"{MAX_QUANTITY_PER_PRODUCT} unidades.",
                    cart_items,
                )
        cliente_nome = request.form.get("cliente_nome", "").strip()
        cliente_documento = request.form.get("cliente_documento", "").strip()
        forma_pagamento = request.form.get("forma_pagamento", "")
        if not cliente_nome or forma_pagamento not in PAYMENT_METHODS:
            raise ValueError
    except ValueError:
        return renderizar_pdv_com_erro(
            "Informe cliente, documento, pagamento e quantidades inteiras positivas."
        )
    if len(cliente_nome) > MAX_CLIENT_NAME_LENGTH:
        return renderizar_pdv_com_erro(
            f"O nome do cliente deve ter até {MAX_CLIENT_NAME_LENGTH} caracteres.",
            [
                {"id": produto_id, "quantidade": quantidade}
                for produto_id, quantidade in itens.items()
            ],
        )
    if not documento_cliente_valido(cliente_documento):
        return renderizar_pdv_com_erro(
            "Informe um CPF com 11 ou CNPJ com 14 dígitos.",
            [
                {"id": produto_id, "quantidade": quantidade}
                for produto_id, quantidade in itens.items()
            ],
        )

    cart_items = [
        {"id": produto_id, "quantidade": quantidade}
        for produto_id, quantidade in itens.items()
    ]
    try:
        venda_id, numero_pedido, total = executar_checkout(
            itens,
            cliente_nome,
            cliente_documento,
            forma_pagamento,
            int(usuario["id"]),
        )
    except CheckoutError as erro:
        return renderizar_pdv_com_erro(str(erro), cart_items)

    return redirect(url_for("recibo", venda_id=venda_id))


@app.route("/vendas/verificar")
def verificar_recibo() -> Any:
    pedido = request.args.get("pedido", "").strip()
    chave = request.args.get("chave", "").strip()
    resultado = None
    verificacao_realizada = bool(pedido or chave)
    if pedido and chave:
        conexao = obter_conexao()
        cursor = conexao.cursor()
        try:
            cursor.execute(
                """
                SELECT numero_pedido, data_hora, valor_total, status_venda
                FROM vendas
                WHERE numero_pedido = ? AND chave_autenticidade = ?
                """,
                (pedido, chave),
            )
            resultado = cursor.fetchone()
        finally:
            cursor.close()
            conexao.close()
    return render_template(
        "verificar_recibo.html",
        pedido=pedido,
        chave=chave,
        resultado=resultado,
        verificacao_realizada=verificacao_realizada,
    )


@app.route("/vendas/<int:venda_id>")
@exige_perfil("OPERADOR", "GERENTE")
def recibo(venda_id: int) -> Any:
    usuario = usuario_atual()
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT v.*, u.nome_completo AS operador_nome, u.email AS operador_email
            FROM vendas v
            JOIN usuarios u ON u.id = v.usuario_id
            WHERE v.id = ?
            """,
            (venda_id,),
        )
        venda = cursor.fetchone()
        if venda is None:
            abort(404)
        if (
            usuario is not None
            and usuario["perfil_acesso"] == "OPERADOR"
            and venda["usuario_id"] != usuario["id"]
        ):
            abort(404)
        cursor.execute(
            """
            SELECT iv.quantidade_vendida, iv.preco_unitario_praticado,
                   iv.subtotal_item, p.sku, p.nome_comercial, p.classificacao,
                   p.modalidade_licenciamento
            FROM itens_venda iv
            JOIN produtos p ON p.id = iv.produto_id
            WHERE iv.venda_id = ?
            ORDER BY iv.id
            """,
            (venda_id,),
        )
        itens = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template("recibo.html", venda=venda, itens=itens)


@app.route("/vendas/<int:venda_id>/cancelar", methods=["POST"])
@exige_perfil("GERENTE")
def cancelar_venda(venda_id: int) -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT numero_pedido, status_venda FROM vendas WHERE id = ?",
            (venda_id,),
        )
        venda = cursor.fetchone()
        if venda is None:
            raise CheckoutError("Venda não encontrada.")
        if venda["status_venda"] != "CONCLUIDA":
            raise CheckoutError("A venda já está cancelada.")

        cursor.execute(
            """
            SELECT produto_id, quantidade_vendida
            FROM itens_venda
            WHERE venda_id = ?
            """,
            (venda_id,),
        )
        itens = cursor.fetchall()
        for item in itens:
            cursor.execute(
                "UPDATE produtos SET volume_estoque = volume_estoque + ? WHERE id = ?",
                (item["quantidade_vendida"], item["produto_id"]),
            )
            cursor.execute(
                """
                INSERT INTO movimentacoes
                    (tipo_operacao, quantidade, justificativa, produto_id)
                VALUES ('Entrada', ?, ?, ?)
                """,
                (
                    item["quantidade_vendida"],
                    f"Estorno da venda {venda['numero_pedido']}",
                    item["produto_id"],
                ),
            )
        cursor.execute(
            "UPDATE vendas SET status_venda = 'CANCELADA' WHERE id = ?",
            (venda_id,),
        )
        conexao.commit()
    except CheckoutError as erro:
        if conexao.in_transaction:
            conexao.rollback()
        flash(str(erro), "danger")
    except sqlite3.Error:
        if conexao.in_transaction:
            conexao.rollback()
        app.logger.exception("Falha ao cancelar venda %s.", venda_id)
        raise
    else:
        flash("Venda cancelada e estoque estornado.", "success")
    finally:
        cursor.close()
        conexao.close()
    return redirect(url_for("relatorios"))


@app.route("/relatorios")
@exige_perfil("GERENTE")
def relatorios() -> Any:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT v.id, v.numero_pedido, v.data_hora, v.cliente_nome,
                   v.valor_total, v.status_venda, u.nome_completo AS operador_nome
            FROM vendas v
            JOIN usuarios u ON u.id = v.usuario_id
            ORDER BY v.id DESC
            """
        )
        vendas = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    return render_template("relatorios.html", vendas=vendas)


def imprimir_tabelas_terminal() -> None:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT p.sku, p.nome_comercial, p.fabricante, p.classificacao,
                   p.modalidade_licenciamento, p.valor_unitario, p.volume_estoque
            FROM produtos p
            ORDER BY p.nome_comercial
            """
        )
        produtos = cursor.fetchall()
        cursor.execute(
            """
            SELECT v.numero_pedido, v.cliente_nome, u.nome_completo AS operador,
                   v.forma_pagamento, v.valor_total, v.status_venda
            FROM vendas v
            JOIN usuarios u ON u.id = v.usuario_id
            ORDER BY v.id DESC
            """
        )
        vendas = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()

    colunas_produtos = (
        ("SKU", 18),
        ("PRODUTO", 30),
        ("FABRICANTE", 16),
        ("TIPO", 17),
        ("PREÇO", 12),
        ("ESTOQUE", 8),
    )
    colunas_vendas = (
        ("PEDIDO", 18),
        ("CLIENTE", 19),
        ("OPERADOR", 16),
        ("PAGAMENTO", 17),
        ("TOTAL", 13),
        ("STATUS", 12),
    )

    def formatar(valor: object, largura: int) -> str:
        texto = str(valor)
        if len(texto) > largura:
            texto = texto[: largura - 3] + "..."
        return texto.ljust(largura)

    def imprimir_tabela(
        titulo: str,
        colunas: tuple[tuple[str, int], ...],
        linhas: list[tuple[object, ...]],
        vazio: str,
    ) -> None:
        separador = "+-" + "-+-".join("-" * largura for _, largura in colunas) + "-+"
        print(f"\n{titulo}")
        print(separador)
        cabecalho = " | ".join(
            formatar(rotulo, largura) for rotulo, largura in colunas
        )
        print(f"| {cabecalho} |")
        print(separador)
        if not linhas:
            print(vazio)
        for valores in linhas:
            celulas = " | ".join(
                formatar(valor, largura)
                for valor, (_, largura) in zip(valores, colunas)
            )
            print(f"| {celulas} |")
        print(separador)

    linhas_produtos = []
    for produto in produtos:
        tipo = (
            "Licença digital"
            if produto["classificacao"] == "Software"
            or produto["modalidade_licenciamento"] not in (None, "", "N/A")
            else "Produto físico"
        )
        valores = (
            produto["sku"],
            produto["nome_comercial"],
            produto["fabricante"],
            tipo,
            f"R$ {produto['valor_unitario']:.2f}",
            produto["volume_estoque"],
        )
        linhas_produtos.append(valores)

    linhas_vendas = []
    for venda in vendas:
        linhas_vendas.append(
            (
                venda["numero_pedido"],
                venda["cliente_nome"],
                venda["operador"],
                venda["forma_pagamento"].replace("_", " "),
                f"R$ {venda['valor_total']:.2f}",
                venda["status_venda"],
            )
        )

    print("TECHSTOCK MANAGER - RESUMO DO SISTEMA")
    imprimir_tabela(
        "CATÁLOGO / ESTOQUE",
        colunas_produtos,
        linhas_produtos,
        "Nenhum produto cadastrado.",
    )
    imprimir_tabela(
        "VENDAS / FATURAMENTO",
        colunas_vendas,
        linhas_vendas,
        "Nenhuma venda registrada.",
    )
    print(f"Total de produtos: {len(produtos)}")
    print(f"Total de vendas: {len(vendas)}")


def imprimir_tabela_terminal(
    titulo: str, cabecalhos: tuple[str, ...], linhas: list[tuple[object, ...]]
) -> None:
    larguras = [
        min(32, max([len(cabecalho), *(len(str(linha[i])) for linha in linhas)]))
        for i, cabecalho in enumerate(cabecalhos)
    ]

    def formatar(valor: object, largura: int) -> str:
        texto = str(valor)
        if len(texto) > largura:
            texto = texto[: largura - 3] + "..."
        return texto.ljust(largura)

    separador = "+-" + "-+-".join("-" * largura for largura in larguras) + "-+"
    print(f"\n{titulo}")
    print(separador)
    print(
        "| "
        + " | ".join(
            formatar(valor, largura)
            for valor, largura in zip(cabecalhos, larguras)
        )
        + " |"
    )
    print(separador)
    if not linhas:
        print("| Nenhum registro encontrado.")
    for linha in linhas:
        print(
            "| "
            + " | ".join(
                formatar(valor, largura) for valor, largura in zip(linha, larguras)
            )
            + " |"
        )
    print(separador)


def terminal_consultar_produtos() -> list[sqlite3.Row]:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT id, sku, nome_comercial, fabricante, classificacao,
                   modalidade_licenciamento, valor_unitario, volume_estoque
            FROM produtos
            ORDER BY nome_comercial
            """
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()


def terminal_mostrar_catalogo() -> None:
    produtos = terminal_consultar_produtos()
    linhas = [
        (
            p["sku"],
            p["nome_comercial"],
            p["fabricante"],
            (
                "Licença digital"
                if p["classificacao"] == "Software"
                or p["modalidade_licenciamento"] not in (None, "", "N/A")
                else "Produto físico"
            ),
            f"R$ {p['valor_unitario']:.2f}",
            p["volume_estoque"],
        )
        for p in produtos
    ]
    imprimir_tabela_terminal(
        "CATÁLOGO E ESTOQUE",
        ("SKU", "Produto", "Fabricante", "Tipo", "Preço", "Estoque"),
        linhas,
    )


def terminal_listar_vendas(usuario: sqlite3.Row) -> list[sqlite3.Row]:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        if usuario["perfil_acesso"] == "GERENTE":
            cursor.execute(
                """
                SELECT v.id, v.numero_pedido, v.data_hora, v.cliente_nome,
                       u.nome_completo AS operador, v.forma_pagamento,
                       v.valor_total, v.status_venda
                FROM vendas v JOIN usuarios u ON u.id = v.usuario_id
                ORDER BY v.id DESC
                """
            )
        else:
            cursor.execute(
                """
                SELECT v.id, v.numero_pedido, v.data_hora, v.cliente_nome,
                       u.nome_completo AS operador, v.forma_pagamento,
                       v.valor_total, v.status_venda
                FROM vendas v JOIN usuarios u ON u.id = v.usuario_id
                WHERE v.usuario_id = ?
                ORDER BY v.id DESC
                """,
                (usuario["id"],),
            )
        return cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()


def terminal_mostrar_vendas(usuario: sqlite3.Row, abrir_recibo: bool = True) -> None:
    vendas = terminal_listar_vendas(usuario)
    linhas = [
        (
            venda["id"],
            venda["numero_pedido"],
            venda["cliente_nome"],
            venda["operador"],
            venda["forma_pagamento"].replace("_", " "),
            f"R$ {venda['valor_total']:.2f}",
            venda["status_venda"],
        )
        for venda in vendas
    ]
    imprimir_tabela_terminal(
        "VENDAS E RECIBOS",
        ("ID", "Pedido", "Cliente", "Operador", "Pagamento", "Total", "Status"),
        linhas,
    )
    if not vendas or not abrir_recibo:
        return
    resposta = input("Digite o ID da venda para abrir o recibo (Enter para voltar): ")
    if resposta.strip():
        try:
            terminal_mostrar_recibo(int(resposta), usuario)
        except ValueError:
            print("ID inválido.")


def terminal_mostrar_recibo(venda_id: int, usuario: sqlite3.Row) -> None:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT v.*, u.nome_completo AS operador, u.email AS operador_email
            FROM vendas v JOIN usuarios u ON u.id = v.usuario_id
            WHERE v.id = ?
            """,
            (venda_id,),
        )
        venda = cursor.fetchone()
        if (
            venda is None
            or (
                usuario["perfil_acesso"] == "OPERADOR"
                and venda["usuario_id"] != usuario["id"]
            )
        ):
            print("Venda não encontrada ou sem permissão.")
            return
        cursor.execute(
            """
            SELECT p.sku, p.nome_comercial, p.classificacao,
                   p.modalidade_licenciamento, iv.quantidade_vendida,
                   iv.preco_unitario_praticado, iv.subtotal_item
            FROM itens_venda iv JOIN produtos p ON p.id = iv.produto_id
            WHERE iv.venda_id = ?
            ORDER BY iv.id
            """,
            (venda_id,),
        )
        itens = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()

    print("\n" + "=" * 64)
    print("TECHSTORE INFORMÁTICA - RECIBO / ORDEM DE FATURAMENTO")
    print("=" * 64)
    print(f"Pedido: {venda['numero_pedido']} | Data: {venda['data_hora']}")
    print(f"Operador: {venda['operador']} ({venda['operador_email']})")
    print(f"Cliente: {venda['cliente_nome']} | Documento: {venda['cliente_documento']}")
    print(
        f"Pagamento: {venda['forma_pagamento']} | Status: {venda['status_venda']}"
    )
    linhas = []
    for item in itens:
        tipo = (
            "Licença digital"
            if item["classificacao"] == "Software"
            or item["modalidade_licenciamento"] not in (None, "", "N/A")
            else "Produto físico"
        )
        linhas.append(
            (
                item["sku"],
                item["nome_comercial"],
                tipo,
                item["quantidade_vendida"],
                f"R$ {item['preco_unitario_praticado']:.2f}",
                f"R$ {item['subtotal_item']:.2f}",
            )
        )
    imprimir_tabela_terminal(
        "ITENS DA VENDA",
        ("SKU", "Produto", "Tipo", "Qtd.", "Unitário", "Subtotal"),
        linhas,
    )
    print(f"TOTAL: R$ {venda['valor_total']:.2f}")
    print("=" * 64)


def terminal_cadastrar_conta() -> None:
    nome = input("Nome completo: ").strip()
    email = input("E-mail para acesso: ").strip().lower()
    senha = input("Senha (mínimo 8 caracteres): ")
    if len(nome) < 2 or "@" not in email or len(senha) < 8:
        print("Dados inválidos. Informe nome, e-mail válido e senha de 8 caracteres.")
        return

    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO usuarios (nome_completo, email, senha_hash, perfil_acesso, status)
            VALUES (?, ?, ?, 'OPERADOR', 'ATIVO')
            """,
            (nome, email, generate_password_hash(senha)),
        )
        conexao.commit()
        print("Conta de operador criada. Faça login para continuar.")
    except sqlite3.IntegrityError:
        conexao.rollback()
        print("Já existe uma conta com esse e-mail.")
    finally:
        cursor.close()
        conexao.close()


def terminal_login() -> sqlite3.Row | None:
    email = input("E-mail: ").strip().lower()
    senha = input("Senha: ")
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            SELECT id, nome_completo, email, senha_hash, perfil_acesso, status
            FROM usuarios WHERE email = ?
            """,
            (email,),
        )
        usuario = cursor.fetchone()
        if (
            usuario is None
            or usuario["status"] != "ATIVO"
            or not check_password_hash(usuario["senha_hash"], senha)
        ):
            print("E-mail ou senha inválidos.")
            return None
        print(f"Bem-vindo(a), {usuario['nome_completo']} ({usuario['perfil_acesso']}).")
        return usuario
    finally:
        cursor.close()
        conexao.close()


def terminal_nova_venda(usuario: sqlite3.Row) -> None:
    produtos = terminal_consultar_produtos()
    if not produtos:
        print("Não existem produtos cadastrados.")
        return
    linhas = [
        (
            produto["sku"],
            produto["nome_comercial"],
            f"R$ {produto['valor_unitario']:.2f}",
            produto["volume_estoque"],
        )
        for produto in produtos
    ]
    imprimir_tabela_terminal(
        "PDV - PRODUTOS DISPONÍVEIS",
        ("SKU", "Produto", "Preço", "Estoque"),
        linhas,
    )
    por_sku = {produto["sku"].casefold(): produto for produto in produtos}
    itens: dict[int, int] = {}
    while True:
        sku = input("SKU do produto (F finalizar, Q cancelar): ").strip()
        if sku.casefold() == "q":
            print("Venda cancelada sem alterações.")
            return
        if sku.casefold() == "f":
            break
        produto = por_sku.get(sku.casefold())
        if produto is None:
            print("SKU não encontrado.")
            continue
        try:
            quantidade = int(input("Quantidade: ").strip())
            if quantidade <= 0:
                raise ValueError
        except ValueError:
            print("Informe uma quantidade inteira maior que zero.")
            continue
        itens[produto["id"]] = itens.get(produto["id"], 0) + quantidade
        print(f"Adicionado: {produto['nome_comercial']} x {quantidade}")

    if not itens:
        print("Carrinho vazio; nenhuma venda foi registrada.")
        return
    try:
        cliente_nome = input("Nome / razão social do cliente: ").strip()
        cliente_documento = input("CPF/CNPJ: ").strip()
        print("Forma de pagamento: 1) PIX  2) Cartão de crédito  3) Boleto faturado")
        pagamentos = {"1": "PIX", "2": "CARTAO_CREDITO", "3": "BOLETO_FATURADO"}
        forma_pagamento = pagamentos[input("Selecione 1, 2 ou 3: ").strip()]
        if not cliente_nome or not cliente_documento:
            raise ValueError
    except (KeyError, ValueError):
        print("Dados do cliente ou pagamento inválidos. Venda não registrada.")
        return

    try:
        venda_id, numero_pedido, total = executar_checkout(
            itens,
            cliente_nome,
            cliente_documento,
            forma_pagamento,
            int(usuario["id"]),
        )
    except CheckoutError as erro:
        print(f"ERRO: {erro} Venda desfeita integralmente (ROLLBACK).")
        return

    print(
        f"Venda concluída: {numero_pedido} | Total: R$ {total:.2f} "
        "| estoque e auditoria atualizados."
    )
    terminal_mostrar_recibo(venda_id, usuario)


def terminal_cancelar_venda(usuario: sqlite3.Row) -> None:
    if usuario["perfil_acesso"] != "GERENTE":
        print("Acesso permitido somente ao gerente.")
        return
    terminal_mostrar_vendas(usuario, abrir_recibo=False)
    resposta = input("ID da venda concluída para cancelar (Enter para voltar): ").strip()
    if not resposta:
        return
    try:
        venda_id = int(resposta)
    except ValueError:
        print("ID inválido.")
        return
    if input("Confirma cancelamento e estorno do estoque? (S/N): ").strip().upper() != "S":
        print("Cancelamento não realizado.")
        return

    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT numero_pedido, status_venda FROM vendas WHERE id = ?",
            (venda_id,),
        )
        venda = cursor.fetchone()
        if venda is None or venda["status_venda"] != "CONCLUIDA":
            raise CheckoutError("Venda não encontrada ou não está concluída.")
        cursor.execute(
            "SELECT produto_id, quantidade_vendida FROM itens_venda WHERE venda_id = ?",
            (venda_id,),
        )
        for item in cursor.fetchall():
            cursor.execute(
                "UPDATE produtos SET volume_estoque = volume_estoque + ? WHERE id = ?",
                (item["quantidade_vendida"], item["produto_id"]),
            )
            cursor.execute(
                """
                INSERT INTO movimentacoes
                    (tipo_operacao, quantidade, justificativa, produto_id)
                VALUES ('Entrada', ?, ?, ?)
                """,
                (
                    item["quantidade_vendida"],
                    f"Estorno da venda {venda['numero_pedido']}",
                    item["produto_id"],
                ),
            )
        cursor.execute(
            "UPDATE vendas SET status_venda = 'CANCELADA' WHERE id = ?",
            (venda_id,),
        )
        conexao.commit()
        print(f"Venda {venda['numero_pedido']} cancelada; estoque estornado.")
    except CheckoutError as erro:
        if conexao.in_transaction:
            conexao.rollback()
        print(str(erro))
    except sqlite3.Error:
        if conexao.in_transaction:
            conexao.rollback()
        raise
    finally:
        cursor.close()
        conexao.close()


def terminal_movimentar_estoque() -> None:
    produtos = terminal_consultar_produtos()
    imprimir_tabela_terminal(
        "ESTOQUE",
        ("SKU", "Produto", "Saldo"),
        [(p["sku"], p["nome_comercial"], p["volume_estoque"]) for p in produtos],
    )
    por_sku = {p["sku"].casefold(): p for p in produtos}
    produto = por_sku.get(input("SKU do produto: ").strip().casefold())
    if produto is None:
        print("SKU não encontrado.")
        return
    tipo = input("Tipo (E=Entrada, S=Saída): ").strip().upper()
    if tipo not in {"E", "S"}:
        print("Tipo inválido.")
        return
    try:
        quantidade = int(input("Quantidade: ").strip())
        if quantidade <= 0:
            raise ValueError
    except ValueError:
        print("Quantidade deve ser um inteiro positivo.")
        return
    justificativa = input("Justificativa: ").strip()
    if not justificativa:
        print("Justificativa obrigatória.")
        return

    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT volume_estoque FROM produtos WHERE id = ?", (produto["id"],)
        )
        estoque = int(cursor.fetchone()["volume_estoque"])
        if tipo == "S" and estoque < quantidade:
            raise CheckoutError(
                f"Estoque insuficiente: disponível {estoque}, solicitado {quantidade}."
            )
        delta = quantidade if tipo == "E" else -quantidade
        cursor.execute(
            "UPDATE produtos SET volume_estoque = volume_estoque + ? WHERE id = ?",
            (delta, produto["id"]),
        )
        cursor.execute(
            """
            INSERT INTO movimentacoes
                (tipo_operacao, quantidade, justificativa, produto_id)
            VALUES (?, ?, ?, ?)
            """,
            ("Entrada" if tipo == "E" else "Saída", quantidade, justificativa, produto["id"]),
        )
        conexao.commit()
        print("Movimentação registrada.")
    except CheckoutError as erro:
        if conexao.in_transaction:
            conexao.rollback()
        print(erro)
    except sqlite3.Error:
        if conexao.in_transaction:
            conexao.rollback()
        raise
    finally:
        cursor.close()
        conexao.close()


def terminal_cadastrar_produto() -> None:
    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute("SELECT id, nome FROM categorias ORDER BY nome")
        categorias = cursor.fetchall()
        cursor.execute("SELECT id, razao_social FROM fornecedores ORDER BY razao_social")
        fornecedores = cursor.fetchall()
    finally:
        cursor.close()
        conexao.close()
    print("Categorias: " + ", ".join(f"{r['id']}={r['nome']}" for r in categorias))
    print(
        "Fornecedores: "
        + ", ".join(f"{r['id']}={r['razao_social']}" for r in fornecedores)
    )
    try:
        sku = input("SKU: ").strip()
        nome = input("Nome comercial: ").strip()
        fabricante = input("Fabricante: ").strip()
        classificacao = input("Classificação (Hardware/Software/Periférico): ").strip()
        modalidade = input("Licenciamento (N/A para produto físico): ").strip() or "N/A"
        preco = Decimal(input("Preço unitário: ").replace(",", "."))
        estoque = int(input("Estoque inicial: "))
        reposicao = int(input("Ponto de reposição: "))
        localizacao = input("Localização: ").strip()
        categoria_id = int(input("ID da categoria: "))
        fornecedor_id = int(input("ID do fornecedor: "))
        if (
            not all((sku, nome, fabricante, localizacao))
            or classificacao not in {"Hardware", "Software", "Periférico"}
            or preco <= 0
            or estoque < 0
            or reposicao < 0
        ):
            raise ValueError
    except (InvalidOperation, ValueError):
        print("Dados inválidos; produto não cadastrado.")
        return

    conexao = obter_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO produtos (
                sku, nome_comercial, fabricante, classificacao,
                modalidade_licenciamento, valor_unitario, volume_estoque,
                ponto_reposicao, localizacao, categoria_id, fornecedor_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sku,
                nome,
                fabricante,
                classificacao,
                modalidade,
                float(preco),
                estoque,
                reposicao,
                localizacao,
                categoria_id,
                fornecedor_id,
            ),
        )
        conexao.commit()
        print("Produto cadastrado.")
    except sqlite3.IntegrityError:
        conexao.rollback()
        print("Não foi possível cadastrar: SKU ou categoria/fornecedor inválido.")
    finally:
        cursor.close()
        conexao.close()


def executar_terminal_interativo() -> None:
    print("TECHSTOCK MANAGER - MODO INTERATIVO")
    usuario: sqlite3.Row | None = None
    while True:
        if usuario is None:
            print("\n1) Entrar  2) Criar conta de operador  0) Sair")
            escolha = input("Selecione: ").strip()
            if escolha == "1":
                usuario = terminal_login()
            elif escolha == "2":
                terminal_cadastrar_conta()
            elif escolha == "0":
                print("Até logo.")
                return
            else:
                print("Opção inválida.")
            continue

        print(f"\nTechStock | {usuario['nome_completo']} | {usuario['perfil_acesso']}")
        if usuario["perfil_acesso"] == "GERENTE":
            print(
                "1) Catálogo  2) Nova venda/PDV  3) Vendas e recibos  "
                "4) Cancelar venda  5) Movimentar estoque  "
                "6) Cadastrar produto  7) Sair da conta"
            )
        else:
            print(
                "1) Catálogo  2) Nova venda/PDV  3) Minhas vendas e recibos  "
                "4) Sair da conta"
            )
        escolha = input("Selecione: ").strip()
        if escolha == "1":
            terminal_mostrar_catalogo()
        elif escolha == "2":
            terminal_nova_venda(usuario)
        elif escolha == "3":
            terminal_mostrar_vendas(usuario)
        elif usuario["perfil_acesso"] == "GERENTE" and escolha == "4":
            terminal_cancelar_venda(usuario)
        elif usuario["perfil_acesso"] == "GERENTE" and escolha == "5":
            terminal_movimentar_estoque()
        elif usuario["perfil_acesso"] == "GERENTE" and escolha == "6":
            terminal_cadastrar_produto()
        elif escolha == ("7" if usuario["perfil_acesso"] == "GERENTE" else "4"):
            usuario = None
            print("Sessão encerrada.")
        else:
            print("Opção inválida para seu perfil.")


criar_tabelas()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TechStock Manager")
    parser.add_argument(
        "--tabela",
        action="store_true",
        help="compatibilidade: inicia o sistema interativo no terminal",
    )
    parser.add_argument(
        "--terminal",
        action="store_true",
        help="inicia o sistema interativo no terminal",
    )
    argumentos = parser.parse_args()
    if argumentos.terminal or argumentos.tabela:
        executar_terminal_interativo()
    else:
        app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
