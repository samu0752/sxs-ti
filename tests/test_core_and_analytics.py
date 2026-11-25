"""Testes unitários independentes de núcleo (SLA, migrações e analytics)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.analise_chamados import analyze, build_summary
from sxs_core import WAITING, add_business, business_minutes, migrate, parse_date, sla_for

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class CoreAndAnalyticsTests(unittest.TestCase):
    def _migrated_db(self, db_path: Path) -> sqlite3.Connection:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            migrate(db, MIGRATIONS_DIR)
        return db

    def test_parse_date_normaliza_timestamps_naive_e_aware(self) -> None:
        naive = parse_date("2026-11-23T12:00:00")
        aware = parse_date("2026-11-23T12:00:00+00:00")
        self.assertIsNotNone(naive.tzinfo)
        self.assertEqual(naive, aware)

    def test_sla_fallback_para_prioridade_desconhecida(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._migrated_db(Path(tmp) / "core.db")
            try:
                with db:
                    db.execute(
                        """
                        INSERT INTO tickets(protocol, requester, email, title, description,
                            category, priority, status, created_at, updated_at, sla_priority)
                        VALUES ('CH-2026-0001', 'Ana', 'ana@example.com', 'Teste', 'Desc',
                            'Hardware', 'Desconhecida', 'Aberto', '2026-11-23T08:00:00-03:00',
                            '2026-11-23T08:00:00-03:00', 'Desconhecida')
                    """
                    )
                row = db.execute("SELECT * FROM tickets WHERE id = 1").fetchone()
            finally:
                db.close()
        now = datetime.fromisoformat("2026-11-23T09:00:00-03:00")
        result = sla_for(row, now)
        self.assertEqual(result["label"], "No prazo")
        self.assertAlmostEqual(result["consumption"], 0.25)

    def test_business_minutes_e_add_business_em_fim_de_semana(self) -> None:
        friday = parse_date("2026-11-20T17:00:00-03:00")
        monday = parse_date("2026-11-23T10:00:00-03:00")
        self.assertEqual(business_minutes(friday, monday), 180.0)
        due = add_business(friday, 120)
        self.assertEqual((due.weekday(), due.hour, due.minute), (0, 9, 0))

    def test_analise_chamados_calcula_kpis_e_exporta_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "demo.db"
            json_path = Path(tmp) / "kpis.json"
            db = self._migrated_db(db_path)
            try:
                with db:
                    db.execute(
                        """
                        INSERT INTO tickets(protocol, requester, email, title, description,
                            category, priority, status, created_at, updated_at,
                            first_response_at, resolved_at, is_sample, ticket_type,
                            sla_priority, csat)
                        VALUES
                            ('CH-2026-0001', 'Marina', 'm@example.invalid', 'VPN cai', 'Desc',
                             'Rede e conectividade', 'Alta', 'Resolvido',
                             '2026-11-23T08:00:00-03:00', '2026-11-23T10:00:00-03:00',
                             '2026-11-23T08:30:00-03:00', '2026-11-23T10:00:00-03:00',
                             1, 'Incidente', 'Alta', 5),
                            ('CH-2026-0002', 'Lucas', 'l@example.invalid', 'Mouse', 'Desc',
                             'Hardware', 'Baixa', ?,
                             '2026-11-23T09:00:00-03:00', '2026-11-23T09:30:00-03:00',
                             '2026-11-23T09:30:00-03:00', NULL,
                             1, 'Requisição', 'Baixa', NULL)
                    """,
                        (WAITING,),
                    )
            finally:
                db.close()

            summary = build_summary(db_path, scope="demo")
            self.assertEqual(summary["volume"]["total"], 2)
            self.assertEqual(summary["volume"]["concluidos"], 1)
            self.assertEqual(summary["volume"]["ativos"], 1)
            self.assertEqual(summary["sla"]["cumprido_pct"], 100.0)
            self.assertEqual(summary["csat"]["media"], 5.0)
            self.assertEqual(summary["csat"]["positivo_pct"], 100.0)

            analyze(db_path, scope="demo", export_json=json_path)
            exported = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(exported["volume"]["total"], 2)
            self.assertEqual(exported["distribuicoes"]["tipos"]["Incidente"], 1)


if __name__ == "__main__":
    unittest.main()
