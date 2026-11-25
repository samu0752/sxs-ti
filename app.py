"""Central de Chamados SXS TI, com SQLite e Flask."""

from __future__ import annotations

import csv
import hmac
import io
import logging
import os
import re
import secrets
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from sxs_core import (
    ATTACHMENT_LIMIT,
    BRT,
    PRIORITIES,
    TICKET_TYPES,
    WAITING,
    SLAResult,
    business_minutes,
    load_pauses,
    migrate,
    parse_date,
    sla_for,
    structured,
    validated_attachment,
)

BASE_DIR = Path(__file__).resolve().parent
READ_ONLY = os.environ.get("DEMO_READ_ONLY") == "1"
default_database = "demonstracao.db" if READ_ONLY else "chamados.db"
database_variable = "DEMO_DATABASE" if READ_ONLY else "TICKETS_DATABASE"
DATABASE = Path(os.environ.get(database_variable, BASE_DIR / "instance" / default_database))
CATEGORIES = (
    "Acesso e contas",
    "Hardware",
    "Rede e conectividade",
    "Sistemas e software",
    "Impressoras",
    "Outro",
)
STATUSES = ("Aberto", "Em atendimento", WAITING, "Resolvido", "Fechado")
ACTIVE_STATUSES = STATUSES[:3]
COMPLETED_STATUSES = STATUSES[3:]
ROLES = ("solicitante", "tecnico")
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=ATTACHMENT_LIMIT + 64 * 1024,
    MAX_FORM_MEMORY_SIZE=128 * 1024,
    MAX_FORM_PARTS=32,
    DEMO_READ_ONLY=READ_ONLY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

if os.environ.get("TRUST_RENDER_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")
events_logger = logging.getLogger("sxs.events")
events_logger.setLevel(logging.INFO)
events_logger.propagate = False
event_handler = logging.StreamHandler()
event_handler.setFormatter(logging.Formatter("%(message)s"))
events_logger.addHandler(event_handler)
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        events_logger.warning("SQLite WAL indisponível; mantendo journal padrão.")
    return db


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_error: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    with closing(connect_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db, BASE_DIR / "migrations")
        default_sample = "1" if app.config["DEMO_READ_ONLY"] else "0"
        if os.environ.get("LOAD_SAMPLE_DATA", default_sample) == "1":
            if not db.execute("SELECT 1 FROM tickets LIMIT 1").fetchone():
                seed_demo_data(db)


def seed_demo_data(db: sqlite3.Connection) -> None:
    examples = [
        (
            "Marina Costa",
            "VPN desconecta durante o acesso remoto",
            "Rede e conectividade",
            "Alta",
            "Aberto",
            9,
        ),
        (
            "Rafael Lima",
            "Notebook perde conexão durante chamadas",
            "Rede e conectividade",
            "Média",
            "Em atendimento",
            5,
        ),
        (
            "Joana Alves",
            "Permissão de acesso à pasta financeira",
            "Acesso e contas",
            "Baixa",
            WAITING,
            9,
        ),
        (
            "Pedro Nunes",
            "Impressora do setor aparece offline",
            "Impressoras",
            "Alta",
            "Resolvido",
            38,
        ),
        (
            "Camila Rocha",
            "Tela azul ao iniciar a estação de trabalho",
            "Hardware",
            "Crítica",
            "Fechado",
            73,
        ),
        (
            "Lucas Mendes",
            "Instalação do leitor de PDF",
            "Sistemas e software",
            "Baixa",
            "Resolvido",
            120,
        ),
    ]
    now = datetime.now(UTC)
    for name, title, category, priority, status, age in examples:
        created = now - timedelta(hours=age)
        response = created + timedelta(minutes=45) if status != "Aberto" else None
        resolved = created + timedelta(hours=3) if status in COMPLETED_STATUSES else None
        cursor = db.execute(
            """
            INSERT INTO tickets (protocol, requester, email, title, description, category, priority,
                status, created_at, updated_at, resolved_at, first_response_at, is_sample,
                ticket_type, sla_priority, csat)
            VALUES ('PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
            (
                name,
                "demonstracao@example.invalid",
                title,
                f"Chamado fictício de demonstração. {title}.",
                category,
                priority,
                status,
                created.isoformat(timespec="seconds"),
                now_iso(),
                resolved.isoformat(timespec="seconds") if resolved else None,
                response.isoformat(timespec="seconds") if response else None,
                "Requisição" if category == "Sistemas e software" else "Incidente",
                priority,
                5 if status == "Fechado" else None,
            ),
        )
        ticket_id = cursor.lastrowid
        if ticket_id is None:
            raise RuntimeError("O SQLite não retornou o identificador do chamado.")
        protocol = f"CH-{created.year}-{ticket_id:04d}"
        db.execute("UPDATE tickets SET protocol=? WHERE id=?", (protocol, ticket_id))
        if status == WAITING and response:
            row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            sync_pause(db, row, status, response.isoformat(timespec="seconds"))
        if resolved:
            db.execute(
                """
                INSERT INTO timeline (ticket_id, actor, kind, body, created_at)
                VALUES (?, 'Equipe de suporte', 'Solução', ?, ?)
            """,
                (
                    cursor.lastrowid,
                    "Procedimento concluído e funcionamento confirmado.",
                    resolved.isoformat(timespec="seconds"),
                ),
            )


@app.cli.command("criar-usuario")
@click.option("--nome", prompt="Nome")
@click.option("--email", prompt="E-mail")
@click.option("--perfil", type=click.Choice((*ROLES, "administrador")), prompt="Perfil")
@click.option("--usuario", default=None)
@click.password_option("--senha", prompt="Senha")
def create_user(nome: str, email: str, perfil: str, usuario: str | None, senha: str) -> None:
    """Cria uma conta no banco local."""
    if app.config["DEMO_READ_ONLY"]:
        raise click.ClickException("Contas locais estão desabilitadas na demonstração pública.")
    try:
        save_user(
            nome.strip(),
            email.strip(),
            usuario.strip() if usuario else None,
            senha,
            perfil,
            provisioned=True,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Conta criada para {nome.strip()}.")


def save_user(
    name: str, email: str, username: str | None, password: str, role: str, *, provisioned: bool
) -> None:
    if not 1 <= len(name) <= 80 or len(email) > 120 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Informe um nome e um e-mail válidos.")
    if username is not None:
        username = username.lower()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(
                "Use de 3 a 32 caracteres no usuário. "
                "Use letras, números, ponto, hífen ou sublinhado."
            )
    if not 12 <= len(password) <= 128:
        raise ValueError("A senha deve ter entre 12 e 128 caracteres.")
    admin = role == "administrador"
    role = "tecnico" if admin else role
    db = get_db()
    try:
        with db:
            cursor = db.execute(
                """
                INSERT INTO users (
                    name, email, username, password_hash, role, can_manage_users, provisioned
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    name,
                    email.lower(),
                    username,
                    generate_password_hash(password),
                    role,
                    admin,
                    provisioned,
                ),
            )
            if provisioned:
                actor = g.get("user")
                audit_log(
                    db,
                    "conta.criada",
                    "sucesso",
                    actor["id"] if actor else None,
                    f"perfil={role};conta={cursor.lastrowid}",
                )
    except sqlite3.IntegrityError as error:
        raise ValueError("Já existe uma conta com esse usuário ou e-mail.") from error


