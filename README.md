**OOSE Project: End-to-End Setup Guide**

This guide covers all steps to run the OOSE project, including API configuration, sample data, document upload, verification workflow, and expected outputs.

1. System Requirements
Python: 3.10+ (recommended 3.12)
pip: Python package manager
Terminal access: macOS / Linux / Windows
Internet: Required for API calls (Gemini + Resend)
Tesseract OCR: Required for local OCR fallback on documents
1.1 Install Tesseract OCR
macOS (Homebrew):
brew install tesseract
Ubuntu / Debian:
sudo apt update
sudo apt install -y tesseract-ocr
Windows:
Download from UB Mannheim builds
Keep default install path or note it
Add to PATH if needed
Verify installation:
tesseract --version
2. Project Setup

Open terminal in project folder:

cd /path/to/OOSE
2.1 Create Virtual Environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
2.2 Install Dependencies
pip install -r requirements.txt
3. Create API Keys
3.1 Gemini API Key
Generate via Google AI Studio
Docs: Gemini API
Rate limits & quotas: Quota Docs
Note: If you receive HTTP 429 errors, quota is exceeded. Enable billing or use another key.
3.2 Resend API Key
Dashboard: Resend API Keys
Domain verification (for production): Resend Domains
API reference: Send Emails
Sandbox mode: If MAIL_FROM=onboarding@resend.dev, emails can only go to your own inbox. Use MAIL_SANDBOX_RECIPIENT for demo emails.
4. Configure Environment (.env)

Copy template:

cp .env.example .env

Set the following in .env:

SECRET_KEY=change-this-secret
DATABASE_URL=sqlite:///verification.db
UPLOAD_FOLDER=uploads
MAX_CONTENT_LENGTH=10485760

AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-2.0-flash

MAIL_ENABLED=true
MAIL_API_TOKEN=your_resend_key_here
MAIL_FROM=onboarding@resend.dev
MAIL_SANDBOX_RECIPIENT=your_email@example.com
5. Initialize Database with Demo Data
flask init-db --with-sample --with-mock-registry

Creates:

Tables for the app
Sample users
Mock verification registries (identity, education, employment, address)
6. Run the Application
python3 run.py

Open in browser:

http://127.0.0.1:5001
7. Default Demo Accounts
Role	Username	Password
Admin	admin	Admin@123
Recruiter	recruiter1	Recruiter@123
Verifier	verifier1	Verifier@123
Candidate	candidate1	Candidate@123

Note: Login requires username, not email.

8. Candidate Profile IDs
Identity: ID-1001
Education: EDU-2001
Employment: EMP-3001
Address: ADDR-4001

Example candidate profile:

identity_number: ID-1001
education_details: EDU-2001, B.E Computer Science, ABC College
employment_details: EMP-3001, Software Intern, Innotech Systems
address: ADDR-4001, 221B Baker Street, Chennai
9. Sample Documents

Upload documents from sample_docs/:

Document Type	File Name
Identity	identity_proof_ID-1001.pdf
Education	education_certificate_EDU-2001.pdf
Employment	employment_letter_EMP-3001.pdf
Address	address_proof_ADDR-4001.pdf
10. End-to-End Workflow
Login as recruiter1 → Create verification request for candidate1 → Assign verifier1.
Logout → Login as candidate1 → Update profile with IDs → Upload all four documents.
Logout → Login as verifier1 → Review documents, AI summary, and confidence → Mark stages as verified/rejected.
Once complete → Report is generated → Use Resend Email to send notifications.

Expected Output:

Request transitions through statuses
Report view & PDF download available
Completion email delivered to MAIL_SANDBOX_RECIPIENT (or verified domain)
11. Useful Validation Commands
Mail test:
flask mail-test --to your_email@example.com
Login check:
flask login-check --username verifier1 --password Verifier@123
12. Troubleshooting
Invalid username/password: Use username, not email; ensure DB is initialized.
Gemini errors:
404 model not found → Change GEMINI_MODEL
429 quota exceeded → Billing/quota issue
Resend errors: Sandbox emails go only to MAIL_SANDBOX_RECIPIENT
Document view/download errors: Re-upload documents; check files exist under uploads/.
13. Sharing with Others

Before sharing project ZIP:

Remove real secrets from .env
Include .env.example
Optionally include verification.db for preloaded demo data

Recipient only needs sections 2, 4, 5, 6 to get started.

14. Security Notes
Never commit real API keys/tokens
Rotate any keys shared via chat/screenshots
