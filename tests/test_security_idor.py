"""A autorização deve usar o dono do recurso em todas as rotas aninhadas."""

import unittest
from unittest.mock import patch

from support import AppCase


class SecurityIDORTests(AppCase):
    def test_requester_cannot_view_comment_or_change_another_ticket(self) -> None:
        ticket_id = self.create_ticket(3)
        client, csrf = self.session_for(2)
        path = f"/chamados/{ticket_id}"
        self.assertEqual(client.get(path).status_code, 404)
        response = client.post(f"{path}/mensagens", data={"csrf_token": csrf, "body": "Teste"})
        self.assertEqual(response.status_code, 404)
        denied = {
            "status": {"status": "Fechado", "solution": "Invasão de escopo"},
            "responsavel": {"assignee_id": 1},
            "csat": {"score": 5},
            "ativo": {"asset_id": ""},
            "vinculos": {"protocol": "CH-TESTE", "kind": "relacionado"},
        }
        for route, fields in denied.items():
            with self.subTest(route=route):
                result = client.post(f"{path}/{route}", data={"csrf_token": csrf, **fields})
                self.assertIn(result.status_code, {403, 404})
        with self.db() as db:
            self.assertEqual(db.execute("SELECT status FROM tickets").fetchone()[0], "Aberto")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline").fetchone()[0], 1)

    def test_account_lock_applies_to_email_and_username(self) -> None:
        client, csrf = self.session_for(None)
        url = "/entrar"
        for _ in range(5):
            with patch("app.check_password_hash", return_value=False):
                response = client.post(
                    url,
                    data={
                        "csrf_token": csrf,
                        "identifier": "sol-a",
                        "password": "incorreta",
                    },
                    environ_overrides={"REMOTE_ADDR": "192.0.2.51"},
                )
            self.assertEqual(response.status_code, 200)
        with self.db() as db:
            row = db.execute(
                "SELECT failed_attempts, locked_until FROM users WHERE id=2"
            ).fetchone()
            self.assertEqual(row[0], 5)
            self.assertIsNotNone(row[1])
        with patch("app.check_password_hash", return_value=True) as check:
            response = client.post(
                url,
                data={
                    "csrf_token": csrf,
                    "identifier": "a@example.com",
                    "password": "correta",
                },
                environ_overrides={"REMOTE_ADDR": "192.0.2.51"},
            )
        self.assertEqual(response.status_code, 200)
        check.assert_not_called()
        with self.db() as db:
            events = [row[0] for row in db.execute("SELECT outcome FROM audit_logs ORDER BY id")]
            self.assertEqual(events, ["falha"] * 5 + ["bloqueado"])

    def test_login_per_ip_limit_uses_post_only(self) -> None:
        client, csrf = self.session_for(None)
        ip = {"REMOTE_ADDR": "192.0.2.52"}
        with patch("app.check_password_hash", return_value=False):
            for _ in range(30):
                self.assertEqual(
                    client.post(
                        "/entrar",
                        data={
                            "csrf_token": csrf,
                            "identifier": "desconhecido",
                        },
                        environ_overrides=ip,
                    ).status_code,
                    200,
                )
            limited = client.post("/entrar", data={"csrf_token": csrf}, environ_overrides=ip)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(client.get("/entrar", environ_overrides=ip).status_code, 200)

    def test_headers_and_health_over_https(self) -> None:
        client, _ = self.session_for(None)
        response = client.get("/health", base_url="https://example.test")
        self.assertEqual(response.json, {"status": "ok"})
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])


if __name__ == "__main__":
    unittest.main()