def csrf_token() -> str:
    return session.setdefault("csrf_token", secrets.token_urlsafe(32))


app.jinja_env.globals["csrf_token"] = csrf_token


@app.template_filter("datetime_br")
def datetime_br(value: str) -> str:
    return parse_date(value).astimezone(BRT).strftime("%d/%m/%Y %H:%M")


@app.template_filter("duration")
def duration(value: float | None) -> str:
    if value is None:
        return "—"
    minutes = max(0, round(value))
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h {remainder:02d}min" if hours else f"{remainder}min"


@app.template_filter("tone")
def tone(value: str) -> str:
    return {
        "Crítica": "red",
        "Alta": "amber",
        "Média": "blue",
        "Baixa": "neutral",
        "Aberto": "blue",
        "Em atendimento": "blue",
        WAITING: "amber",
        "Resolvido": "green",
        "Fechado": "neutral",
    }.get(value, "neutral")


@app.before_request
def before_request() -> ResponseReturnValue | None:
    g.user = None
    g.demo = app.config["DEMO_READ_ONLY"]
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted.encode(), expected.encode()):
            abort(400, description="O formulário expirou. Atualize a página e tente novamente.")
        if g.demo and request.endpoint != "reset_demo":
            abort(403, description="A demonstração está em modo somente leitura.")
    if not g.demo and session.get("user_id") is not None:
        g.user = (
            get_db()
            .execute(
                "SELECT id, name, email, role, can_manage_users FROM users WHERE id = ?",
                (session["user_id"],),
            )
            .fetchone()
        )
        if g.user is None:
            session.pop("user_id", None)
    g.technician = bool(g.user and g.user["role"] == "tecnico")
    g.admin = bool(g.technician and g.user["can_manage_users"])
    if (
        not g.demo
        and not g.user
        and request.endpoint
        in {
            "index",
            "ticket_detail",
            "create_ticket",
            "update_status",
            "assign_ticket",
            "comment",
            "manage_users",
            "audit_logs_view",
            "upload_attachment",
            "download_attachment",
            "rate_ticket",
            "inventory",
            "set_asset",
            "knowledge_base",
            "suggest_articles",
            "edit_macros",
            "link_ticket",
            "export_reports",
        }
    ):
        return redirect(url_for("login"))
    return None


@app.after_request
def response_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
        structured(
            events_logger,
            "http.request",
            at=now_iso(),
            method=request.method,
            endpoint=request.endpoint,
            status=response.status_code,
        )
    return response


def audit_log(
    db: sqlite3.Connection,
    action: str,
    outcome: str,
    actor_id: int | None = None,
    detail: str = "",
) -> None:
    db.execute(
        "INSERT INTO audit_logs(created_at, action, outcome, actor_id, detail) VALUES (?,?,?,?,?)",
        (now_iso(), action, outcome, actor_id, detail),
    )


def authenticate(identifier: str, password: str) -> int | None:
    db = get_db()
    now = datetime.now(UTC)
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM users WHERE email = ? OR username = ?", (identifier, identifier)
        ).fetchone()
        if row and row["locked_until"] and parse_date(row["locked_until"]) > now:
            audit_log(db, "login", "bloqueado", row["id"])
            return None
        password_hash = row["password_hash"] if row else DUMMY_PASSWORD_HASH
        valid = len(password) <= 128 and check_password_hash(password_hash, password)
        if row and valid:
            db.execute(
                "UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (row["id"],)
            )
            audit_log(db, "login", "sucesso", row["id"])
            return int(row["id"])
        if row:
            failures = 1 if row["locked_until"] else row["failed_attempts"] + 1
            locked = (now + timedelta(minutes=15)).isoformat() if failures >= 5 else None
            db.execute(
                "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                (failures, locked, row["id"]),
            )
        audit_log(db, "login", "falha", row["id"] if row else None)
        return None


