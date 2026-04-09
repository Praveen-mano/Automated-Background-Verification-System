from __future__ import annotations

from pathlib import Path

import click
from flask import Flask, render_template
from flask_login import current_user

from .config import Config
from .extensions import db, login_manager
from .models import (
    MockAddressRecord,
    CandidateProfile,
    MockEducationRecord,
    MockEmploymentRecord,
    MockIdentityRecord,
    Role,
    User,
)
from .services import send_plain_email
from .utils import stage_display_name


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    # Keep local/dev SQLite schema in sync when new models are added.
    # This creates only missing tables and does not drop existing data.
    with app.app_context():
        db.create_all()

    from .routes import (
        admin_bp,
        auth_bp,
        candidate_bp,
        main_bp,
        recruiter_bp,
        reports_bp,
        verifier_bp,
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(candidate_bp)
    app.register_blueprint(recruiter_bp)
    app.register_blueprint(verifier_bp)
    app.register_blueprint(reports_bp)

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    @app.template_filter("stage_label")
    def stage_label(value) -> str:
        raw_value = value.value if hasattr(value, "value") else str(value)
        return stage_display_name(raw_value)

    @app.context_processor
    def inject_notification_count():
        if current_user.is_authenticated:
            unread = sum(1 for n in current_user.notifications if not n.is_read)
            return {"unread_notification_count": unread}
        return {"unread_notification_count": 0}

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("errors/413.html"), 413

    @app.cli.command("init-db")
    @click.option("--with-sample", is_flag=True, help="Seed sample users and candidate data.")
    @click.option("--with-mock-registry", is_flag=True, help="Seed mock verification registries.")
    def init_db(with_sample: bool, with_mock_registry: bool):
        db.create_all()
        if with_sample:
            seed_sample_data()
        if with_mock_registry:
            seed_mock_registry_data()
        if with_sample and with_mock_registry:
            click.echo("Database initialized with sample data and mock registry data.")
        elif with_mock_registry:
            click.echo("Database initialized with mock registry data.")
        elif with_sample:
            click.echo("Database initialized with sample data.")
        else:
            click.echo("Database initialized.")

    @app.cli.command("mail-test")
    @click.option("--to", "recipient", required=True, help="Recipient email for test message.")
    def mail_test(recipient: str):
        click.echo("Mail configuration check:")
        click.echo(f"  MAIL_ENABLED={app.config.get('MAIL_ENABLED')}")
        click.echo(f"  MAIL_API_TOKEN_SET={bool(app.config.get('MAIL_API_TOKEN'))}")
        click.echo(f"  MAIL_FROM={app.config.get('MAIL_FROM')}")
        click.echo(f"  MAIL_SANDBOX_RECIPIENT={app.config.get('MAIL_SANDBOX_RECIPIENT')}")

        ok, detail = send_plain_email(
            [recipient],
            subject="Verification Platform Mail Test",
            body=(
                "This is a test email from your Automated Verification Platform.\n\n"
                "If you received this, SMTP configuration is working."
            ),
        )
        if ok:
            click.echo(f"Mail test success: {detail}")
        else:
            click.echo(f"Mail test failed: {detail}")

    @app.cli.command("login-check")
    @click.option("--username", required=True, help="Username to verify.")
    @click.option("--password", required=True, help="Plain password to test.")
    def login_check(username: str, password: str):
        click.echo(f"DB URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
        user = User.query.filter_by(username=username.strip()).first()
        if user is None:
            click.echo("Result: user not found")
            return

        click.echo(f"User found: id={user.id}, role={user.role.value}, active={user.is_active_user}")
        if not user.is_active_user:
            click.echo("Password check: skipped (user inactive)")
            return

        ok = user.check_password(password)
        click.echo(f"Password check: {'PASS' if ok else 'FAIL'}")

    @app.cli.command("seed-mock-registry")
    def seed_mock_registry():
        seed_mock_registry_data()
        click.echo("Mock verification registry data seeded.")

    return app


def seed_sample_data() -> None:
    samples = [
        {
            "username": "admin",
            "email": "admin@example.com",
            "full_name": "System Admin",
            "role": Role.ADMIN,
            "password": "Admin@123",
        },
        {
            "username": "recruiter1",
            "email": "recruiter@example.com",
            "full_name": "Recruiter One",
            "role": Role.RECRUITER,
            "password": "Recruiter@123",
        },
        {
            "username": "verifier1",
            "email": "verifier@example.com",
            "full_name": "Verifier One",
            "role": Role.VERIFIER,
            "password": "Verifier@123",
        },
        {
            "username": "candidate1",
            "email": "candidate@example.com",
            "full_name": "Candidate One",
            "role": Role.CANDIDATE,
            "password": "Candidate@123",
        },
    ]

    created_users: dict[str, User] = {}

    for entry in samples:
        user = User.query.filter_by(username=entry["username"]).first()
        if user is None:
            user = User(
                username=entry["username"],
                email=entry["email"],
                full_name=entry["full_name"],
                role=entry["role"],
            )
            user.set_password(entry["password"])
            db.session.add(user)
            db.session.flush()
        created_users[entry["username"]] = user

    candidate_user = created_users["candidate1"]
    if candidate_user.candidate_profile is None:
        db.session.add(
            CandidateProfile(
                user_id=candidate_user.id,
                phone="9999999999",
                date_of_birth="2000-01-01",
                identity_number="ID-1001",
                address="ADDR-4001, 221B Baker Street, Chennai",
                education_details="EDU-2001, B.E Computer Science, ABC College",
                employment_details="EMP-3001, Software Intern, Innotech Systems",
            )
        )

    db.session.commit()


def seed_mock_registry_data() -> None:
    identity_rows = [
        ("ID-1001", "Candidate One", "2000-01-01", "national_id"),
        ("ID-1002", "Alice Demo", "1999-07-11", "passport"),
    ]
    education_rows = [
        ("EDU-2001", "Candidate One", "ABC College", "B.E Computer Science", "2022"),
        ("EDU-2002", "Alice Demo", "State University", "B.Sc Mathematics", "2021"),
    ]
    employment_rows = [
        ("EMP-3001", "Candidate One", "Innotech Systems", "Software Intern", "2023-01-02"),
        ("EMP-3002", "Alice Demo", "BluePeak Labs", "Analyst", "2022-09-15"),
    ]
    address_rows = [
        ("ADDR-4001", "Candidate One", "221B Baker Street", "Chennai", "600001"),
        ("ADDR-4002", "Alice Demo", "12 Palm Residency", "Bengaluru", "560001"),
    ]

    for record_id, name, dob, id_type in identity_rows:
        if MockIdentityRecord.query.filter_by(record_id=record_id).first() is None:
            db.session.add(
                MockIdentityRecord(
                    record_id=record_id,
                    candidate_name=name,
                    date_of_birth=dob,
                    id_type=id_type,
                )
            )
    for record_id, name, institution, degree, year in education_rows:
        if MockEducationRecord.query.filter_by(record_id=record_id).first() is None:
            db.session.add(
                MockEducationRecord(
                    record_id=record_id,
                    candidate_name=name,
                    institution=institution,
                    degree=degree,
                    graduation_year=year,
                )
            )
    for record_id, name, employer, designation, start_date in employment_rows:
        if MockEmploymentRecord.query.filter_by(record_id=record_id).first() is None:
            db.session.add(
                MockEmploymentRecord(
                    record_id=record_id,
                    candidate_name=name,
                    employer=employer,
                    designation=designation,
                    start_date=start_date,
                )
            )
    for record_id, name, line, city, postal_code in address_rows:
        if MockAddressRecord.query.filter_by(record_id=record_id).first() is None:
            db.session.add(
                MockAddressRecord(
                    record_id=record_id,
                    candidate_name=name,
                    address_line=line,
                    city=city,
                    postal_code=postal_code,
                )
            )

    db.session.commit()
