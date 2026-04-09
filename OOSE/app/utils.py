from __future__ import annotations

from functools import wraps
from flask import abort, current_app
from flask_login import current_user, login_required

from .models import Role, VerificationRequest


def role_required(*roles: Role):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def stage_display_name(stage_value: str) -> str:
    return stage_value.replace("_", " ").title()


def is_request_visible_to_user(user, request: VerificationRequest) -> bool:
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.RECRUITER:
        return request.created_by_recruiter_id == user.id
    if user.role == Role.VERIFIER:
        return request.assigned_verifier_id == user.id
    if user.role == Role.CANDIDATE and user.candidate_profile:
        return request.candidate_profile_id == user.candidate_profile.id
    return False
