from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from .extensions import db
from .models import (
    CandidateProfile,
    Document,
    Notification,
    Role,
    StageStatus,
    User,
    VerificationRequest,
    VerificationStage,
    VerificationStatus,
)
from .services import (
    analyze_document_with_ai,
    apply_ai_preverification,
    create_default_stages,
    create_notification,
    generate_report,
    recompute_request_status,
    role_home_endpoint,
    send_verification_result_emails,
    verify_stage_with_ai,
)
from .utils import allowed_file, is_request_visible_to_user, role_required

auth_bp = Blueprint("auth", __name__)
main_bp = Blueprint("main", __name__)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
candidate_bp = Blueprint("candidate", __name__, url_prefix="/candidate")
recruiter_bp = Blueprint("recruiter", __name__, url_prefix="/recruiter")
verifier_bp = Blueprint("verifier", __name__, url_prefix="/verifier")
reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _is_safe_redirect(target: str) -> bool:
    return target and urlsplit(target).netloc == ""


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _project_root() -> Path:
    return Path(current_app.root_path).parent


def _resolved_upload_root() -> Path:
    upload_root = Path(current_app.config["UPLOAD_FOLDER"])
    if not upload_root.is_absolute():
        upload_root = (_project_root() / upload_root).resolve()
    return upload_root


def _resolved_storage_path(storage_path: str) -> Path:
    path = Path(storage_path)
    if path.is_absolute():
        return path
    return (_project_root() / path).resolve()


@auth_bp.route("/login", methods=["GET", "POST"], endpoint="login")
def auth_login():
    if current_user.is_authenticated:
        return redirect(url_for(role_home_endpoint(current_user.role)))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_active_user:
                flash("Your account is inactive. Contact administrator.", "danger")
                return render_template("auth/login.html")

            login_user(user)
            next_url = request.args.get("next", "")
            if _is_safe_redirect(next_url):
                return redirect(next_url)
            return redirect(url_for(role_home_endpoint(user.role)))

        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST"], endpoint="logout")
