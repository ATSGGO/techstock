import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit


_test_directory = tempfile.TemporaryDirectory()
os.environ["TECHSTOCK_DATABASE"] = str(Path(_test_directory.name) / "techstock.db")
os.environ["TECHSTOCK_SECRET_KEY"] = "test-only-secret-key"

import app as techstock


class AcceptanceGapTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        _test_directory.cleanup()

    def setUp(self) -> None:
        database_path = Path(techstock.DATABASE_PATH)
        if database_path.exists():
            database_path.unlink()
        techstock.criar_tabelas()
        techstock.app.config.update(TESTING=True, SECRET_KEY="test-only-secret-key")
        self.client = techstock.app.test_client()
        self.csrf_token = "test-csrf-token"

    def set_usuario(self, email: str) -> int:
        conexao = techstock.obter_conexao()
        try:
            usuario = conexao.execute(
                "SELECT id FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
            self.assertIsNotNone(usuario)
            usuario_id = int(usuario["id"])
        finally:
            conexao.close()
        with self.client.session_transaction() as sess:
            sess["usuario_id"] = usuario_id
            sess["_csrf_token"] = self.csrf_token
        return usuario_id

    def test_authenticated_pages_use_shared_role_aware_navigation(self) -> None:
        self.set_usuario("gerente@techstore.com")
        gerente = self.client.get("/")
        gerente_html = gerente.get_data(as_text=True)
        self.assertIn("Catálogo", gerente_html)
        self.assertIn("Ponto de venda", gerente_html)
        self.assertIn("Relatórios", gerente_html)
        self.assertIn("/static/app.css", gerente_html)
        self.assertIn("/static/techstock-logo.png", gerente_html)
        stylesheet = self.client.get("/static/app.css")
        self.assertEqual(stylesheet.status_code, 200)
        stylesheet.close()
        logo = self.client.get("/static/techstock-logo.png")
        self.assertEqual(logo.status_code, 200)
        self.assertTrue(logo.content_type.startswith("image/png"))
        logo.close()
        login = techstock.app.test_client().get("/login")
        self.assertIn(
            "/static/techstock-logo.png", login.get_data(as_text=True)
        )

        self.set_usuario("operador@techstore.com")
        operador_html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Ponto de venda", operador_html)
        self.assertNotIn(">Relatórios</a>", operador_html)

    def test_checkout_redirects_to_verifiable_receipt(self) -> None:
        self.set_usuario("operador@techstore.com")
        conexao = techstock.obter_conexao()
        try:
            produto = conexao.execute(
                "SELECT id FROM produtos WHERE sku = 'SSD-NVME-1TB'"
            ).fetchone()
            produto_id = int(produto["id"])
        finally:
            conexao.close()

        resposta = self.client.post(
            "/vendas/checkout",
            data={
                "_csrf_token": self.csrf_token,
                "cliente_nome": "Cliente de teste",
                "cliente_documento": "12345678900",
                "forma_pagamento": "PIX",
                "produto_id": str(produto_id),
                "quantidade": "1",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        recibo_url = urlsplit(resposta.headers["Location"])
        self.assertRegex(recibo_url.path, r"^/vendas/\d+$")

        recibo = self.client.get(recibo_url.path)
        self.assertEqual(recibo.status_code, 200)
        conexao = techstock.obter_conexao()
        try:
            venda = conexao.execute(
                "SELECT numero_pedido, chave_autenticidade FROM vendas"
            ).fetchone()
        finally:
            conexao.close()
        self.assertIsNotNone(venda["chave_autenticidade"])
        self.assertIn(venda["chave_autenticidade"], recibo.get_data(as_text=True))

        validacao = self.client.get(
            "/vendas/verificar",
            query_string={
                "pedido": venda["numero_pedido"],
                "chave": venda["chave_autenticidade"],
            },
        )
        self.assertIn("Recibo autêntico", validacao.get_data(as_text=True))
        invalidacao = self.client.get(
            "/vendas/verificar",
            query_string={"pedido": venda["numero_pedido"], "chave": "incorreta"},
        )
        self.assertIn("Não foi possível confirmar", invalidacao.get_data(as_text=True))

    def test_checkout_enforces_name_document_and_quantity_limits(self) -> None:
        self.set_usuario("operador@techstore.com")
        conexao = techstock.obter_conexao()
        try:
            produto = conexao.execute(
                "SELECT id FROM produtos WHERE sku = 'SSD-NVME-1TB'"
            ).fetchone()
            produto_id = int(produto["id"])
            conexao.execute(
                "UPDATE produtos SET volume_estoque = 300 WHERE id = ?",
                (produto_id,),
            )
            conexao.commit()
        finally:
            conexao.close()

        base = {
            "_csrf_token": self.csrf_token,
            "cliente_nome": "Cliente",
            "cliente_documento": "12345678900",
            "forma_pagamento": "PIX",
            "produto_id": str(produto_id),
            "quantidade": "1",
        }
        pdv = self.client.get("/vendas/nova")
        pdv_html = pdv.get_data(as_text=True)
        self.assertIn('maxlength="50"', pdv_html)
        self.assertIn('maxlength="18"', pdv_html)
        self.assertIn('quantityInput.max = "250"', pdv_html)

        resposta = self.client.post(
            "/vendas/checkout", data={**base, "cliente_nome": "N" * 51}
        )
        self.assertIn("até 50 caracteres", resposta.get_data(as_text=True))
        resposta = self.client.post(
            "/vendas/checkout", data={**base, "cliente_documento": "1234567890"}
        )
        self.assertIn(
            "CPF com 11 ou CNPJ com 14 dígitos", resposta.get_data(as_text=True)
        )
        resposta = self.client.post(
            "/vendas/checkout", data={**base, "quantidade": "251"}
        )
        self.assertIn(
            "máxima permitida por produto é 250", resposta.get_data(as_text=True)
        )
        resposta = self.client.post(
            "/vendas/checkout",
            data={
                **base,
                "produto_id": [str(produto_id), str(produto_id)],
                "quantidade": ["126", "125"],
            },
        )
        self.assertIn(
            "máxima permitida por produto é 250", resposta.get_data(as_text=True)
        )

        resposta = self.client.post(
            "/vendas/checkout",
            data={
                **base,
                "cliente_nome": "N" * 50,
                "cliente_documento": "12.345.678/0001-90",
                "quantidade": "250",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        conexao = techstock.obter_conexao()
        try:
            saldo = conexao.execute(
                "SELECT volume_estoque FROM produtos WHERE id = ?", (produto_id,)
            ).fetchone()["volume_estoque"]
            total_vendas = conexao.execute("SELECT COUNT(*) FROM vendas").fetchone()[0]
        finally:
            conexao.close()
        self.assertEqual(saldo, 50)
        self.assertEqual(total_vendas, 1)

    def test_only_manager_can_edit_and_delete_products(self) -> None:
        operador_id = self.set_usuario("operador@techstore.com")
        conexao = techstock.obter_conexao()
        try:
            produto = conexao.execute(
                "SELECT * FROM produtos WHERE sku = 'PROC-I7-147K'"
            ).fetchone()
            produto_id = int(produto["id"])
        finally:
            conexao.close()

        self.assertEqual(
            self.client.get(f"/produtos/{produto_id}/editar").status_code, 403
        )
        self.assertEqual(
            self.client.post(
                f"/produtos/{produto_id}/excluir",
                data={"_csrf_token": self.csrf_token},
            ).status_code,
            403,
        )

        gerente_id = self.set_usuario("gerente@techstore.com")
        self.assertNotEqual(operador_id, gerente_id)
        conexao = techstock.obter_conexao()
        try:
            produto = conexao.execute(
                "SELECT * FROM produtos WHERE id = ?", (produto_id,)
            ).fetchone()
            saldo_original = produto["volume_estoque"]
        finally:
            conexao.close()

        resposta = self.client.post(
            f"/produtos/{produto_id}/editar",
            data={
                "_csrf_token": self.csrf_token,
                "sku": produto["sku"],
                "nome_comercial": produto["nome_comercial"],
                "fabricante": produto["fabricante"],
                "classificacao": produto["classificacao"],
                "especificacao_tecnica": produto["especificacao_tecnica"] or "",
                "modalidade_licenciamento": produto["modalidade_licenciamento"],
                "valor_unitario": "2300.00",
                "volume_estoque": "999",
                "ponto_reposicao": str(produto["ponto_reposicao"]),
                "localizacao": produto["localizacao"],
                "categoria_id": str(produto["categoria_id"]),
                "fornecedor_id": str(produto["fornecedor_id"]),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        conexao = techstock.obter_conexao()
        try:
            produto_atualizado = conexao.execute(
                "SELECT valor_unitario, volume_estoque FROM produtos WHERE id = ?",
                (produto_id,),
            ).fetchone()
            auditoria = conexao.execute(
                """
                SELECT usuario_id, valor_anterior, valor_novo
                FROM auditoria_precos WHERE produto_id = ?
                """,
                (produto_id,),
            ).fetchone()
        finally:
            conexao.close()
        self.assertEqual(produto_atualizado["valor_unitario"], 2300.0)
        self.assertEqual(produto_atualizado["volume_estoque"], saldo_original)
        self.assertEqual(auditoria["usuario_id"], gerente_id)
        self.assertEqual(auditoria["valor_novo"], 2300.0)

        resposta = self.client.post(
            f"/produtos/{produto_id}/excluir",
            data={"_csrf_token": self.csrf_token},
        )
        self.assertEqual(resposta.status_code, 302)
        conexao = techstock.obter_conexao()
        try:
            produto_excluido = conexao.execute(
                "SELECT id FROM produtos WHERE id = ?", (produto_id,)
            ).fetchone()
            auditoria_preservada = conexao.execute(
                "SELECT produto_id FROM auditoria_precos WHERE sku = ?",
                (produto["sku"],),
            ).fetchone()
        finally:
            conexao.close()
        self.assertIsNone(produto_excluido)
        self.assertIsNone(auditoria_preservada["produto_id"])

        conexao = techstock.obter_conexao()
        try:
            produto_com_historico = conexao.execute(
                "SELECT id FROM produtos WHERE sku = 'SSD-NVME-1TB'"
            ).fetchone()
            produto_com_historico_id = int(produto_com_historico["id"])
            conexao.execute(
                """
                INSERT INTO movimentacoes
                    (tipo_operacao, quantidade, justificativa, produto_id)
                VALUES ('Entrada', 1, 'Registro de teste', ?)
                """,
                (produto_com_historico_id,),
            )
            conexao.commit()
        finally:
            conexao.close()
        resposta = self.client.post(
            f"/produtos/{produto_com_historico_id}/excluir",
            data={"_csrf_token": self.csrf_token},
        )
        self.assertEqual(resposta.status_code, 302)
        conexao = techstock.obter_conexao()
        try:
            produto_preservado = conexao.execute(
                "SELECT id FROM produtos WHERE id = ?",
                (produto_com_historico_id,),
            ).fetchone()
        finally:
            conexao.close()
        self.assertIsNotNone(produto_preservado)

    def test_startup_backfills_authentication_keys_for_legacy_sales(self) -> None:
        database_path = Path(techstock.DATABASE_PATH)
        database_path.unlink()
        conexao = sqlite3.connect(database_path)
        try:
            conexao.execute(
                """
                CREATE TABLE vendas (
                    id INTEGER PRIMARY KEY,
                    numero_pedido TEXT NOT NULL
                )
                """
            )
            conexao.execute(
                "INSERT INTO vendas (numero_pedido) VALUES ('PED-LEGADO-1')"
            )
            conexao.commit()
        finally:
            conexao.close()

        techstock.criar_tabelas()
        conexao = techstock.obter_conexao()
        try:
            chave = conexao.execute(
                "SELECT chave_autenticidade FROM vendas WHERE numero_pedido = ?",
                ("PED-LEGADO-1",),
            ).fetchone()["chave_autenticidade"]
        finally:
            conexao.close()
        self.assertTrue(chave)
        techstock.criar_tabelas()
        conexao = techstock.obter_conexao()
        try:
            chave_reinicializada = conexao.execute(
                "SELECT chave_autenticidade FROM vendas WHERE numero_pedido = ?",
                ("PED-LEGADO-1",),
            ).fetchone()["chave_autenticidade"]
        finally:
            conexao.close()
        self.assertEqual(chave, chave_reinicializada)


if __name__ == "__main__":
    unittest.main()
