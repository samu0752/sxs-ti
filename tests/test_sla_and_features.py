"""Regras de SLA, migração legada e fluxos operacionais."""

import sqlite3
import unittest
from datetime import datetime

from support import AppCase

import app as ticket_app
from sxs_core import WAITING, add_business, business_minutes, parse_date, sla_for


class SLATests(AppCase):
    def test_fim_de_semana_e_pausa_em_horas_uteis(self) -> None:
        start = parse_date("2026-11-20T17:00:00-03:00")
        monday = parse_date("2026-11-23T10:00:00-03:00")
        self.assertEqual(business_minutes(start, monday), 180)
        self.assertEqual(add_business(start, 120).hour, 9)
        ticket_id = self.create_ticket(2)
        with self.db() as db:
            db.execute("UPDATE tickets SET created_at=? WHERE id=?", (start.isoformat(), ticket_id))
            row = db.execute("SELECT * FROM tickets").fetchone()
            ticket_app.sync_pause(db, row, WAITING, "2026-11-20T17:30:00-03:00")
            db.execute("UPDATE tickets SET status=? WHERE id=?", (WAITING, ticket_id))
            row = db.execute("SELECT * FROM tickets").fetchone()
            self.assertEqual(sla_for(row, monday, [(start, None)])["label"], "SLA pausado")
            ticket_app.sync_pause(db, row, "Em atendimento", "2026-11-23T09:00:00-03:00")
            db.execute("UPDATE tickets SET status='Em atendimento' WHERE id=?", (ticket_id,))
            row = db.execute("SELECT * FROM tickets").fetchone()
            pauses = ticket_app.load_pauses(db, ticket_id)[ticket_id]
        self.assertEqual(row["paused_minutes"], 90)
        timing = sla_for(row, monday, pauses)
        self.assertEqual(timing["consumption"], 0.75)
        self.assertEqual(parse_date(timing["first_due"]).hour, 10)
        self.assertEqual(parse_date(timing["first_due"]).minute, 30)

    def test_escalonamento_ocorre_uma_vez_sem_mudar_orcamento_do_sla(self) -> None:
        ticket_id = self.create_ticket(2)
        with self.db() as db:
            db.execute("UPDATE tickets SET created_at='2026-11-23T08:00:00-03:00'")
        now = datetime.fromisoformat("2026-11-23T09:43:00-03:00")
        with self.db() as db:
            self.assertEqual(ticket_app.escalate_due(db, now), 1)
            self.assertEqual(ticket_app.escalate_due(db, now), 0)
            row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        self.assertEqual((row["priority"], row["sla_priority"]), ("Crítica", "Alta"))
        self.assertEqual(sla_for(row, now)["color"], "amber")

    def test_csat_report_assets_and_knowledge(self) -> None:
        technician, admin_csrf = self.session_for(1)
        owner, owner_csrf = self.session_for(2)
        self.assertEqual(
            technician.post(
                "/inventario",
                data={
                    "csrf_token": admin_csrf,
                    "tag": "PT-01",
                    "equipment": "Notebook",
                    "user_id": 2,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            owner.post(
                "/chamados",
                data={
                    **self.payload(owner_csrf),
                    "ticket_type": "Problema",
                    "asset_id": 1,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            technician.post(
                "/chamados/1/status",
                data={
                    "csrf_token": admin_csrf,
                    "status": "Fechado",
                    "solution": "Notebook substituído.",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            owner.post(
                "/chamados/1/csat",
                data={
                    "csrf_token": owner_csrf,
                    "score": 5,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            owner.post(
                "/chamados/1/csat",
                data={
                    "csrf_token": owner_csrf,
                    "score": 1,
                },
            ).status_code,
            409,
        )
        csv = technician.get("/relatorios/exportar.csv")
        self.assertIn(b"Problema", csv.data)
        self.assertIn(b"PT-01", csv.data)
        self.assertEqual(owner.get("/relatorios/exportar.csv").status_code, 403)
        suggestions = owner.get(
            "/conhecimento/sugestoes",
            query_string={
                "category": "Rede e conectividade",
            },
        ).json
        self.assertTrue(any("VPN" in item["title"] for item in suggestions))

    def test_reset_only_replaces_sample_tickets(self) -> None:
        private = self.create_ticket(2)
        client, csrf = self.session_for(None)
        self.assertEqual(client.post("/demo/resetar", data={"csrf_token": csrf}).status_code, 404)
        ticket_app.app.config["DEMO_READ_ONLY"] = True
        for _ in range(2):
            result = client.post(
                "/demo/resetar",
                data={"csrf_token": csrf},
                environ_overrides={"REMOTE_ADDR": "192.0.2.53"},
            )
            self.assertEqual(result.status_code, 302)
        with self.db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM tickets WHERE is_sample=1").fetchone()[0], 6
            )
            self.assertTrue(db.execute("SELECT 1 FROM tickets WHERE id=?", (private,)).fetchone())
        self.assertEqual(client.get(f"/chamados/{private}").status_code, 404)

    def test_migration_accepts_legacy_database_and_is_idempotent(self) -> None:
        old = ticket_app.DATABASE.with_name("legacy.db")
        with sqlite3.connect(old) as legacy:
            legacy.executescript(
                (ticket_app.BASE_DIR / "migrations/001_initial_schema.sql").read_text("utf-8")
            )
            legacy.execute("""
                INSERT INTO tickets(protocol, requester, email, title, description, category,
                    priority, status, created_at, updated_at)
                VALUES ('LEGADO', 'Ana', 'a@example.com', 'Título', 'Relato', 'Hardware',
                    'Alta', 'Aguardando usuário', '2026-11-20T17:00:00-03:00',
                    '2026-11-20T17:20:00-03:00')
            """)
        legacy.close()
        ticket_app.DATABASE = old
        ticket_app.init_db()
        ticket_app.init_db()
        with self.db() as db:
            self.assertEqual(
                [r[0] for r in db.execute("SELECT version FROM schema_version")], [1, 2]
            )
            row = db.execute("SELECT * FROM tickets WHERE protocol='LEGADO'").fetchone()
        self.assertEqual((row["status"], row["ticket_type"]), (WAITING, "Incidente"))
        self.assertIsNotNone(row["paused_at"])


if __name__ == "__main__":
    unittest.main()