@login_required
def auth_logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"], endpoint="register_candidate")
def auth_register_candidate():
    if current_user.is_authenticated:
        return redirect(url_for(role_home_endpoint(current_user.role)))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        phone = request.form.get("phone", "").strip()

        if not all([full_name, email, username, password, confirm_password]):
            flash("Please fill all mandatory fields.", "danger")
            return render_template("auth/register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("auth/register.html")

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("auth/register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return render_template("auth/register.html")

        user = User(full_name=full_name, email=email, username=username, role=Role.CANDIDATE)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        profile = CandidateProfile(user_id=user.id, phone=phone)
        db.session.add(profile)
        db.session.commit()

        flash("Candidate registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@main_bp.route("/", endpoint="index")
def main_index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    return redirect(url_for(role_home_endpoint(current_user.role)))


@main_bp.route("/notifications", endpoint="notifications")
@login_required
def main_notifications():
    items = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("notifications.html", notifications=items)


@main_bp.post("/notifications/<int:notification_id>/read", endpoint="notification_read")
@login_required
def main_notification_read(notification_id: int):
    note = Notification.query.get_or_404(notification_id)
    if note.user_id != current_user.id:
        abort(403)

    note.is_read = True
    db.session.commit()

    redirect_to = note.link if note.link else url_for("main.notifications")
    return redirect(redirect_to)


@main_bp.get("/api/requests/<int:request_id>/status", endpoint="request_status_api")
@login_required
def main_request_status_api(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)
    if not is_request_visible_to_user(current_user, verification_request):
        abort(403)

    return jsonify(
        {
            "request_id": verification_request.id,
            "status": verification_request.status.value,
            "updated_at": verification_request.updated_at.isoformat(timespec="seconds"),
            "stages": [
                {
                    "id": stage.id,
                    "stage": stage.stage_type.value,
                    "status": stage.status.value,
                    "comments": stage.comments or "",
                    "verified_at": (
                        stage.verified_at.isoformat(timespec="seconds") if stage.verified_at else None
                    ),
                }
                for stage in verification_request.stages
            ],
        }
    )


@main_bp.post("/notifications/read-all", endpoint="mark_all_notifications_read")
@login_required
def main_mark_all_notifications_read():
    notes = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
    for note in notes:
        note.is_read = True
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("main.notifications"))


@admin_bp.get("/", endpoint="dashboard")
@role_required(Role.ADMIN)
def admin_dashboard():
    total_users = User.query.count()
    total_requests = VerificationRequest.query.count()
    candidate_count = User.query.filter_by(role=Role.CANDIDATE).count()
    recruiter_count = User.query.filter_by(role=Role.RECRUITER).count()
    verifier_count = User.query.filter_by(role=Role.VERIFIER).count()

    latest_requests = (
        VerificationRequest.query.order_by(VerificationRequest.created_at.desc()).limit(8).all()
    )

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        total_requests=total_requests,
        candidate_count=candidate_count,
        recruiter_count=recruiter_count,
        verifier_count=verifier_count,
        latest_requests=latest_requests,
    )


@admin_bp.get("/users", endpoint="user_list")
@role_required(Role.ADMIN)
def admin_user_list():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/new", methods=["GET", "POST"], endpoint="user_new")
@role_required(Role.ADMIN)
def admin_user_new():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role_value = request.form.get("role", Role.CANDIDATE.value)

        if not all([username, full_name, email, password]):
            flash("All fields are mandatory.", "danger")
            return render_template("admin/user_form.html", roles=Role, user=None)

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("admin/user_form.html", roles=Role, user=None)

        if User.query.filter_by(email=email).first():
            flash("Email already exists.", "danger")
            return render_template("admin/user_form.html", roles=Role, user=None)

        user = User(username=username, full_name=full_name, email=email, role=Role(role_value))
        user.set_password(password)

        db.session.add(user)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("admin.user_list"))

    return render_template("admin/user_form.html", roles=Role, user=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"], endpoint="user_edit")
@role_required(Role.ADMIN)
def admin_user_edit(user_id: int):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        user.full_name = request.form.get("full_name", "").strip()
        user.email = request.form.get("email", "").strip().lower()
        user.role = Role(request.form.get("role", user.role.value))
        user.is_active_user = request.form.get("is_active") == "on"

        new_password = request.form.get("password", "")
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.user_list"))

    return render_template("admin/user_form.html", roles=Role, user=user)


@admin_bp.get("/requests", endpoint="request_monitor")
@role_required(Role.ADMIN)
def admin_request_monitor():
    requests = VerificationRequest.query.order_by(VerificationRequest.created_at.desc()).all()
    return render_template("admin/requests.html", requests=requests, current_user=current_user)


@admin_bp.post("/requests/<int:request_id>/delete", endpoint="request_delete")
@role_required(Role.ADMIN)
def admin_request_delete(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)
    db.session.delete(verification_request)
    db.session.commit()
    flash(f"Verification request #{request_id} deleted.", "success")
    return redirect(url_for("admin.request_monitor"))


@candidate_bp.route("/profile", methods=["GET", "POST"], endpoint="profile")
@role_required(Role.CANDIDATE)
def candidate_profile():
    profile = current_user.candidate_profile

    if profile is None:
        flash("Candidate profile missing. Contact admin.", "danger")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        profile.phone = request.form.get("phone", "").strip()
        profile.date_of_birth = request.form.get("date_of_birth", "").strip()
        profile.identity_number = request.form.get("identity_number", "").strip()
        profile.address = request.form.get("address", "").strip()
        profile.education_details = request.form.get("education_details", "").strip()
        profile.employment_details = request.form.get("employment_details", "").strip()

        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("candidate.profile"))

    return render_template("candidate/profile.html", profile=profile)


