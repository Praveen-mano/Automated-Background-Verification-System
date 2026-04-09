from __future__ import annotations

import enum
from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(str, enum.Enum):
    ADMIN = "admin"
    RECRUITER = "recruiter"
    CANDIDATE = "candidate"
    VERIFIER = "verifier"


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class StageType(str, enum.Enum):
    IDENTITY = "identity"
    EDUCATION = "education"
    EMPLOYMENT = "employment"
    ADDRESS = "address"


class StageStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    REJECTED = "rejected"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.Enum(Role), nullable=False, default=Role.CANDIDATE)
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)

    candidate_profile = db.relationship(
        "CandidateProfile", back_populates="user", uselist=False
    )

    created_requests = db.relationship(
        "VerificationRequest",
        back_populates="recruiter",
        foreign_keys="VerificationRequest.created_by_recruiter_id",
    )

    assigned_requests = db.relationship(
        "VerificationRequest",
        back_populates="assigned_verifier",
        foreign_keys="VerificationRequest.assigned_verifier_id",
    )

    uploaded_documents = db.relationship(
        "Document", back_populates="uploaded_by", foreign_keys="Document.uploaded_by_user_id"
    )

    notifications = db.relationship("Notification", back_populates="user")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # Flask-Login integration
        return self.is_active_user


class CandidateProfile(db.Model):
    __tablename__ = "candidate_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    date_of_birth = db.Column(db.String(20), nullable=True)
    identity_number = db.Column(db.String(80), nullable=True)
    address = db.Column(db.Text, nullable=True)
    education_details = db.Column(db.Text, nullable=True)
    employment_details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    user = db.relationship("User", back_populates="candidate_profile")
    documents = db.relationship(
        "Document", back_populates="candidate_profile", cascade="all, delete-orphan"
    )
    verification_requests = db.relationship(
        "VerificationRequest",
        back_populates="candidate_profile",
        cascade="all, delete-orphan",
    )


class MockIdentityRecord(db.Model):
    __tablename__ = "mock_identity_records"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.String(80), unique=True, nullable=False)
    candidate_name = db.Column(db.String(120), nullable=False)
    date_of_birth = db.Column(db.String(20), nullable=True)
    id_type = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)


class MockEducationRecord(db.Model):
    __tablename__ = "mock_education_records"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.String(80), unique=True, nullable=False)
    candidate_name = db.Column(db.String(120), nullable=False)
    institution = db.Column(db.String(160), nullable=False)
    degree = db.Column(db.String(160), nullable=True)
    graduation_year = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)


class MockEmploymentRecord(db.Model):
    __tablename__ = "mock_employment_records"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.String(80), unique=True, nullable=False)
    candidate_name = db.Column(db.String(120), nullable=False)
    employer = db.Column(db.String(160), nullable=False)
    designation = db.Column(db.String(120), nullable=True)
    start_date = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)


class MockAddressRecord(db.Model):
    __tablename__ = "mock_address_records"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.String(80), unique=True, nullable=False)
    candidate_name = db.Column(db.String(120), nullable=False)
    address_line = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(80), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    candidate_profile_id = db.Column(
        db.Integer, db.ForeignKey("candidate_profiles.id"), nullable=False
    )
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    doc_type = db.Column(db.String(80), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    content_type = db.Column(db.String(120), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum_sha256 = db.Column(db.String(64), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)

    candidate_profile = db.relationship("CandidateProfile", back_populates="documents")
    uploaded_by = db.relationship("User", back_populates="uploaded_documents")
    analysis = db.relationship(
        "DocumentAIAnalysis",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )


class DocumentAIAnalysis(db.Model):
    __tablename__ = "document_ai_analyses"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), unique=True, nullable=False)
    provider = db.Column(db.String(40), nullable=False, default="local")
    model_name = db.Column(db.String(120), nullable=True)
    extracted_text = db.Column(db.Text, nullable=False, default="")
    summary = db.Column(db.Text, nullable=False, default="")
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    needs_manual_review = db.Column(db.Boolean, nullable=False, default=True)
    raw_response = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    document = db.relationship("Document", back_populates="analysis")


class VerificationRequest(db.Model):
    __tablename__ = "verification_requests"

    id = db.Column(db.Integer, primary_key=True)
    candidate_profile_id = db.Column(
        db.Integer, db.ForeignKey("candidate_profiles.id"), nullable=False
    )
    created_by_recruiter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_verifier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(
        db.Enum(VerificationStatus), nullable=False, default=VerificationStatus.PENDING
    )
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    candidate_profile = db.relationship("CandidateProfile", back_populates="verification_requests")
    recruiter = db.relationship(
        "User",
        back_populates="created_requests",
        foreign_keys=[created_by_recruiter_id],
    )
    assigned_verifier = db.relationship(
        "User",
        back_populates="assigned_requests",
        foreign_keys=[assigned_verifier_id],
    )
    stages = db.relationship(
        "VerificationStage",
        back_populates="verification_request",
        cascade="all, delete-orphan",
        order_by="VerificationStage.id",
    )
    report = db.relationship(
        "VerificationReport",
        back_populates="verification_request",
        uselist=False,
        cascade="all, delete-orphan",
    )


class VerificationStage(db.Model):
    __tablename__ = "verification_stages"
    __table_args__ = (
        db.UniqueConstraint("request_id", "stage_type", name="uq_request_stage_type"),
    )

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("verification_requests.id"), nullable=False)
    stage_type = db.Column(db.Enum(StageType), nullable=False)
    status = db.Column(db.Enum(StageStatus), nullable=False, default=StageStatus.PENDING)
    comments = db.Column(db.Text, nullable=True)
    verified_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    verification_request = db.relationship("VerificationRequest", back_populates="stages")
    verified_by = db.relationship("User")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), nullable=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)

    user = db.relationship("User", back_populates="notifications")


class VerificationReport(db.Model):
    __tablename__ = "verification_reports"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer, db.ForeignKey("verification_requests.id"), unique=True, nullable=False
    )
    generated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    overall_status = db.Column(db.Enum(VerificationStatus), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    generated_at = db.Column(db.DateTime, default=utcnow_naive, nullable=False)

    verification_request = db.relationship("VerificationRequest", back_populates="report")
    generated_by = db.relationship("User")
