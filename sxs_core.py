"""Persistência, relógio de atendimento e validação de anexos."""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

BRT = timezone(timedelta(hours=-3), "BRT")
WAITING = "Aguardando solicitante"
PRIORITIES = ("Baixa", "Média", "Alta", "Crítica")
SLA_HOURS = {"Crítica": (1, 4), "Alta": (2, 8), "Média": (4, 24), "Baixa": (8, 48)}
ATTACHMENT_LIMIT = 2 * 1024 * 1024
TICKET_TYPES = ("Incidente", "Requisição", "Problema")
Pause = tuple[datetime, datetime | None]


def migrate(db: sqlite3.Connection, directory: Path) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    applied = {row[0] for row in db.execute("SELECT version FROM schema_version")}
    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:3])
        if version in applied:
            continue
        pending = ""
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
            pending += line
            if not sqlite3.complete_statement(pending):
                continue
            statement, pending = pending.strip(), ""
            # A versão original criava as colunas de 001 sob demanda.
            legacy = re.match(r"ALTER TABLE (users|tickets) ADD COLUMN (\w+)", statement)
            if version == 1 and legacy:
                table, column = legacy.groups()
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if column in columns:
                    continue
            db.execute(statement)
        if pending.strip():
            raise ValueError(f"SQL incompleto em {path.name}")
        db.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))


def parse_date(value: str) -> datetime:
    date = datetime.fromisoformat(value)
    return date.replace(tzinfo=UTC) if date.tzinfo is None else date


def business_minutes(start: datetime, end: datetime) -> float:
    left, right = start.astimezone(BRT), end.astimezone(BRT)
    if right <= left:
        return 0.0
    if left.date() == right.date():
        return _day_minutes(left, right)
    first_end = datetime.combine(left.date() + timedelta(days=1), time.min, BRT)
    last_start = datetime.combine(right.date(), time.min, BRT)
    weeks, remainder = divmod((last_start - first_end).days, 7)
    full_days = weeks * 5 + sum((first_end.weekday() + i) % 7 < 5 for i in range(remainder))
    return _day_minutes(left, first_end) + full_days * 600 + _day_minutes(last_start, right)


def _day_minutes(left: datetime, right: datetime) -> float:
    if left.weekday() >= 5:
        return 0.0
    opening = datetime.combine(left.date(), time(8), BRT)
    closing = datetime.combine(left.date(), time(18), BRT)
    return max(0.0, (min(right, closing) - max(left, opening)).total_seconds() / 60)


def add_business(start: datetime, minutes: float) -> datetime:
    current = start.astimezone(BRT)
    remaining = timedelta(minutes=max(0, minutes))
    while True:
        opening = datetime.combine(current.date(), time(8), BRT)
        closing = datetime.combine(current.date(), time(18), BRT)
        if current.weekday() < 5 and current < closing:
            current = max(current, opening)
            available = closing - current
            if remaining <= available:
                return current + remaining
            remaining -= available
        current = datetime.combine(current.date() + timedelta(days=1), time(8), BRT)


def load_pauses(db: sqlite3.Connection, ticket_id: int | None = None) -> dict[int, list[Pause]]:
    result: dict[int, list[Pause]] = {}
    sql = "SELECT ticket_id, started_at, ended_at FROM sla_pauses ORDER BY started_at, id"
    if ticket_id is not None:
        sql = sql.replace(" ORDER BY", " WHERE ticket_id = ? ORDER BY")
    records = db.execute(sql, () if ticket_id is None else (ticket_id,))
    for row in records:
        end = parse_date(row["ended_at"]) if row["ended_at"] else None
        result.setdefault(row["ticket_id"], []).append((parse_date(row["started_at"]), end))
    return result


class SLAResult(TypedDict):
    label: str
    color: str
    first_due: str | None
    resolution_due: str | None
    consumption: float
    met: bool | None


def sla_for(ticket: sqlite3.Row, now: datetime, pauses: list[Pause] | None = None) -> SLAResult:
    start = parse_date(ticket["created_at"])
    first = parse_date(ticket["first_response_at"]) if ticket["first_response_at"] else None
    resolved = parse_date(ticket["resolved_at"]) if ticket["resolved_at"] else None
    paused = ticket["status"] == WAITING
    first_hours, resolution_hours = SLA_HOURS[ticket["sla_priority"] or ticket["priority"]]
    periods = pauses or []

    def elapsed(until: datetime) -> float:
        excluded = sum(
            business_minutes(max(start, left), min(until, right or until))
            for left, right in periods
        )
        return max(0, business_minutes(start, until) - excluded)

    def deadline(hours: int, through: datetime) -> str:
        minutes = hours * 60.0
        cursor = start
        for left, right in periods:
            if left > through:
                break
            available = business_minutes(cursor, left)
            if minutes <= available:
                break
            minutes -= available
            cursor = max(cursor, min(right or through, through))
        return add_business(cursor, minutes).isoformat()

    resolution_ratio = elapsed(resolved or now) / (resolution_hours * 60)
    first_ratio = elapsed(first or resolved or now) / (first_hours * 60)
    consumption = max(resolution_ratio, first_ratio if first is None else 0.0)
    met = (
        None
        if resolved is None
        else first is not None and first_ratio <= 1 and resolution_ratio <= 1
    )
    if ticket["status"] in {"Resolvido", "Fechado"}:
        label, color = "Concluído", "neutral"
    elif paused:
        label, color = "SLA pausado", "neutral"
    elif consumption >= 1:
        label, color = "Atrasado", "red"
    elif consumption >= 0.85:
        label, color = "Próximo do vencimento", "amber"
    else:
        label, color = "No prazo", "green"
    return {
        "label": label,
        "color": color,
        "consumption": consumption,
        "met": met,
        "first_due": None if paused and not first else deadline(first_hours, first or now),
        "resolution_due": None if paused else deadline(resolution_hours, resolved or now),
    }


def structured(logger: logging.Logger, event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


def validated_attachment(file: FileStorage) -> tuple[str, str, bytes]:
    original = file.filename or ""
    name = secure_filename(original)
    extension = Path(name).suffix.lower()
    if any(char in original for char in ("/", "\\", "\x00")) or len(name) > 120:
        raise ValueError("Nome de arquivo inválido.")
    body = file.stream.read(ATTACHMENT_LIMIT + 1)
    if not 0 < len(body) <= ATTACHMENT_LIMIT:
        raise ValueError("O anexo deve ter entre 1 byte e 2 MB.")
    if extension == ".png" and body.startswith(b"\x89PNG\r\n\x1a\n"):
        if body[8:16] == b"\x00\x00\x00\x0dIHDR" and body.endswith(b"IEND\xaeB\x60\x82"):
            return name, "image/png", body
    if extension in {".jpg", ".jpeg"} and body.startswith(b"\xff\xd8\xff"):
        if body.endswith(b"\xff\xd9"):
            return name, "image/jpeg", body
    if extension == ".pdf" and re.match(rb"%PDF-[12]\.\d(?:\r\n|\r|\n)", body):
        if body.rstrip().endswith(b"%%EOF"):
            return name, "application/pdf", body
    if extension in {".txt", ".log"}:
        try:
            decoded = body.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("O texto do anexo deve estar em UTF-8.") from error
        if not any(ord(char) < 32 and char not in "\r\n\t" for char in decoded):
            return name, "text/plain", body
    raise ValueError("Formato não permitido ou conteúdo incompatível com a extensão.")