@candidate_bp.route("/documents", methods=["GET", "POST"], endpoint="documents")
@role_required(Role.CANDIDATE)
def candidate_documents():
    profile = current_user.candidate_profile
    if profile is None:
        flash("Candidate profile missing.", "danger")
        return redirect(url_for("candidate.profile"))

    if request.method == "POST":
        doc_type = request.form.get("doc_type", "").strip()
        uploaded = request.files.get("document")

        if not doc_type or not uploaded or uploaded.filename == "":
            flash("Document type and file are required.", "danger")
            return redirect(url_for("candidate.documents"))

        if not allowed_file(uploaded.filename):
            flash("Unsupported file type.", "danger")
            return redirect(url_for("candidate.documents"))

        original_filename = uploaded.filename
        safe_name = secure_filename(original_filename)
        storage_name = f"{uuid4().hex}_{safe_name}"

        file_bytes = uploaded.read()
        file_size = len(file_bytes)
        if file_size == 0:
            flash("Uploaded file is empty.", "danger")
            return redirect(url_for("candidate.documents"))

        checksum = hashlib.sha256(file_bytes).hexdigest()

        candidate_folder = _resolved_upload_root() / str(profile.id)
        candidate_folder.mkdir(parents=True, exist_ok=True)

        full_path = (candidate_folder / storage_name).resolve()
        with open(full_path, "wb") as file_handle:
            file_handle.write(file_bytes)

        doc = Document(
            candidate_profile_id=profile.id,
            uploaded_by_user_id=current_user.id,
            doc_type=doc_type,
            original_filename=original_filename,
            storage_filename=storage_name,
            storage_path=str(full_path),
            content_type=uploaded.mimetype,
            size_bytes=file_size,
            checksum_sha256=checksum,
        )
        db.session.add(doc)
        db.session.flush()
        analyze_document_with_ai(doc)

        open_requests = (
            VerificationRequest.query.filter_by(candidate_profile_id=profile.id)
            .filter(VerificationRequest.status.in_([VerificationStatus.PENDING, VerificationStatus.IN_PROGRESS]))
            .all()
        )
        for verification_request in open_requests:
            updated, status_changed = apply_ai_preverification(verification_request)
            if updated or status_changed:
                create_notification(
                    [
                        verification_request.assigned_verifier_id,
                        verification_request.created_by_recruiter_id,
                    ],
                    (
                        f"AI pre-verification updated for request #{verification_request.id}. "
                        "Verifier review is required."
                    ),
                    link=url_for("verifier.task_detail", request_id=verification_request.id),
                )
        db.session.commit()

        if doc.analysis and doc.analysis.needs_manual_review:
            flash(
                (
                    "Document uploaded and AI extraction completed "
                    f"(confidence {doc.analysis.confidence:.2f}, manual review recommended)."
                ),
                "warning",
            )
        else:
            flash("Document uploaded and AI extraction completed.", "success")
        return redirect(url_for("candidate.documents"))

    docs = (
        Document.query.filter_by(candidate_profile_id=profile.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return render_template("candidate/documents.html", documents=docs)


@candidate_bp.get("/documents/<int:document_id>/download", endpoint="document_download")
@role_required(Role.CANDIDATE)
def candidate_document_download(document_id: int):
    profile = current_user.candidate_profile
    document = Document.query.get_or_404(document_id)

    if profile is None or document.candidate_profile_id != profile.id:
        return redirect(url_for("candidate.documents"))

    file_path = _resolved_storage_path(document.storage_path)
    if not file_path.is_file():
        flash("Document file is missing from storage.", "danger")
        return redirect(url_for("candidate.documents"))
    return send_file(file_path, as_attachment=True, download_name=document.original_filename)


@candidate_bp.post("/documents/<int:document_id>/delete", endpoint="document_delete")
@role_required(Role.CANDIDATE)
def candidate_document_delete(document_id: int):
    profile = current_user.candidate_profile
    document = Document.query.get_or_404(document_id)

    if profile is None or document.candidate_profile_id != profile.id:
        flash("Access denied.", "danger")
        return redirect(url_for("candidate.documents"))

    file_path = _resolved_storage_path(document.storage_path) if document.storage_path else None
    db.session.delete(document)
    db.session.commit()

    if file_path and file_path.is_file():
        try:
            file_path.unlink()
        except OSError:
            current_app.logger.warning("Failed to delete document file from disk: %s", file_path)

    flash("Document deleted.", "success")
    return redirect(url_for("candidate.documents"))


@candidate_bp.get("/requests", endpoint="request_list")
@role_required(Role.CANDIDATE)
def candidate_request_list():
    profile = current_user.candidate_profile
    if profile is None:
        flash("Candidate profile missing.", "danger")
        return redirect(url_for("candidate.profile"))

    requests = (
        VerificationRequest.query.filter_by(candidate_profile_id=profile.id)
        .order_by(VerificationRequest.created_at.desc())
        .all()
    )
    return render_template("candidate/requests.html", requests=requests)


@candidate_bp.get("/requests/<int:request_id>", endpoint="request_detail")
@role_required(Role.CANDIDATE)
def candidate_request_detail(request_id: int):
    profile = current_user.candidate_profile
    verification_request = VerificationRequest.query.get_or_404(request_id)

    if profile is None or verification_request.candidate_profile_id != profile.id:
        flash("Access denied.", "danger")
        return redirect(url_for("candidate.request_list"))

    return render_template("candidate/request_detail.html", verification_request=verification_request)


@recruiter_bp.get("/candidates", endpoint="candidate_list")
@role_required(Role.RECRUITER)
def recruiter_candidate_list():
    candidates = CandidateProfile.query.order_by(CandidateProfile.created_at.desc()).all()
    return render_template("recruiter/candidates.html", candidates=candidates)


@recruiter_bp.get("/requests", endpoint="request_list")
@role_required(Role.RECRUITER)
def recruiter_request_list():
    requests = (
        VerificationRequest.query.filter_by(created_by_recruiter_id=current_user.id)
        .order_by(VerificationRequest.created_at.desc())
        .all()
    )
    return render_template("recruiter/requests.html", requests=requests)


@recruiter_bp.route("/requests/new", methods=["GET", "POST"], endpoint="request_new")
@role_required(Role.RECRUITER)
def recruiter_request_new():
    candidates = CandidateProfile.query.order_by(CandidateProfile.id.desc()).all()
    verifiers = User.query.filter_by(role=Role.VERIFIER, is_active_user=True).all()

    if request.method == "POST":
        candidate_profile_id = request.form.get("candidate_profile_id", "", type=int)
        verifier_id = request.form.get("verifier_id", "", type=int)

        candidate_profile = db.session.get(CandidateProfile, candidate_profile_id)
        verifier = User.query.filter_by(id=verifier_id, role=Role.VERIFIER).first()

        if not candidate_profile or not verifier:
            flash("Please select a valid candidate and verifier.", "danger")
            return render_template(
                "recruiter/request_new.html",
                candidates=candidates,
                verifiers=verifiers,
            )

        verification_request = VerificationRequest(
            candidate_profile_id=candidate_profile.id,
            created_by_recruiter_id=current_user.id,
            assigned_verifier_id=verifier.id,
        )

        db.session.add(verification_request)
        db.session.flush()

        create_default_stages(verification_request)
        db.session.flush()

        create_notification(
            [verifier.id],
            f"New verification request #{verification_request.id} assigned.",
            link=url_for("verifier.task_detail", request_id=verification_request.id),
        )
        create_notification(
            [candidate_profile.user_id],
            f"Verification request #{verification_request.id} has been initiated.",
            link=url_for("candidate.request_detail", request_id=verification_request.id),
        )

        if candidate_profile.documents:
            updated, status_changed = apply_ai_preverification(verification_request)
        else:
            updated, status_changed = (False, False)

        if updated or status_changed:
            create_notification(
                [candidate_profile.user_id, current_user.id],
                (
                    f"AI pre-verification prepared for request #{verification_request.id}. "
                    "Verifier review is required."
                ),
                link=url_for("recruiter.request_detail", request_id=verification_request.id),
            )

        db.session.commit()

        flash("Verification request created.", "success")
        return redirect(url_for("recruiter.request_detail", request_id=verification_request.id))

    return render_template("recruiter/request_new.html", candidates=candidates, verifiers=verifiers)


@recruiter_bp.get("/requests/<int:request_id>", endpoint="request_detail")
@role_required(Role.RECRUITER)
def recruiter_request_detail(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)
    if verification_request.created_by_recruiter_id != current_user.id:
        flash("Access denied.", "danger")
        return redirect(url_for("recruiter.request_list"))

    return render_template("recruiter/request_detail.html", verification_request=verification_request)


@recruiter_bp.post("/requests/<int:request_id>/delete", endpoint="request_delete")
@role_required(Role.RECRUITER)
def recruiter_request_delete(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)
    if verification_request.created_by_recruiter_id != current_user.id:
        flash("Access denied.", "danger")
        return redirect(url_for("recruiter.request_list"))

    db.session.delete(verification_request)
    db.session.commit()
    flash(f"Verification request #{request_id} deleted.", "success")
    return redirect(url_for("recruiter.request_list"))


def _get_verifier_request_or_redirect(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)
    if verification_request.assigned_verifier_id != current_user.id:
        flash("Access denied.", "danger")
        return None, redirect(url_for("verifier.task_list"))
    return verification_request, None


def _get_request_document_or_redirect(verification_request: VerificationRequest, document_id: int):
    document = Document.query.get_or_404(document_id)
    if document.candidate_profile_id != verification_request.candidate_profile_id:
        flash("Document does not belong to this request.", "danger")
        return None, redirect(url_for("verifier.task_detail", request_id=verification_request.id))
    if not document.storage_path:
        flash("Document file is missing from storage.", "danger")
        return None, redirect(url_for("verifier.task_detail", request_id=verification_request.id))
    file_path = _resolved_storage_path(document.storage_path)
    if not file_path.is_file():
        flash("Document file is missing from storage.", "danger")
        return None, redirect(url_for("verifier.task_detail", request_id=verification_request.id))
    return (document, file_path), None


@verifier_bp.get("/tasks", endpoint="task_list")
@role_required(Role.VERIFIER)
def verifier_task_list():
    tasks = (
        VerificationRequest.query.filter_by(assigned_verifier_id=current_user.id)
        .order_by(VerificationRequest.created_at.desc())
        .all()
    )
    return render_template("verifier/tasks.html", tasks=tasks)


@verifier_bp.get("/tasks/<int:request_id>", endpoint="task_detail")
@role_required(Role.VERIFIER)
def verifier_task_detail(request_id: int):
    verification_request, redirect_response = _get_verifier_request_or_redirect(request_id)
    if redirect_response:
        return redirect_response
    return render_template("verifier/task_detail.html", verification_request=verification_request)


@verifier_bp.post("/tasks/<int:request_id>/resend-email", endpoint="resend_email")
@role_required(Role.VERIFIER)
def verifier_resend_email(request_id: int):
    verification_request, redirect_response = _get_verifier_request_or_redirect(request_id)
    if redirect_response:
        return redirect_response

    if verification_request.status not in {
        VerificationStatus.COMPLETED,
        VerificationStatus.REJECTED,
    }:
        flash("Email can be resent only after request is completed or rejected.", "warning")
        return redirect(url_for("verifier.task_detail", request_id=request_id))

    sent, detail = send_verification_result_emails(verification_request)
    if sent:
        flash(f"Result email sent. {detail}", "success")
    else:
        flash(f"Email could not be sent. {detail}", "warning")
    return redirect(url_for("verifier.task_detail", request_id=request_id))


@verifier_bp.get(
    "/tasks/<int:request_id>/documents/<int:document_id>/download",
    endpoint="document_download",
)
@role_required(Role.VERIFIER)
def verifier_document_download(request_id: int, document_id: int):
    verification_request, redirect_response = _get_verifier_request_or_redirect(request_id)
    if redirect_response:
        return redirect_response
    doc_and_path, redirect_response = _get_request_document_or_redirect(
        verification_request, document_id
    )
    if redirect_response:
        return redirect_response
    document, file_path = doc_and_path

    return send_file(file_path, as_attachment=True, download_name=document.original_filename)


@verifier_bp.get(
    "/tasks/<int:request_id>/documents/<int:document_id>/view",
    endpoint="document_view",
)
@role_required(Role.VERIFIER)
def verifier_document_view(request_id: int, document_id: int):
    verification_request, redirect_response = _get_verifier_request_or_redirect(request_id)
    if redirect_response:
        return redirect_response
    doc_and_path, redirect_response = _get_request_document_or_redirect(
        verification_request, document_id
    )
    if redirect_response:
        return redirect_response
    document, file_path = doc_and_path

    mimetype = (document.content_type or "").lower()
    viewable = mimetype.startswith("image/") or mimetype in {"application/pdf", "text/plain"}
    if not viewable:
        flash("Inline view is not supported for this file type. Please download it.", "warning")
        return redirect(url_for("verifier.task_detail", request_id=request_id))

    response = send_file(file_path, as_attachment=False, mimetype=document.content_type)
    response.headers["Content-Disposition"] = f'inline; filename="{document.original_filename}"'
    return response


@verifier_bp.post("/tasks/<int:request_id>/stages/<int:stage_id>", endpoint="update_stage")
@role_required(Role.VERIFIER)
def verifier_update_stage(request_id: int, stage_id: int):
    verification_request, redirect_response = _get_verifier_request_or_redirect(request_id)
    if redirect_response:
        return redirect_response

    stage = VerificationStage.query.filter_by(id=stage_id, request_id=request_id).first_or_404()

    status_value = request.form.get("status", "").strip().lower()
    comments = request.form.get("comments", "").strip()

    allowed_statuses = {
        StageStatus.IN_PROGRESS.value,
        StageStatus.VERIFIED.value,
        StageStatus.REJECTED.value,
    }
    if status_value not in allowed_statuses:
        flash("Invalid stage status.", "danger")
        return redirect(url_for("verifier.task_detail", request_id=request_id))

    stage.status = StageStatus(status_value)
    stage.comments = comments
    stage.verified_by_user_id = current_user.id
    if stage.status in {StageStatus.VERIFIED, StageStatus.REJECTED}:
        stage.verified_at = utcnow_naive()

    status_changed, report_created = recompute_request_status(
        verification_request, generated_by_user_id=current_user.id
    )
    should_send_email = status_changed and verification_request.status in {
        VerificationStatus.COMPLETED,
        VerificationStatus.REJECTED,
    }

    recipients = [
        verification_request.created_by_recruiter_id,
        verification_request.candidate_profile.user_id,
    ]

    create_notification(
        recipients,
        (
            f"Request #{verification_request.id}: {stage.stage_type.value} stage updated to "
            f"{stage.status.value}."
        ),
        link=url_for("recruiter.request_detail", request_id=verification_request.id),
    )

    if status_changed:
        create_notification(
            recipients,
            f"Request #{verification_request.id} status changed to {verification_request.status.value}.",
            link=url_for("recruiter.request_detail", request_id=verification_request.id),
        )

    if report_created:
        create_notification(
            recipients,
            f"Automated report generated for request #{verification_request.id}.",
            link=url_for("reports.report_view", request_id=verification_request.id),
        )

    db.session.commit()
    if should_send_email:
        sent, detail = send_verification_result_emails(verification_request)
        if sent:
            flash(f"Verification completed. Status/report email sent. {detail}", "success")
        else:
            flash(
                f"Verification completed, but email could not be sent. {detail}",
                "warning",
            )
    flash("Verification stage updated.", "success")
    return redirect(url_for("verifier.task_detail", request_id=request_id))


def _build_report_pdf(verification_request: VerificationRequest) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 32

    TOP_MARGIN = margin
    BOTTOM_MARGIN = margin
    CONTENT_WIDTH = width - (2 * margin)
    SECTION_HEADER_HEIGHT = 16
    SECTION_GAP = 10
    SMALL_GAP = 6
    ROW_HEIGHT = 16
    TABLE_HEADER_HEIGHT = 16
    CELL_PAD_X = 6

    request = verification_request
    report = request.report
    candidate = request.candidate_profile
    candidate_user = candidate.user
    stages = request.stages

    verified_count = sum(1 for stage in stages if stage.status == StageStatus.VERIFIED)
    rejected_count = sum(1 for stage in stages if stage.status == StageStatus.REJECTED)
    in_progress_count = sum(1 for stage in stages if stage.status == StageStatus.IN_PROGRESS)
    pending_count = sum(1 for stage in stages if stage.status == StageStatus.PENDING)
    total_stages = len(stages)
    completion_pct = int((verified_count / total_stages) * 100) if total_stages else 0

    if request.status == VerificationStatus.COMPLETED:
        recommendation = "Recommended for hiring from verification standpoint."
        decision_bg = colors.HexColor("#eaf7ee")
    elif request.status == VerificationStatus.REJECTED:
        recommendation = "Not recommended until rejected verification findings are resolved."
        decision_bg = colors.HexColor("#fff0f0")
    else:
        recommendation = "Decision pending until verification is finalized."
        decision_bg = colors.HexColor("#fff7e8")

    y = height - TOP_MARGIN

    def new_page() -> None:
        nonlocal y
        pdf.showPage()
        y = height - TOP_MARGIN

    def ensure_space(required_height: float) -> None:
        if y - required_height < BOTTOM_MARGIN:
            new_page()

    def draw_footer() -> None:
        footer_y = BOTTOM_MARGIN - 6
        pdf.setStrokeColor(colors.HexColor("#d2dbe8"))
        pdf.line(margin, footer_y + 10, width - margin, footer_y + 10)
        pdf.setFillColor(colors.HexColor("#607086"))
        pdf.setFont("Helvetica-Oblique", 7.8)
        pdf.drawString(
            margin,
            footer_y,
            "System-generated report for recruitment verification workflow.",
        )
        pdf.drawRightString(width - margin, footer_y, f"Request #{request.id}")

    def draw_report_header() -> None:
        nonlocal y
        ensure_space(40)
        pdf.setTitle(f"Background Verification Report #{request.id}")
        pdf.setFillColor(colors.HexColor("#0f2747"))
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(margin, y, f"Background Verification Report #{request.id}")
        y -= 18
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.HexColor("#5a6778"))
        pdf.drawString(margin, y, f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M')}")
        pdf.drawRightString(width - margin, y, f"Outcome: {report.overall_status.value}")
        y -= SECTION_GAP

    def draw_section_header(text: str) -> None:
        nonlocal y
        ensure_space(SECTION_HEADER_HEIGHT + SMALL_GAP)
        pdf.setFillColor(colors.HexColor("#edf3fb"))
        pdf.rect(
            margin,
            y - SECTION_HEADER_HEIGHT + 2,
            CONTENT_WIDTH,
            SECTION_HEADER_HEIGHT,
            fill=1,
            stroke=0,
        )
        pdf.setFillColor(colors.HexColor("#203c5c"))
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(margin + CELL_PAD_X, y - 9, text)
        y -= SECTION_HEADER_HEIGHT + SMALL_GAP

    def draw_kv_row(l_label: str, l_val: str, r_label: str, r_val: str) -> None:
        nonlocal y
        ensure_space(ROW_HEIGHT)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(margin + CELL_PAD_X, y - 9, l_label)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(margin + 70, y - 9, l_val[:34])
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(margin + 286, y - 9, r_label)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(margin + 356, y - 9, r_val[:28])
        y -= ROW_HEIGHT

    def draw_summary_table() -> None:
        nonlocal y
        headers = ["Total", "Verified", "Rejected", "In Progress", "Pending", "Completion"]
        values = [
            str(total_stages),
            str(verified_count),
            str(rejected_count),
            str(in_progress_count),
            str(pending_count),
            f"{completion_pct}%",
        ]
        col_w = CONTENT_WIDTH / 6

        ensure_space(TABLE_HEADER_HEIGHT + ROW_HEIGHT + SMALL_GAP)
        pdf.setFillColor(colors.HexColor("#1f3d60"))
        pdf.rect(
            margin,
            y - TABLE_HEADER_HEIGHT + 2,
            CONTENT_WIDTH,
            TABLE_HEADER_HEIGHT,
            fill=1,
            stroke=0,
        )
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 8.2)
        for idx, header in enumerate(headers):
            pdf.drawCentredString(margin + (idx + 0.5) * col_w, y - 9, header)
        y -= TABLE_HEADER_HEIGHT

        pdf.setFillColor(colors.HexColor("#f4f8fd"))
        pdf.rect(margin, y - ROW_HEIGHT + 2, CONTENT_WIDTH, ROW_HEIGHT, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 8.8)
        for idx, value in enumerate(values):
            pdf.drawCentredString(margin + (idx + 0.5) * col_w, y - 9, value)
        y -= ROW_HEIGHT + SMALL_GAP

    def draw_decision_summary() -> None:
        nonlocal y
        box_h = 64
        ensure_space(SECTION_HEADER_HEIGHT + box_h + SMALL_GAP)
        draw_section_header("Decision Summary")
        pdf.setFillColor(decision_bg)
        pdf.rect(margin, y - box_h + 2, CONTENT_WIDTH, box_h, fill=1, stroke=0)
        pdf.setStrokeColor(colors.HexColor("#8ea3bf"))
        pdf.rect(margin, y - box_h + 2, CONTENT_WIDTH, box_h, fill=0, stroke=1)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(margin + CELL_PAD_X, y - 13, f"Outcome: {request.status.value.title()}")
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(margin + CELL_PAD_X, y - 31, "Recommendation:")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(margin + CELL_PAD_X, y - 46, recommendation)
        y -= box_h + SMALL_GAP

    stage_col_widths = [82, 72, 120, 94, CONTENT_WIDTH - (82 + 72 + 120 + 94)]

    def draw_stage_table_header() -> None:
        nonlocal y
        ensure_space(TABLE_HEADER_HEIGHT)
        pdf.setFillColor(colors.HexColor("#2f4f73"))
        pdf.rect(
            margin,
            y - TABLE_HEADER_HEIGHT + 2,
            CONTENT_WIDTH,
            TABLE_HEADER_HEIGHT,
            fill=1,
            stroke=0,
        )
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 8)
        x = margin
        headers = ["Stage", "Status", "Verifier", "Checked At", "Notes"]
        for idx, header in enumerate(headers):
            pdf.drawString(x + CELL_PAD_X, y - 9, header)
            x += stage_col_widths[idx]
        y -= TABLE_HEADER_HEIGHT

    def wrap_text(value: str, max_chars: int) -> list[str]:
        words = value.split()
        if not words:
            return ["-"]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def draw_stage_table_rows() -> None:
        nonlocal y
        row_index = 0
        for stage in stages:
            note_text = (stage.comments or "-").replace("\n", " ").strip()
            note_lines = wrap_text(note_text, 38)[:2]
            row_lines = max(1, len(note_lines))
            row_height = ROW_HEIGHT + (row_lines - 1) * 10

            ensure_space(row_height + 2)
            if y == height - TOP_MARGIN:
                draw_report_header()
                draw_section_header("Stage Findings (Continued)")
                draw_stage_table_header()

            bg = colors.HexColor("#f8fbff") if row_index % 2 else colors.white
            pdf.setFillColor(bg)
            pdf.rect(margin, y - row_height + 2, CONTENT_WIDTH, row_height, fill=1, stroke=0)

            stage_cells = [
                stage.stage_type.value.title(),
                stage.status.value,
                (stage.verified_by.full_name if stage.verified_by else "-")[:22],
                stage.verified_at.strftime("%Y-%m-%d") if stage.verified_at else "-",
            ]

            pdf.setFillColor(colors.black)
            pdf.setFont("Helvetica", 8.2)
            x = margin
            for idx, cell in enumerate(stage_cells):
                pdf.drawString(x + CELL_PAD_X, y - 9, cell)
                x += stage_col_widths[idx]

            note_x = margin + sum(stage_col_widths[:4]) + CELL_PAD_X
            text_y = y - 9
            for line in note_lines:
                pdf.drawString(note_x, text_y, line)
                text_y -= 10

            y -= row_height
            row_index += 1

    draw_report_header()

    draw_section_header("Candidate Snapshot")
    draw_kv_row("Name", candidate_user.full_name, "Email", candidate_user.email)
    draw_kv_row(
        "Phone",
        candidate.phone or "Not provided",
        "Identity No",
        candidate.identity_number or "Not provided",
    )
    draw_kv_row("Address", candidate.address or "Not provided", "Request ID", str(request.id))
    y -= SMALL_GAP

    draw_section_header("Verification Summary")
    draw_summary_table()

    draw_section_header("Stage Findings")
    draw_stage_table_header()
    draw_stage_table_rows()
    y -= SMALL_GAP

    draw_decision_summary()
    draw_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def _report_metrics(verification_request: VerificationRequest) -> dict:
    stages = verification_request.stages
    total_stages = len(stages)
    verified_count = sum(1 for stage in stages if stage.status == StageStatus.VERIFIED)
    rejected_count = sum(1 for stage in stages if stage.status == StageStatus.REJECTED)
    in_progress_count = sum(1 for stage in stages if stage.status == StageStatus.IN_PROGRESS)
    pending_count = sum(1 for stage in stages if stage.status == StageStatus.PENDING)
    completion_pct = int((verified_count / total_stages) * 100) if total_stages else 0
    return {
        "total_stages": total_stages,
        "verified_count": verified_count,
        "rejected_count": rejected_count,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "completion_pct": completion_pct,
    }


