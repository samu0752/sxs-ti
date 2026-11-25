import os
import re
import unittest

TEST_DATABASE = os.path.join(os.path.dirname(__file__), ".test-chamados.db")
os.environ["TICKETS_DATABASE"] = TEST_DATABASE
os.environ["LOAD_SAMPLE_DATA"] = "0"

import app as ticket_app  # noqa: E402
from werkzeug.security import generate_password_hash

TEST_PASSWORD = 'senha-apenas-para-testes'
TEST_HASH = generate_password_hash(TEST_PASSWORD, method='pbkdf2:sha256:1000')


class TicketAppTests(unittest.TestCase):
    def setUp(self):
        self.db_path = ticket_app.DATABASE
        if self.db_path.exists():
            self.db_path.unlink()
        ticket_app.init_db()
        ticket_app.app.config.update(TESTING=True, SECRET_KEY="test-secret", DEMO_READ_ONLY=False)
        with ticket_app.app.app_context():
            db = ticket_app.get_db()
            with db:
                db.executemany("INSERT INTO users (id, name, email, password_hash, role) VALUES (?, ?, ?, ?, ?)", (
                    (1, "Técnico de teste", "tecnico@example.com", TEST_HASH, "tecnico"),
                    (2, "Ana Souza", "ana@example.com", TEST_HASH, "solicitante"),
                    (3, "Bruno Lopes", "bruno@example.com", TEST_HASH, "solicitante"),
                ))
        self.client = ticket_app.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        page = self.client.get("/")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
        if match is None:
            # O formulário de criação sempre fornece o token, inclusive sem chamados.
            self.fail("Token CSRF não foi renderizado")
        self.csrf = match.group(1).decode()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def post_form(self, path, **values):
        return self.client.post(path, data={"csrf_token": self.csrf, **values}, follow_redirects=True)

    def create_payload(self, **overrides):
        return {
            "requester": "Ana Souza", "email": "ana@example.com",
            "title": "Sem acesso ao e-mail", "description": "A autenticação retorna erro.",
            "category": "Acesso e contas", "priority": "Alta", **overrides,
        }

    def test_dashboard_renders_empty_state_and_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nenhum chamado encontrado".encode(), response.data)
        self.assertEqual(self.client.get("/saude").json, {"status": "ok"})

    def test_create_ticket_generates_protocol_and_updates_metrics(self):
        response = self.post_form("/chamados", **self.create_payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CH-", response.data)
        self.assertIn("Chamados ativos".encode(), response.data)
        self.assertIn("Sem acesso ao e-mail".encode(), response.data)

    def test_invalid_ticket_is_not_saved(self):
        response = self.post_form("/chamados", **self.create_payload(priority="Urgente"))
        self.assertIn("Selecione uma prioridade válida.".encode(), response.data)
        self.assertIn("Sem acesso ao e-mail".encode(), response.data)
        with ticket_app.app.app_context():
            count = ticket_app.get_db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        self.assertEqual(count, 0)

    def test_invalid_email_is_rejected_and_form_values_are_preserved(self):
        response = self.post_form("/chamados", **self.create_payload(email="ana@local"))
        self.assertIn("Informe um e-mail válido.".encode(), response.data)
        self.assertIn(b'value="ana@local"', response.data)

    def test_search_and_category_filters(self):
        self.post_form("/chamados", **self.create_payload())
        self.post_form("/chamados", **self.create_payload(
            requester="Bruno", email="bruno@example.com", title="Monitor sem imagem", category="Hardware"))
        response = self.client.get("/?q=Ana&category=Acesso+e+contas")
        self.assertIn("Sem acesso ao e-mail".encode(), response.data)
        self.assertNotIn("Monitor sem imagem".encode(), response.data)

    def test_ticket_detail_displays_full_description(self):
        self.post_form("/chamados", **self.create_payload())
        with ticket_app.app.app_context():
            ticket_id = ticket_app.get_db().execute("SELECT id FROM tickets").fetchone()["id"]
        response = self.client.get(f"/chamados/{ticket_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("A autenticação retorna erro.".encode(), response.data)

    def test_status_change_sets_resolution_time(self):
        self.post_form("/chamados", **self.create_payload())
        with ticket_app.app.app_context():
            row = ticket_app.get_db().execute("SELECT id FROM tickets").fetchone()
        response = self.post_form(f"/chamados/{row['id']}/status", status="Resolvido", solution="Acesso restabelecido.")
        self.assertIn("Status do chamado atualizado.".encode(), response.data)
        with ticket_app.app.app_context():
            ticket = ticket_app.get_db().execute("SELECT status, resolved_at FROM tickets WHERE id = ?", (row["id"],)).fetchone()
        self.assertEqual(ticket["status"], "Resolvido")
        self.assertIsNotNone(ticket["resolved_at"])

    def test_status_change_from_detail_returns_to_detail(self):
        self.post_form("/chamados", **self.create_payload())
        with ticket_app.app.app_context():
            ticket_id = ticket_app.get_db().execute("SELECT id FROM tickets").fetchone()["id"]
        response = self.client.post(
            f"/chamados/{ticket_id}/status",
            data={"csrf_token": self.csrf, "status": "Em atendimento", "return_to": f"/chamados/{ticket_id}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], f"/chamados/{ticket_id}")

    def test_invalid_status_is_rejected(self):
        self.post_form("/chamados", **self.create_payload())
        with ticket_app.app.app_context():
            ticket_id = ticket_app.get_db().execute("SELECT id FROM tickets").fetchone()["id"]
        response = self.post_form(f"/chamados/{ticket_id}/status", status="Concluído")
        self.assertIn("Status inválido".encode(), response.data)

    def test_post_without_csrf_token_is_rejected(self):
        response = self.client.post("/chamados", data=self.create_payload())
        self.assertEqual(response.status_code, 400)

    def test_read_only_demo_hides_form_and_rejects_writes(self):
        ticket_app.app.config["DEMO_READ_ONLY"] = True
        response = self.client.get("/")
        self.assertIn("somente leitura".encode(), response.data)
        self.assertNotIn(b'name="requester"', response.data)
        write_response = self.client.post("/chamados", data={"csrf_token": self.csrf, **self.create_payload()})
        self.assertEqual(write_response.status_code, 403)

    def test_unknown_ticket_returns_404(self):
        response = self.post_form("/chamados/999/status", status="Aberto")
        self.assertEqual(response.status_code, 404)

    def test_login_and_requester_scope(self):
        self.post_form("/chamados", **self.create_payload())
        stranger = ticket_app.app.test_client()
        self.assertEqual(stranger.get("/").status_code, 302)
        page = stranger.get("/entrar")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)[1].decode()
        response = stranger.post("/entrar", data={
            "email": "ana@example.com", "password": TEST_PASSWORD, "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn(b"Meus chamados", response.data)
        self.assertIn("Sem acesso ao e-mail".encode(), response.data)
        with stranger.session_transaction() as session:
            self.assertNotEqual(session["csrf_token"], token)
            session["user_id"] = 3
        self.assertEqual(stranger.get("/chamados/1").status_code, 404)
        self.assertNotIn("Sem acesso ao e-mail".encode(), stranger.get("/").data)

    def test_assignment_queue_and_private_notes(self):
        self.post_form("/chamados", **self.create_payload())
        self.assertNotIn("Sem acesso ao e-mail".encode(), self.client.get("/?queue=minha").data)
        self.post_form("/chamados/1/responsavel", assignee_id=1)
        self.assertIn("Sem acesso ao e-mail".encode(), self.client.get("/?queue=minha").data)
        self.post_form("/chamados/1/mensagens", internal=1, body="Diagnóstico reservado.")
        with ticket_app.app.app_context():
            row = ticket_app.get_db().execute("SELECT assignee_id, first_response_at FROM tickets").fetchone()
            self.assertEqual(row["assignee_id"], 1)
            self.assertIsNone(row["first_response_at"])
        self.post_form("/chamados/1/mensagens", body="Vamos verificar as permissões.")
        with ticket_app.app.app_context():
            row = ticket_app.get_db().execute("SELECT first_response_at FROM tickets").fetchone()
            self.assertIsNotNone(row["first_response_at"])
        with self.client.session_transaction() as session:
            session["user_id"] = 2
        page = self.client.get("/chamados/1")
        self.assertIn("Vamos verificar as permissões.".encode(), page.data)
        self.assertNotIn("Diagnóstico reservado.".encode(), page.data)
        self.assertEqual(self.post_form("/chamados/1/status", status="Fechado").status_code, 403)
        self.assertEqual(self.post_form("/chamados/1/mensagens", body="Privado", internal=1).status_code, 403)

    def test_sla_queue_and_resolution_note(self):
        self.post_form("/chamados", **self.create_payload())
        with ticket_app.app.app_context():
            db = ticket_app.get_db()
            with db:
                db.execute("UPDATE tickets SET created_at = '2020-01-01T00:00:00+00:00'")
        self.assertIn("Sem acesso ao e-mail".encode(), self.client.get("/?queue=atrasados").data)
        response = self.post_form("/chamados/1/status", status="Resolvido")
        self.assertIn("Registre a solução".encode(), response.data)
        self.post_form("/chamados/1/status", status="Resolvido", solution="Ajuste de permissão concluído.")
        self.assertNotIn("Sem acesso ao e-mail".encode(), self.client.get("/?queue=atrasados").data)
        self.assertIn("Ajuste de permissão concluído.".encode(), self.client.get("/chamados/1").data)

    def test_public_demo_hides_private_tickets(self):
        self.post_form("/chamados", **self.create_payload())
        ticket_app.app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(self.client.get("/chamados/1").status_code, 404)
        self.assertNotIn("Sem acesso ao e-mail".encode(), self.client.get("/").data)

    def account_payload(self, **overrides):
        return {
            "name": "Pessoa de teste", "email": "pessoa@example.com", "username": "pessoa-ti",
            "password": TEST_PASSWORD, "password_confirm": TEST_PASSWORD, **overrides,
        }

    def test_signup_creates_own_account_and_can_log_in_by_username(self):
        visitor = ticket_app.app.test_client()
        self.assertIn(b"Criar conta", visitor.get("/entrar").data)
        self.assertEqual(visitor.get("/criar-conta").status_code, 200)
        with visitor.session_transaction() as session:
            token = session["csrf_token"]
        create = visitor.post("/criar-conta", data={
            "csrf_token": token, **self.account_payload(role="administrador"),
        })
        self.assertEqual(create.status_code, 302)
        login = visitor.post("/entrar", data={
            "csrf_token": token, "identifier": "PESSOA-TI", "password": TEST_PASSWORD,
        }, follow_redirects=True)
        self.assertIn(b"Meus chamados", login.data)
        self.assertEqual(visitor.get("/administracao/contas").status_code, 403)
        self.post_form("/chamados", **self.create_payload(email="pessoa@example.com"))
        self.assertEqual(visitor.get("/chamados/1").status_code, 404)

    def test_administrator_can_create_a_technician(self):
        self.assertEqual(self.client.get("/administracao/contas").status_code, 403)
        denied = self.post_form("/administracao/contas", **self.account_payload(role="tecnico"))
        self.assertEqual(denied.status_code, 403)
        with ticket_app.app.app_context():
            db = ticket_app.get_db()
            with db:
                db.execute("UPDATE users SET can_manage_users = 1 WHERE id = 1")
        response = self.post_form("/administracao/contas", **self.account_payload(role="tecnico"))
        self.assertIn(b"pessoa-ti", response.data)
        self.assertNotIn(TEST_PASSWORD.encode(), response.data)
        with ticket_app.app.app_context():
            account = ticket_app.get_db().execute("SELECT * FROM users WHERE username = 'pessoa-ti'").fetchone()
            self.assertEqual((account["role"], account["can_manage_users"], account["provisioned"]), ("tecnico", 0, 1))
        visitor = ticket_app.app.test_client()
        visitor.get("/entrar")
        with visitor.session_transaction() as session:
            token = session["csrf_token"]
        login = visitor.post("/entrar", data={
            "identifier": "pessoa-ti", "password": TEST_PASSWORD, "csrf_token": token,
        }, follow_redirects=True)
        self.assertIn("Visão geral do atendimento".encode(), login.data)
        self.assertEqual(visitor.get("/administracao/contas").status_code, 403)

    def test_public_demo_does_not_accept_new_accounts(self):
        ticket_app.app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(self.client.get("/criar-conta").status_code, 404)
        response = self.post_form("/criar-conta", **self.account_payload())
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