@app.route("/entrar", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"])
def login() -> ResponseReturnValue:
    if g.demo or g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        identifier = request.form.get("identifier", request.form.get("email", "")).strip()
        password = request.form.get("password", "")
        user_id = authenticate(identifier[:121], password)
        if user_id is not None:
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            return redirect(url_for("index"))
        flash("Usuário, e-mail ou senha inválidos.", "error")
    return render_template("login.html")


def account_form(*, provisioned: bool) -> None:
    name = request.form.get("name", "").strip()[:121]
    email = request.form.get("email", "").strip()[:121]
    username = request.form.get("username", "").strip()[:121]
    role = request.form.get("role", "") if provisioned else "solicitante"
    password = request.form.get("password", "")
    if role not in ROLES:
        raise ValueError("Selecione Solicitante ou Técnico.")
    if password != request.form.get("password_confirm", ""):
        raise ValueError("As senhas precisam ser iguais.")
    save_user(name, email, username, password, role, provisioned=provisioned)


@app.route("/criar-conta", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register() -> ResponseReturnValue:
    if g.demo:
        abort(404)
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        try:
            account_form(provisioned=False)
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash("Conta criada. Entre com o usuário e a senha que você escolheu.", "success")
            return redirect(url_for("login"))
    return render_template("accounts.html", admin=False)


@app.route("/administracao/contas", methods=["GET", "POST"])
def manage_users() -> ResponseReturnValue:
    if not g.admin:
        abort(403)
    if request.method == "POST":
        try:
            account_form(provisioned=True)
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash("Conta criada. A pessoa já pode entrar com as credenciais escolhidas.", "success")
            return redirect(url_for("manage_users"))
    accounts = (
        get_db()
        .execute("""
        SELECT name, email, username, role, can_manage_users
        FROM users ORDER BY name, id
    """)
        .fetchall()
    )
    return render_template("accounts.html", admin=True, accounts=accounts)


@app.get("/administracao/auditoria")
def audit_logs_view() -> ResponseReturnValue:
    if not g.admin:
        abort(403)
    action = request.args.get("action", "").strip()[:60]
    outcome = request.args.get("outcome", "").strip()[:40]
    db = get_db()
    logs = db.execute(
        """
        SELECT a.id, a.created_at, a.action, a.outcome, a.detail, u.name AS actor_name
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.actor_id
        WHERE (? = '' OR a.action = ?) AND (? = '' OR a.outcome = ?)
        ORDER BY a.id DESC
        LIMIT 100
    """,
        (action, action, outcome, outcome),
    ).fetchall()
    actions = [
        row[0] for row in db.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action")
    ]
    outcomes = [
        row[0] for row in db.execute("SELECT DISTINCT outcome FROM audit_logs ORDER BY outcome")
    ]
    return render_template(
        "audit.html",
        logs=logs,
        actions=actions,
        outcomes=outcomes,
        selected_action=action,
        selected_outcome=outcome,
    )


@app.post("/sair")
def logout() -> ResponseReturnValue:
    if g.user:
        db = get_db()
        with db:
            audit_log(db, "logout", "sucesso", g.user["id"])
    session.clear()
    return redirect(url_for("login"))


def ticket_scope() -> tuple[str, tuple[int, ...]]:
    if g.demo:
        return "t.is_sample = 1", ()
    if g.technician:
        return "1 = 1", ()
    return "t.owner_id = ?", (g.user["id"],)


TICKET_SELECT = """
    SELECT t.*, u.name AS assignee FROM tickets t LEFT JOIN users u ON u.id = t.assignee_id
"""


def ticket_by_id(ticket_id: int) -> sqlite3.Row:
    where, params = ticket_scope()
    ticket = (
        get_db()
        .execute(f"{TICKET_SELECT} WHERE t.id = ? AND {where}", (ticket_id, *params))
        .fetchone()
    )
    if ticket is None:
        abort(404)
    return ticket


def average_minutes(tickets: list[sqlite3.Row], key: str) -> float | None:
    values = [
        (parse_date(ticket[key]) - parse_date(ticket["created_at"])).total_seconds() / 60
        for ticket in tickets
        if ticket[key]
    ]
    return sum(values) / len(values) if values else None


@app.get("/")
def index(form_values: dict[str, str] | None = None) -> ResponseReturnValue:
    scope, scope_params = ticket_scope()
    db = get_db()
    filters = {
        key: request.args.get(key, "").strip()[:100]
        for key in ("q", "status", "category", "priority", "queue")
    }
    clauses = [scope]
    params: list[int | str] = list(scope_params)
    if filters["q"]:
        clauses.append(
            "(t.protocol LIKE ? OR t.requester LIKE ? OR t.title LIKE ? OR t.email LIKE ?)"
        )
        params.extend([f"%{filters['q']}%"] * 4)
    for key, choices in (("status", STATUSES), ("category", CATEGORIES), ("priority", PRIORITIES)):
        if filters[key] in choices:
            clauses.append(f"t.{key} = ?")
            params.append(filters[key])
    if filters["queue"] == "minha" and g.technician:
        clauses.append("t.assignee_id = ?")
        params.append(g.user["id"])
    if filters["queue"] == "livre" and (g.technician or g.demo):
        clauses.append("t.assignee_id IS NULL")
    now = datetime.now(UTC)
    if not g.demo:
        escalate_due(db, now)
    with db:
        db.execute("BEGIN")
        all_tickets = db.execute(f"{TICKET_SELECT} WHERE {scope}", scope_params).fetchall()
        matching = db.execute(
            f"{TICKET_SELECT} WHERE {' AND '.join(clauses)} ORDER BY t.id DESC", params
        ).fetchall()
        pauses = load_pauses(db)
    timings = {
        ticket["id"]: sla_for(ticket, now, pauses.get(ticket["id"])) for ticket in all_tickets
    }
    if filters["queue"] == "atrasados":
        matching = [ticket for ticket in matching if timings[ticket["id"]]["color"] == "red"]
    active = [ticket for ticket in all_tickets if ticket["status"] in ACTIVE_STATUSES]
    overdue = sum(timings[ticket["id"]]["color"] == "red" for ticket in active)
    return render_template(
        "index.html",
        tickets=matching,
        timings=timings,
        filters=filters,
        statuses=STATUSES,
        categories=CATEGORIES,
        priorities=PRIORITIES,
        active_count=len(active),
        resolved_count=len(all_tickets) - len(active),
        overdue_count=overdue,
        avg_minutes=average_minutes(all_tickets, "resolved_at"),
        avg_response=average_minutes(all_tickets, "first_response_at"),
        form_values=form_values or {},
        ticket_types=TICKET_TYPES,
        assets=available_assets(db),
        dashboard=dashboard_data(all_tickets, timings, now),
        status_counts={
            status: sum(ticket["status"] == status for ticket in all_tickets) for status in STATUSES
        },
    )


@app.get("/chamados/<int:ticket_id>")
def ticket_detail(ticket_id: int) -> ResponseReturnValue:
    db = get_db()
    ticket = ticket_by_id(ticket_id)
    if not g.demo and escalate_due(db, datetime.now(UTC), ticket_id):
        ticket = ticket_by_id(ticket_id)
    internal = int(g.technician)
    timeline = db.execute(
        "SELECT * FROM timeline WHERE ticket_id = ? AND internal <= ? ORDER BY id DESC",
        (ticket_id, internal),
    ).fetchall()
    technicians = []
    if g.technician:
        technicians = db.execute(
            "SELECT id, name FROM users WHERE role = 'tecnico' ORDER BY name"
        ).fetchall()
    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        statuses=STATUSES,
        technicians=technicians,
        timeline=timeline,
        sla=sla_for(ticket, datetime.now(UTC), load_pauses(db, ticket_id).get(ticket_id)),
        **detail_data(db, ticket),
    )


def append_event(
    db: sqlite3.Connection,
    ticket_id: int,
    kind: str,
    body: str,
    timestamp: str,
    internal: bool = False,
    *,
    actor: str | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO timeline (ticket_id, actor, kind, body, internal, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (ticket_id, actor or g.user["name"], kind, body, internal, timestamp),
    )


@app.post("/chamados")
def create_ticket() -> ResponseReturnValue:
    values = {
        key: request.form.get(key, "").strip()
        for key in ("requester", "email", "title", "description", "category", "priority")
    }
    if not g.technician:
        values.update(requester=g.user["name"], email=g.user["email"])
    errors = []
    if not values["requester"] or len(values["requester"]) > 80:
        errors.append("Informe o nome do solicitante (até 80 caracteres).")
    if len(values["email"]) > 120 or not EMAIL_PATTERN.fullmatch(values["email"]):
        errors.append("Informe um e-mail válido.")
    if not values["title"] or len(values["title"]) > 120:
        errors.append("Informe um título (até 120 caracteres).")
    if not values["description"] or len(values["description"]) > 2000:
        errors.append("Descreva o problema (até 2.000 caracteres).")
    if values["category"] not in CATEGORIES:
        errors.append("Selecione uma categoria válida.")
    if values["priority"] not in PRIORITIES:
        errors.append("Selecione uma prioridade válida.")
    values["ticket_type"] = request.form.get("ticket_type", "Incidente").strip()
    if values["ticket_type"] not in TICKET_TYPES:
        errors.append("Selecione um tipo de chamado válido.")
    asset_raw = request.form.get("asset_id", "")
    values["asset_id"] = asset_raw
    asset_id = request.form.get("asset_id", type=int)
    file = request.files.get("attachment")
    attachment = None
    if file and file.filename:
        try:
            attachment = validated_attachment(file)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        for message in errors:
            flash(message, "error")
        return index({key: value[:2000] for key, value in values.items()})

    values["email"] = values["email"].lower()
    timestamp = now_iso()
    db = get_db()
    with db:
        if g.technician:
            account = db.execute(
                "SELECT id FROM users WHERE email = ? AND role = 'solicitante' AND provisioned = 1",
                (values["email"],),
            ).fetchone()
            owner = account["id"] if account else None
        else:
            owner = g.user["id"]
        if asset_raw:
            check_asset(db, asset_id, owner)
        cursor = db.execute(
            """
            INSERT INTO tickets (protocol, requester, email, title, description, category, priority,
                status, created_at, updated_at, owner_id, ticket_type, asset_id, sla_priority)
            VALUES ('PENDING', ?, ?, ?, ?, ?, ?, 'Aberto', ?, ?, ?, ?, ?, ?)
        """,
            (
                values["requester"],
                values["email"],
                values["title"],
                values["description"],
                values["category"],
                values["priority"],
                timestamp,
                timestamp,
                owner,
                values["ticket_type"],
                asset_id,
                values["priority"],
            ),
        )
        ticket_id = cursor.lastrowid
        if ticket_id is None:
            raise RuntimeError("O SQLite não retornou o identificador do chamado.")
        protocol = f"CH-{datetime.fromisoformat(timestamp).year}-{ticket_id:04d}"
        db.execute("UPDATE tickets SET protocol = ? WHERE id = ?", (protocol, ticket_id))
        append_event(db, ticket_id, "Abertura", "Chamado registrado.", timestamp)
        if attachment:
            save_attachment(db, ticket_id, attachment)
    notify("chamado.aberto", ticket_id)
    flash(f"Chamado {protocol} registrado com sucesso.", "success")
    return redirect(url_for("index"))


def redirect_after_status(ticket_id: int) -> ResponseReturnValue:
    detail_path = url_for("ticket_detail", ticket_id=ticket_id)
    if request.form.get("return_to") == detail_path:
        return redirect(detail_path)
    return redirect(url_for("index") + "#chamados")


@app.post("/chamados/<int:ticket_id>/status")
def update_status(ticket_id: int) -> ResponseReturnValue:
    if not g.technician:
        abort(403)
    new_status = request.form.get("status", "").strip()
    if new_status == "Aguardando usuário":
        new_status = WAITING
    solution = request.form.get("solution", "").strip()
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        ticket = ticket_by_id(ticket_id)
        if new_status not in STATUSES:
            flash("Status inválido. Nenhuma alteração foi feita.", "error")
            return redirect_after_status(ticket_id)
        if ticket["status"] == new_status:
            return redirect_after_status(ticket_id)
        if len(solution) > 2000:
            abort(400, description="A solução deve ter até 2.000 caracteres.")
        if (
            new_status in COMPLETED_STATUSES
            and ticket["status"] in ACTIVE_STATUSES
            and not solution
        ):
            flash("Registre a solução para concluir o chamado.", "error")
            return redirect_after_status(ticket_id)
        timestamp = now_iso()
        sync_pause(db, ticket, new_status, timestamp)
        resolved_at = (
            (ticket["resolved_at"] or timestamp) if new_status in COMPLETED_STATUSES else None
        )
        db.execute(
            "UPDATE tickets SET status = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
            (new_status, timestamp, resolved_at, ticket_id),
        )
        append_event(db, ticket_id, "Status", f"{ticket['status']} → {new_status}", timestamp)
        if solution and new_status in COMPLETED_STATUSES:
            append_event(db, ticket_id, "Solução", solution, timestamp)
            db.execute(
                "UPDATE tickets SET first_response_at = COALESCE(first_response_at, ?) "
                "WHERE id = ?",
                (timestamp, ticket_id),
            )
    if new_status in COMPLETED_STATUSES and ticket["status"] in ACTIVE_STATUSES:
        notify("chamado.resolvido", ticket_id)
    flash("Status do chamado atualizado.", "success")
    return redirect_after_status(ticket_id)


@app.post("/chamados/<int:ticket_id>/responsavel")
def assign_ticket(ticket_id: int) -> ResponseReturnValue:
    if not g.technician:
        abort(403)
    assignee_id = request.form.get("assignee_id", type=int)
    if request.form.get("assignee_id") and assignee_id is None:
        abort(400)
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        ticket = ticket_by_id(ticket_id)
        assignee = None
        if assignee_id is not None:
            assignee = db.execute(
                "SELECT name FROM users WHERE id = ? AND role = 'tecnico'", (assignee_id,)
            ).fetchone()
            if assignee is None:
                abort(400, description="Selecione um técnico válido.")
        if ticket["assignee_id"] != assignee_id:
            timestamp = now_iso()
            db.execute(
                "UPDATE tickets SET assignee_id = ?, updated_at = ? WHERE id = ?",
                (assignee_id, timestamp, ticket_id),
            )
            previous = ticket["assignee"] or "Sem responsável"
            current = assignee["name"] if assignee else "Sem responsável"
            append_event(db, ticket_id, "Responsável", f"{previous} → {current}", timestamp)
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.post("/chamados/<int:ticket_id>/mensagens")
def comment(ticket_id: int) -> ResponseReturnValue:
    ticket_by_id(ticket_id)
    body = request.form.get("body", "").strip()
    internal = request.form.get("internal") == "1"
    if internal and not g.technician:
        abort(403)
    if not 1 <= len(body) <= 2000:
        flash("Escreva uma mensagem de até 2.000 caracteres.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        ticket = ticket_by_id(ticket_id)
        if ticket["status"] == "Fechado":
            abort(409, description="Reabra o chamado para enviar mensagens.")
        timestamp = now_iso()
        append_event(
            db, ticket_id, "Nota interna" if internal else "Mensagem", body, timestamp, internal
        )
        db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (timestamp, ticket_id))
        if g.technician and not internal:
            db.execute(
                "UPDATE tickets SET first_response_at = COALESCE(first_response_at, ?) "
                "WHERE id = ?",
                (timestamp, ticket_id),
            )
        if not g.technician and ticket["status"] == WAITING:
            sync_pause(db, ticket, "Em atendimento", timestamp)
            db.execute("UPDATE tickets SET status='Em atendimento' WHERE id=?", (ticket_id,))
            append_event(db, ticket_id, "Status", f"{WAITING} → Em atendimento", timestamp)
    if g.technician and not internal:
        notify("chamado.resposta_publica", ticket_id)
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


def _wants_json_error() -> bool:
    if request.path.startswith(("/conhecimento/sugestoes", "/health", "/saude")):
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and (
        request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]
    )


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(409)
@app.errorhandler(413)
@app.errorhandler(422)
@app.errorhandler(429)
def http_error(error: HTTPException) -> ResponseReturnValue:
    catalog = {
        400: ("Requisição inválida", "Verifique as informações enviadas."),
        403: ("Acesso negado", "Seu perfil não permite essa ação."),
        404: ("Página não encontrada", "O chamado ou a página não foi encontrado."),
        405: ("Método não permitido", "A operação solicitada não é aceita neste endereço."),
        409: ("Conflito de estado", "A ação não está disponível para este chamado."),
        413: ("Arquivo muito grande", "O envio excede o tamanho permitido."),
        422: ("Dados inválidos", "Não foi possível processar os dados enviados."),
        429: ("Muitas tentativas", "Muitas tentativas. Aguarde e tente novamente."),
    }
    code = error.code or 500
    title, message = catalog.get(code, ("Erro na solicitação", "A ação não pôde ser concluída."))
    if error.description and code != 429 and error.description != type(error).description:
        message = error.description
    if _wants_json_error():
        return jsonify({"code": code, "title": title, "error": message}), code
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(500)
def server_error(_error: Exception) -> ResponseReturnValue:
    structured(
        events_logger,
        "http.error",
        at=now_iso(),
        endpoint=request.endpoint,
        status=500,
    )
    title = "Erro interno do servidor"
    message = "Ocorreu um erro interno. Tente novamente em instantes."
    if _wants_json_error():
        return jsonify({"code": 500, "title": title, "error": message}), 500
    return render_template("error.html", code=500, title=title, message=message), 500


@app.get("/health")
@app.get("/saude")
def health() -> ResponseReturnValue:
    try:
        get_db().execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.Error:
        return {"status": "unavailable"}, 503
    return {"status": "ok"}


def sync_pause(db: sqlite3.Connection, ticket: sqlite3.Row, status: str, at: str) -> None:
    if ticket["paused_at"] and status != WAITING:
        minutes = business_minutes(parse_date(ticket["paused_at"]), parse_date(at))
        db.execute(
            "UPDATE sla_pauses SET ended_at=? WHERE ticket_id=? AND ended_at IS NULL",
            (at, ticket["id"]),
        )
        db.execute(
            """
            UPDATE tickets SET paused_minutes=paused_minutes+?, paused_at=NULL WHERE id=?
        """,
            (minutes, ticket["id"]),
        )
    elif status == WAITING and not ticket["paused_at"]:
        db.execute("INSERT INTO sla_pauses(ticket_id, started_at) VALUES (?,?)", (ticket["id"], at))
        db.execute("UPDATE tickets SET paused_at=? WHERE id=?", (at, ticket["id"]))


def escalate_due(db: sqlite3.Connection, now: datetime, ticket_id: int | None = None) -> int:
    query = """SELECT * FROM tickets WHERE status IN ('Aberto','Em atendimento')
               AND escalated_at IS NULL AND priority != 'Crítica'"""
    params = () if ticket_id is None else (ticket_id,)
    if ticket_id is not None:
        query += " AND id=?"
    if db.execute(query + " LIMIT 1", params).fetchone() is None:
        return 0
    count = 0
    with db:
        db.execute("BEGIN IMMEDIATE")
        pauses = load_pauses(db, ticket_id)
        for ticket in db.execute(query, params).fetchall():
            if sla_for(ticket, now, pauses.get(ticket["id"]))["consumption"] < 0.85:
                continue
            current = ticket["priority"]
            raised = PRIORITIES[PRIORITIES.index(current) + 1]
            at = now.isoformat(timespec="seconds")
            db.execute(
                """
                UPDATE tickets SET priority=?, sla_priority=COALESCE(sla_priority, priority),
                    escalated_at=?, updated_at=? WHERE id=?
            """,
                (raised, at, at, ticket["id"]),
            )
            append_event(
                db,
                ticket["id"],
                "Escalonamento",
                f"{current} → {raised}",
                at,
                internal=True,
                actor="Sistema",
            )
            count += 1
    return count


@app.cli.command("processar-sla")
def process_sla() -> None:
    """Escalona chamados elegíveis fora do ciclo HTTP."""
    if app.config["DEMO_READ_ONLY"]:
        raise click.ClickException("A operação exige um banco operacional.")
    click.echo(f"Chamados escalonados: {escalate_due(get_db(), datetime.now(UTC))}")


@app.cli.command("preparar-teste")
def prepare_test() -> None:
    """Prepara uma conta de técnico descartável no banco local padrão."""
    if (
        app.config["DEMO_READ_ONLY"]
        or app.config["SESSION_COOKIE_SECURE"]
        or "TICKETS_DATABASE" in os.environ
        or DATABASE != BASE_DIR / "instance" / "chamados.db"
    ):
        raise click.ClickException("Use apenas o banco local padrão via HTTP em localhost.")
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username='tecnico-teste'").fetchone():
        click.echo("A conta de teste já existe.")
        return
    save_user(
        "Técnico de teste",
        "tecnico-teste@example.invalid",
        "tecnico-teste",
        "SxsTiLocal-2026!",
        "tecnico",
        provisioned=True,
    )
    click.echo("Conta local criada: tecnico-teste.")


def dashboard_data(
    tickets: list[sqlite3.Row],
    timings: dict[int, SLAResult],
    now: datetime,
) -> dict[str, object]:
    today = now.astimezone(BRT).date()
    days = [today - timedelta(days=days) for days in range(6, -1, -1)]
    dates = Counter(parse_date(ticket["created_at"]).astimezone(BRT).date() for ticket in tickets)
    categories = Counter(ticket["category"] for ticket in tickets)
    workload = Counter(
        ticket["assignee"] or "Sem responsável"
        for ticket in tickets
        if ticket["status"] in ACTIVE_STATUSES
    )
    completed = [value["met"] for value in timings.values() if value["met"] is not None]
    scores = [ticket["csat"] for ticket in tickets if ticket["csat"] is not None]
    return {
        "trend": [(day.strftime("%d/%m"), dates[day]) for day in days],
        "categories": categories.most_common(5),
        "workload": workload.most_common() if g.technician or g.demo else [],
        "sla": round(100 * sum(completed) / len(completed), 1) if completed else None,
        "csat": round(sum(scores) / len(scores), 1) if scores else None,
    }


def notify(event: str, ticket_id: int) -> None:
    structured(
        events_logger,
        "email.simulado",
        at=now_iso(),
        template=event,
        ticket_id=ticket_id,
        recipient_role="solicitante",
    )


def save_attachment(
    db: sqlite3.Connection,
    ticket_id: int,
    attachment: tuple[str, str, bytes],
) -> None:
    db.execute(
        """
        INSERT INTO attachments(ticket_id, uploader_id, filename, mime_type, content, created_at)
        VALUES (?,?,?,?,?,?)
    """,
        (ticket_id, g.user["id"], *attachment, now_iso()),
    )


@app.post("/chamados/<int:ticket_id>/anexos")
def upload_attachment(ticket_id: int) -> ResponseReturnValue:
    ticket_by_id(ticket_id)
    file = request.files.get("attachment")
    if not file:
        abort(400, description="Selecione um anexo.")
    try:
        data = validated_attachment(file)
    except ValueError as error:
        abort(400, description=str(error))
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        if ticket_by_id(ticket_id)["status"] == "Fechado":
            abort(409, description="Reabra o chamado para enviar anexos.")
        save_attachment(db, ticket_id, data)
        append_event(db, ticket_id, "Anexo", data[0], now_iso())
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.get("/chamados/<int:ticket_id>/anexos/<int:attachment_id>")
def download_attachment(ticket_id: int, attachment_id: int) -> ResponseReturnValue:
    ticket_by_id(ticket_id)
    row = (
        get_db()
        .execute(
            """
        SELECT filename, content FROM attachments WHERE id=? AND ticket_id=?
    """,
            (attachment_id, ticket_id),
        )
        .fetchone()
    )
    if not row or g.demo:
        abort(404)
    return send_file(
        io.BytesIO(row["content"]),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=row["filename"],
        conditional=False,
    )


@app.post("/chamados/<int:ticket_id>/csat")
def rate_ticket(ticket_id: int) -> ResponseReturnValue:
    ticket = ticket_by_id(ticket_id)
    if g.technician or ticket["owner_id"] != g.user["id"]:
        abort(403)
    score = request.form.get("score", type=int)
    if score is None or not 1 <= score <= 5:
        abort(400, description="Selecione uma nota de 1 a 5.")
    db = get_db()
    with db:
        saved = db.execute(
            """
            UPDATE tickets SET csat=? WHERE id=? AND status='Fechado' AND csat IS NULL
        """,
            (score, ticket_id),
        )
        if saved.rowcount != 1:
            abort(409, description="A avaliação só pode ser enviada uma vez após o fechamento.")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


def available_assets(db: sqlite3.Connection) -> list[sqlite3.Row]:
    if g.demo:
        return []
    query = """
        SELECT a.id, a.tag, a.equipment, u.name FROM assets a JOIN users u ON u.id=a.user_id
    """
    if g.technician:
        return db.execute(query + " ORDER BY a.tag").fetchall()
    return db.execute(query + " WHERE a.user_id=? ORDER BY a.tag", (g.user["id"],)).fetchall()


def check_asset(db: sqlite3.Connection, asset_id: int | None, owner: int | None) -> None:
    if not db.execute(
        "SELECT 1 FROM assets WHERE id=? AND user_id=?", (asset_id, owner)
    ).fetchone():
        abort(400, description="O ativo deve pertencer ao solicitante do chamado.")


@app.route("/inventario", methods=["GET", "POST"])
def inventory() -> ResponseReturnValue:
    if not g.technician:
        abort(403)
    db = get_db()
    if request.method == "POST":
        tag = request.form.get("tag", "").strip().upper()
        equipment = request.form.get("equipment", "").strip()
        user_id = request.form.get("user_id", type=int)
        if not 1 <= len(tag) <= 40 or not 1 <= len(equipment) <= 120:
            abort(400, description="Informe patrimônio e equipamento válidos.")
        if not db.execute(
            "SELECT 1 FROM users WHERE id=? AND role='solicitante'", (user_id,)
        ).fetchone():
            abort(400, description="Selecione um solicitante válido.")
        try:
            with db:
                added = db.execute(
                    "INSERT INTO assets(tag, equipment, user_id) VALUES (?,?,?)",
                    (tag, equipment, user_id),
                )
                audit_log(db, "ativo.criado", "sucesso", g.user["id"], str(added.lastrowid))
        except sqlite3.IntegrityError:
            abort(409, description="Já existe um ativo com esse patrimônio.")
        return redirect(url_for("inventory"))
    users = db.execute("SELECT id, name FROM users WHERE role='solicitante' ORDER BY name")
    return render_template("inventory.html", assets=available_assets(db), users=users.fetchall())


@app.post("/chamados/<int:ticket_id>/ativo")
def set_asset(ticket_id: int) -> ResponseReturnValue:
    ticket = ticket_by_id(ticket_id)
    if not g.technician:
        abort(403)
    asset_id = request.form.get("asset_id", type=int)
    db = get_db()
    with db:
        if request.form.get("asset_id"):
            check_asset(db, asset_id, ticket["owner_id"])
        db.execute("UPDATE tickets SET asset_id=? WHERE id=?", (asset_id, ticket_id))
        audit_log(db, "ativo.vinculado", "sucesso", g.user["id"], f"chamado={ticket_id}")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/conhecimento", methods=["GET", "POST"])
def knowledge_base() -> ResponseReturnValue:
    db = get_db()
    if request.method == "POST":
        if not g.technician:
            abort(403)
        category = request.form.get("category", "")
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if category not in CATEGORIES or not 1 <= len(title) <= 120 or not 1 <= len(body) <= 4000:
            abort(400, description="Informe título, categoria e artigo válidos.")
        try:
            with db:
                result = db.execute(
                    "INSERT INTO kb_articles(category,title,body,author_id) VALUES (?,?,?,?)",
                    (category, title, body, g.user["id"]),
                )
                audit_log(db, "artigo.publicado", "sucesso", g.user["id"], str(result.lastrowid))
        except sqlite3.IntegrityError:
            abort(409, description="Já existe um artigo com esse título na categoria.")
        return redirect(url_for("knowledge_base"))
    category = request.args.get("category", "")
    query = """
        SELECT * FROM kb_articles
        WHERE (?='' OR category=?) AND (?=0 OR author_id IS NULL) ORDER BY category, title
    """
    articles = db.execute(query, (category, category, g.demo)).fetchall()
    return render_template(
        "knowledge.html", articles=articles, categories=CATEGORIES, selected=category
    )


@app.get("/conhecimento/sugestoes")
def suggest_articles() -> ResponseReturnValue:
    category = request.args.get("category", "")
    if category not in CATEGORIES:
        return jsonify([])
    rows = get_db().execute(
        """
        SELECT id, title FROM kb_articles WHERE category=? AND (?=0 OR author_id IS NULL)
        ORDER BY title LIMIT 5
    """,
        (category, g.demo),
    )
    return jsonify(
        [
            {
                "title": row["title"],
                "url": url_for("knowledge_base", category=category, _anchor=f"artigo-{row['id']}"),
            }
            for row in rows
        ]
    )


@app.route("/administracao/macros", methods=["GET", "POST"])
def edit_macros() -> ResponseReturnValue:
    if not g.admin:
        abort(403)
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if not 1 <= len(title) <= 80 or not 1 <= len(body) <= 2000:
            abort(400, description="Informe título e resposta válidos.")
        with db:
            db.execute(
                """
                INSERT INTO macros(title,body) VALUES (?,?)
                ON CONFLICT(title) DO UPDATE SET body=excluded.body
            """,
                (title, body),
            )
            audit_log(db, "macro.salva", "sucesso", g.user["id"])
        return redirect(url_for("edit_macros"))
    return render_template("macros.html", macros=db.execute("SELECT * FROM macros ORDER BY title"))


@app.post("/chamados/<int:ticket_id>/vinculos")
def link_ticket(ticket_id: int) -> ResponseReturnValue:
    ticket_by_id(ticket_id)
    if not g.technician:
        abort(403)
    protocol = request.form.get("protocol", "").strip().upper()
    kind = request.form.get("kind", "")
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        other = db.execute("SELECT id FROM tickets WHERE protocol=?", (protocol,)).fetchone()
        if not other or other["id"] == ticket_id or kind not in {"duplicado", "relacionado"}:
            abort(400, description="Informe outro protocolo e selecione um tipo de vínculo.")
        left, right = sorted((ticket_id, other["id"]))
        db.execute(
            """
            INSERT INTO ticket_links(ticket_id, related_id, kind) VALUES (?,?,?)
            ON CONFLICT(ticket_id, related_id) DO UPDATE SET kind=excluded.kind
        """,
            (left, right, kind),
        )
        append_event(db, ticket_id, "Vínculo", f"{protocol} · {kind}", now_iso(), internal=True)
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


def detail_data(db: sqlite3.Connection, ticket: sqlite3.Row) -> dict[str, Any]:
    assets: list[sqlite3.Row] = []
    links: list[sqlite3.Row] = []
    macros: list[sqlite3.Row] = []
    attachments: list[sqlite3.Row] = []
    asset = None
    if not g.demo:
        attachments = db.execute(
            """
            SELECT id, filename FROM attachments WHERE ticket_id=? ORDER BY id
        """,
            (ticket["id"],),
        ).fetchall()
        asset = db.execute(
            "SELECT tag, equipment FROM assets WHERE id=?", (ticket["asset_id"],)
        ).fetchone()
    if g.technician:
        assets = db.execute(
            "SELECT id, tag, equipment FROM assets WHERE user_id=? ORDER BY tag",
            (ticket["owner_id"],),
        ).fetchall()
        macros = db.execute("SELECT title, body FROM macros ORDER BY title").fetchall()
        links = db.execute(
            """
            SELECT other.id, other.protocol, l.kind FROM ticket_links l
            JOIN tickets other
              ON other.id=CASE WHEN l.ticket_id=? THEN l.related_id ELSE l.ticket_id END
            WHERE l.ticket_id=? OR l.related_id=? ORDER BY other.id
        """,
            (ticket["id"],) * 3,
        ).fetchall()
    return {
        "attachments": attachments,
        "asset": asset,
        "assets": assets,
        "links": links,
        "macros": macros,
    }


def spreadsheet_cell(value: object) -> object:
    if isinstance(value, str) and (
        value.startswith(("\r", "\n", "\t")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


@app.get("/relatorios/exportar.csv")
def export_reports() -> ResponseReturnValue:
    if not (g.technician or g.demo):
        abort(403)
    db = get_db()
    query = "SELECT * FROM vw_relatorio_chamados"
    if g.demo:
        query += " WHERE demonstracao=1"
    with db:
        db.execute("BEGIN")
        cursor = db.execute(query + " ORDER BY chamado_id")
        rows = cursor.fetchall()
        allowed = {row["chamado_id"] for row in rows}
        tickets = db.execute("SELECT * FROM tickets").fetchall()
        pauses = load_pauses(db)
    now = datetime.now(UTC)
    outcomes = {
        ticket["id"]: sla_for(ticket, now, pauses.get(ticket["id"]))["met"]
        for ticket in tickets
        if ticket["id"] in allowed
    }
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    fields = [column[0] for column in cursor.description or ()]
    writer.writerow([*fields, "sla_cumprido"])
    for row in rows:
        met = outcomes.get(row["chamado_id"])
        cells = [spreadsheet_cell(value) for value in row]
        if g.demo:
            for column in ("tecnico", "patrimonio"):
                cells[fields.index(column)] = None
        writer.writerow([*cells, "" if met is None else "sim" if met else "não"])
    if g.user:
        with db:
            audit_log(db, "exportacao.csv", "sucesso", g.user["id"], f"linhas={len(rows)}")
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="chamados.csv"'},
    )


@app.post("/demo/resetar")
@limiter.limit("3 per minute")
def reset_demo() -> ResponseReturnValue:
    if not g.demo:
        abort(404)
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "DELETE FROM timeline WHERE ticket_id IN (SELECT id FROM tickets WHERE is_sample=1)"
        )
        db.execute("DELETE FROM tickets WHERE is_sample=1")
        seed_demo_data(db)
        audit_log(db, "demo.reset", "sucesso")
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