def _report_recommendation(verification_request: VerificationRequest) -> tuple[str, str]:
    if verification_request.status == VerificationStatus.COMPLETED:
        return "Recommended for hiring from verification standpoint.", "success"
    if verification_request.status == VerificationStatus.REJECTED:
        return "Not recommended until rejected verification findings are resolved.", "danger"
    return "Decision pending until verification is finalized.", "warning"


@reports_bp.get("/<int:request_id>", endpoint="report_view")
@login_required
def reports_report_view(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)

    if not is_request_visible_to_user(current_user, verification_request):
        abort(403)

    if verification_request.status not in {VerificationStatus.COMPLETED, VerificationStatus.REJECTED}:
        flash("Report is available only after verification is completed or rejected.", "warning")

        if current_user.role == Role.RECRUITER:
            return redirect(url_for("recruiter.request_detail", request_id=request_id))
        if current_user.role == Role.CANDIDATE:
            return redirect(url_for("candidate.request_detail", request_id=request_id))
        if current_user.role == Role.VERIFIER:
            return redirect(url_for("verifier.task_detail", request_id=request_id))
        return redirect(url_for("admin.request_monitor"))

    if verification_request.report is None:
        generate_report(verification_request, generated_by_user_id=current_user.id)
        db.session.commit()

    metrics = _report_metrics(verification_request)
    recommendation, recommendation_class = _report_recommendation(verification_request)

    return render_template(
        "reports/view.html",
        verification_request=verification_request,
        total_stages=metrics["total_stages"],
        verified_count=metrics["verified_count"],
        rejected_count=metrics["rejected_count"],
        in_progress_count=metrics["in_progress_count"],
        pending_count=metrics["pending_count"],
        completion_pct=metrics["completion_pct"],
        recommendation=recommendation,
        recommendation_class=recommendation_class,
    )


@reports_bp.get("/<int:request_id>/download", endpoint="report_download")
@login_required
def reports_report_download(request_id: int):
    verification_request = VerificationRequest.query.get_or_404(request_id)

    if not is_request_visible_to_user(current_user, verification_request):
        abort(403)

    if verification_request.report is None:
        generate_report(verification_request, generated_by_user_id=current_user.id)
        db.session.commit()

    report = verification_request.report
    try:
        pdf_bytes = _build_report_pdf(verification_request)
    except ModuleNotFoundError:
        filename = f"verification-report-{request_id}.txt"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(report.summary, mimetype="text/plain", headers=headers)

    filename = f"verification-report-{request_id}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(pdf_bytes, mimetype="application/pdf", headers=headers)
