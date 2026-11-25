"""Banco temporário independente para os testes de integração."""

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

import app as ticket_app

TEST_HASH = generate_password_hash("senha-apenas-para-testes", method="pbkdf2:sha256:1000")


class AppCase(unittest.TestCase):
    def setUp(self) -> None:
        original = ticket_app.DATABASE
        self.addCleanup(setattr, ticket_app, "DATABASE", original)
        config = ticket_app.app.config.copy()
        self.addCleanup(ticket_app.app.config.update, config)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ticket_app.DATABASE = Path(folder.name) / "test.db"
        ticket_app.app.config.update(
            TESTING=True,
            SECRET_KEY="secret-for-tests-only",
            DEMO_READ_ONLY=False,
        )
        ticket_app.init_db()
        with self.db() as db:
            db.executemany(
                """
                INSERT INTO users(id,name,email,username,password_hash,role,can_manage_users)
                VALUES (?,?,?,?,?,?,?)
            """,
                (
                    (1, "Técnico", "t@example.com", "tecnico", TEST_HASH, "tecnico", 1),
                    (2, "Solicitante A", "a@example.com", "sol-a", TEST_HASH, "solicitante", 0),
                    (3, "Solicitante B", "b@example.com", "sol-b", TEST_HASH, "solicitante", 0),
                ),
            )

    @contextmanager
    def db(self):
        db = ticket_app.connect_db()
        try:
            with db:
                yield db
        finally:
            db.close()

    def session_for(self, user_id: int | None) -> tuple[FlaskClient, str]:
        client = ticket_app.app.test_client()
        with client.session_transaction() as session:
            if user_id is not None:
                session["user_id"] = user_id
            session["csrf_token"] = "token-teste"
        return client, self.token(client)

    def token(self, client: FlaskClient) -> str:
        with client.session_transaction() as session:
            return session["csrf_token"]

    def payload(self, csrf: str) -> dict[str, str]:
        return {
            "csrf_token": csrf,
            "title": "Chamado de teste",
            "description": "Relato fictício",
            "category": "Hardware",
            "priority": "Alta",
        }

    def create_ticket(self, owner: int) -> int:
        client, csrf = self.session_for(owner)
        response = client.post("/chamados", data=self.payload(csrf))
        self.assertEqual(response.status_code, 302)
        with self.db() as db:
            return int(db.execute("SELECT MAX(id) FROM tickets").fetchone()[0])
