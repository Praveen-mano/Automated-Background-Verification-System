OOSE Project Setup (End-to-End)
This is a complete guide to run the project exactly as intended, including API keys, sample IDs, document upload flow, verifier actions, and expected output.

1. System Requirements
Python 3.10+ (recommended 3.12)
pip
Terminal access (macOS/Linux/Windows)
Internet for API calls (Gemini + Resend)
Tesseract OCR binary (required for local OCR fallback on images)
1.1 Install Tesseract OCR
Install Tesseract based on your OS:

macOS (Homebrew)
brew install tesseract
Ubuntu / Debian
sudo apt update
sudo apt install -y tesseract-ocr
Windows
Install from official UB Mannheim builds:
https://github.com/UB-Mannheim/tesseract/wiki
During install, keep default path or note the install path.
If needed, add Tesseract install directory to PATH.
Verify installation:

tesseract --version
If command is not found, restart terminal and check PATH.

2. Get the Project Ready
Open terminal in project folder:

cd /path/to/OOSE
Create and activate virtual environment:

python3 -m venv .venv
source .venv/bin/activate
Install dependencies:

pip install -r requirements.txt
3. Create API Keys
3.1 Gemini API Key
Use Google AI Studio:

API key page: https://aistudio.google.com/app/apikey
Gemini docs: https://ai.google.dev/gemini-api/docs
Rate limits/quota docs: https://ai.google.dev/gemini-api/docs/rate-limits
Important:

If you see HTTP 429 quota errors, your key is valid but project quota is exhausted or unavailable.
In that case, enable quota/billing in your Google project or use another project key.
3.2 Resend API Key
Use Resend dashboard:

API keys: https://resend.com/api-keys
Domains (for production sender verification): https://resend.com/domains
API reference: https://resend.com/docs/api-reference/emails/send-email
Sandbox note:

If MAIL_FROM=onboarding@resend.dev, Resend only allows sending to your own account email.
For this project, use MAIL_SANDBOX_RECIPIENT to force all result emails to one inbox for demos.
4. Configure .env
Copy template:

cp .env.example .env
Set these values in .env:

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
Run:

flask init-db --with-sample --with-mock-registry
This creates:

app tables
sample users
mock verification registries for identity/education/employment/address
6. Run the App
python3 run.py
Open in browser:

http://127.0.0.1:5001
7. Default Demo Accounts
Admin: admin / Admin@123
Recruiter: recruiter1 / Recruiter@123
Verifier: verifier1 / Verifier@123
Candidate: candidate1 / Candidate@123
Note: Login expects username, not email.

8. Profile IDs Required for Verification
In candidate profile, provide IDs mapped to mock registries:

Identity field: ID-1001
Education details: include EDU-2001
Employment details: include EMP-3001
Address details: include ADDR-4001
Example values:

identity_number: ID-1001
education_details: EDU-2001, B.E Computer Science, ABC College
employment_details: EMP-3001, Software Intern, Innotech Systems
address: ADDR-4001, 221B Baker Street, Chennai
9. Sample Documents to Upload
Use pre-generated docs in sample_docs/:

sample_docs/identity_proof_ID-1001.pdf
sample_docs/education_certificate_EDU-2001.pdf
sample_docs/employment_letter_EMP-3001.pdf
sample_docs/address_proof_ADDR-4001.pdf
Upload with matching document types:

identity -> identity PDF
education -> education PDF
employment -> employment PDF
address -> address PDF
10. End-to-End Workflow to Reproduce Expected Output
Login as recruiter1.
Create a verification request for candidate candidate1 and assign verifier verifier1.
Logout and login as candidate1.
Update profile with the IDs listed above.
Upload all four sample documents from sample_docs/.
Logout and login as verifier1.
Open assigned task, review documents/AI summary/confidence.
Update stages to verified/rejected manually.
Once all stages finalize the request (completed or rejected), report is generated.
Use Resend Email button (if needed) to resend notification email.
Expected output:

Request transitions through statuses.
Report view and PDF download available.
Completion email delivered to MAIL_SANDBOX_RECIPIENT (or real recipients if verified domain setup is used).
11. Useful Validation Commands
Mail test:

flask mail-test --to your_email@example.com
Login check:

flask login-check --username verifier1 --password Verifier@123
12. Troubleshooting
Invalid username/password
Use username, not email.
Ensure sample DB init command was run.
Start app from project root.
Gemini errors
404 model not found: change GEMINI_MODEL.
429 quota exceeded: key is valid, quota/billing issue in Google project.
Resend errors
If using onboarding@resend.dev, only sandbox-allowed inbox works.
Set MAIL_SANDBOX_RECIPIENT to your own email for demos.
For real multi-recipient delivery, verify your domain in Resend and use a sender on that domain.
Document view/download errors
Re-upload files if storage path is stale.
Ensure files exist under uploads/.
13. Sharing with Others (Zip)
Before sharing:

Remove real secrets from .env.
Include .env.example.
Optionally include verification.db if you want demo data preloaded.
After receiving zip, friend only needs to follow sections 2, 4, 5, 6.

14. Security
Never commit real API keys/tokens.
Rotate any key that has been shared in chat/screenshots.
