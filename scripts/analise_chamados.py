"""Análise operacional e de SLA dos chamados de um banco SQLite migrado."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sxs_core import load_pauses, parse_date, sla_for  # noqa: E402

ACTIVE_STATUSES = {"Aberto", "Em atendimento", "Aguardando solicitante"}


def _format_minutes(value: float | None) -> str:
    if value is None:
        return "—"
    total = max(0, round(value))
    hours, minutes = divmod(total, 60)
    return f"{hours}h {minutes:02d}min" if hours else f"{minutes}min"


def build_summary(path: Path, scope: str = "demo") -> dict[str, Any]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        queries = {
            "demo": "SELECT * FROM tickets WHERE is_sample = 1 ORDER BY id",
            "operacional": "SELECT * FROM tickets WHERE is_sample = 0 ORDER BY id",
            "todos": "SELECT * FROM tickets ORDER BY id",
        }
        tickets = db.execute(queries[scope]).fetchall()
        pauses = load_pauses(db)
        assignees = {
            row["id"]: row["name"]
            for row in db.execute("SELECT id, name FROM users WHERE role = 'tecnico'")
        }

    now = datetime.now(UTC)
    first_response_mins: list[float] = []
    resolution_mins: list[float] = []
    sla_by_priority: dict[str, list[bool]] = {}
    sla_outcomes: list[bool] = []
    overdue_active = 0
    escalated_count = 0

    for ticket in tickets:
        created = parse_date(ticket["created_at"])
        if ticket["first_response_at"]:
            delta = (parse_date(ticket["first_response_at"]) - created).total_seconds() / 60
            first_response_mins.append(max(0.0, delta))
        if ticket["resolved_at"]:
            delta = (parse_date(ticket["resolved_at"]) - created).total_seconds() / 60
            resolution_mins.append(max(0.0, delta))
        if ticket["escalated_at"]:
            escalated_count += 1
        result = sla_for(ticket, now, pauses.get(ticket["id"]))
        if ticket["status"] in ACTIVE_STATUSES and result["color"] == "red":
            overdue_active += 1
        if result["met"] is not None:
            sla_outcomes.append(result["met"])
            prio = str(ticket["sla_priority"] or ticket["priority"])
            sla_by_priority.setdefault(prio, []).append(result["met"])

    total = len(tickets)
    active = sum(1 for t in tickets if t["status"] in ACTIVE_STATUSES)
    completed = total - active
    scores = [int(t["csat"]) for t in tickets if t["csat"] is not None]
    positive_csat = (
        round(100 * sum(1 for s in scores if s >= 4) / len(scores), 1) if scores else None
    )

    return {
        "escopo": scope,
        "gerado_em_utc": now.isoformat(timespec="seconds"),
        "volume": {
            "total": total,
            "ativos": active,
            "concluidos": completed,
            "taxa_resolucao_pct": round(100 * completed / total, 1) if total else None,
            "ativos_atrasados": overdue_active,
            "escalonados": escalated_count,
        },
        "tempos_minutos": {
            "primeira_resposta_media": (
                round(mean(first_response_mins), 1) if first_response_mins else None
            ),
            "primeira_resposta_mediana": (
                round(median(first_response_mins), 1) if first_response_mins else None
            ),
            "resolucao_media": round(mean(resolution_mins), 1) if resolution_mins else None,
            "resolucao_mediana": round(median(resolution_mins), 1) if resolution_mins else None,
        },
        "sla": {
            "avaliados": len(sla_outcomes),
            "cumprido_pct": (
                round(100 * sum(sla_outcomes) / len(sla_outcomes), 1) if sla_outcomes else None
            ),
            "por_prioridade_pct": {
                prio: round(100 * sum(vals) / len(vals), 1)
                for prio, vals in sorted(sla_by_priority.items())
            },
        },
        "csat": {
            "avaliacoes": len(scores),
            "media": round(mean(scores), 2) if scores else None,
            "positivo_pct": positive_csat,
            "distribuicao": dict(sorted(Counter(scores).items())),
        },
        "distribuicoes": {
            "categorias": dict(Counter(t["category"] for t in tickets).most_common()),
            "status": dict(Counter(t["status"] for t in tickets).most_common()),
            "prioridades": dict(Counter(t["priority"] for t in tickets).most_common()),
            "tipos": dict(Counter(t["ticket_type"] for t in tickets).most_common()),
            "carga_ativa_por_tecnico": dict(
                Counter(
                    assignees.get(t["assignee_id"], "Sem responsável")
                    for t in tickets
                    if t["status"] in ACTIVE_STATUSES
                ).most_common()
            ),
        },
    }


def print_report(summary: dict[str, Any]) -> None:
    vol = summary["volume"]
    times = summary["tempos_minutos"]
    sla = summary["sla"]
    csat = summary["csat"]

    print(f"=== SXS TI | Relatório Analítico ({summary['escopo']}) ===")
    print(
        f"Total: {vol['total']} | Ativos: {vol['ativos']} "
        f"(atrasados: {vol['ativos_atrasados']}) | Concluídos: {vol['concluidos']}"
    )
    if vol["taxa_resolucao_pct"] is not None:
        print(
            f"Taxa de resolução: {vol['taxa_resolucao_pct']}% | "
            f"Escalonados: {vol['escalonados']}"
        )
    print(
        f"1ª resposta — média: {_format_minutes(times['primeira_resposta_media'])} | "
        f"mediana: {_format_minutes(times['primeira_resposta_mediana'])}"
    )
    print(
        f"Resolução   — média: {_format_minutes(times['resolucao_media'])} | "
        f"mediana: {_format_minutes(times['resolucao_mediana'])}"
    )
    if sla["cumprido_pct"] is not None:
        print(f"SLA cumprido: {sla['cumprido_pct']}% ({sla['avaliados']} chamados concluídos)")
        for prio, pct in sla["por_prioridade_pct"].items():
            print(f"  {prio}: {pct}%")
    else:
        print("SLA cumprido: sem chamados concluídos para avaliação.")

    for label, key in (
        ("Categorias", "categorias"),
        ("Status", "status"),
        ("Prioridades", "prioridades"),
        ("Tipos ITIL", "tipos"),
        ("Carga ativa por técnico", "carga_ativa_por_tecnico"),
    ):
        print(f"\n{label}")
        items = summary["distribuicoes"][key]
        if not items:
            print("  (nenhum registro)")
            continue
        for value, count in items.items():
            print(f"  {value}: {count}")

    if csat["media"] is not None:
        print(
            f"\nCSAT médio: {csat['media']:.2f}/5 "
            f"({csat['avaliacoes']} avaliações | {csat['positivo_pct']}% positivas ≥ 4)"
        )
    else:
        print("\nNenhuma avaliação CSAT registrada.")


def analyze(path: Path, scope: str = "demo", export_json: Path | None = None) -> dict[str, Any]:
    summary = build_summary(path, scope=scope)
    print_report(summary)
    if export_json is not None:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nResumo JSON exportado em: {export_json}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database", type=Path, nargs="?", default=Path("instance/demonstracao.db")
    )
    parser.add_argument(
        "--escopo",
        choices=("demo", "operacional", "todos"),
        default="demo",
        help="Subconjunto de chamados analisados (padrão: demo).",
    )
    parser.add_argument(
        "--exportar-json",
        type=Path,
        default=None,
        help="Caminho opcional para exportar os KPIs em JSON.",
    )
    args = parser.parse_args()
    try:
        analyze(args.database, scope=args.escopo, export_json=args.exportar_json)
    except sqlite3.Error as error:
        parser.exit(1, f"Abra ou migre o banco antes da análise. SQLite: {error}\n")


if __name__ == "__main__":
    main()
