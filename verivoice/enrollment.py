"""Local account persistence and enrollment policy, independent of the web UI."""
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import wave
import phonenumbers
from .providers.enrollment_api import EnrollmentError, LANGUAGES
from .phone import normalize_phone

MAX_AUDIO = 700000

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + digest

def validate_audio(data):
    if len(data) > MAX_AUDIO:
        raise EnrollmentError("Recording is too large. Keep each sentence under 20 seconds.")
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                raise EnrollmentError("Record mono 16-bit WAV at 16 kHz.")
            frames = wav.getnframes()
            if len(wav.readframes(frames)) != frames * 2:
                raise EnrollmentError("Incomplete WAV recording. Please record again.")
            seconds = frames / 16000
            if not 5 <= seconds <= 20:
                raise EnrollmentError("Read the sentence naturally in 5–20 seconds, then stop.")
            return seconds
    except (wave.Error, EOFError):
        raise EnrollmentError("Invalid WAV recording. Please record again.") from None

class Accounts:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
              language TEXT NOT NULL, phrases TEXT NOT NULL, audios TEXT NOT NULL DEFAULT '[]',
              state TEXT NOT NULL DEFAULT 'pending', hiya_owner TEXT NOT NULL,
              hiya_space TEXT NOT NULL, hiya_region TEXT NOT NULL,
              voiceprint TEXT NOT NULL DEFAULT 'main');
            CREATE TABLE IF NOT EXISTS voice_profiles (
              id TEXT PRIMARY KEY, account TEXT NOT NULL, language TEXT NOT NULL,
              phrases TEXT NOT NULL, audios TEXT NOT NULL DEFAULT '[]',
              state TEXT NOT NULL DEFAULT 'pending', hiya_owner TEXT NOT NULL,
              hiya_space TEXT NOT NULL, hiya_region TEXT NOT NULL,
              voiceprint TEXT NOT NULL DEFAULT 'main', security_phrase_saved INTEGER NOT NULL DEFAULT 0,
              UNIQUE(account,language));
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY, account TEXT NOT NULL, expires REAL NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(accounts)")}
            if "phone" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN phone TEXT")
            if "phone_region" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN phone_region TEXT")
            if "security_phrase_saved" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN security_phrase_saved INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS accounts_phone ON accounts(phone)")
            session_columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            if 'voice' not in session_columns:
                db.execute('ALTER TABLE sessions ADD COLUMN voice TEXT')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, uid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (uid,)).fetchone()
            if row:
                return dict(row)
            profile = db.execute('SELECT * FROM voice_profiles WHERE id=?', (uid,)).fetchone()
            if not profile:
                return None
            owner = db.execute('SELECT * FROM accounts WHERE id=?', (profile['account'],)).fetchone()
            if not owner:
                return None
            return dict(owner) | dict(profile) | {'account_id': owner['id']}

    def profile_table(self, profile):
        return 'voice_profiles' if profile.get('account_id') else 'accounts'

    def voices(self, account):
        root = account.get('account_id', account['id'])
        owner = self.get(root)
        result = [{'language': owner['language'], 'state': owner['state']}]
        with self.connect() as db:
            result.extend(dict(row) for row in db.execute(
                'SELECT language,state FROM voice_profiles WHERE account=? ORDER BY language', (root,)))
        return result

    def select_voice(self, token, language):
        account = self.authenticate(token)
        root = account.get('account_id', account['id'])
        owner = self.get(root)
        with self.connect() as db:
            profile = db.execute('SELECT id FROM voice_profiles WHERE account=? AND language=?', (root,language)).fetchone()
            uid = root if language == owner['language'] else profile['id'] if profile else None
            if uid is None:
                raise EnrollmentError('Enroll this language first.')
            db.execute('UPDATE sessions SET voice=? WHERE token=? AND account=?',
                       (uid,hashlib.sha256(token.encode()).hexdigest(),root))
        return self.get(uid)

    def add_voice(self, token, language, providers):
        account = self.authenticate(token)
        root = account.get('account_id', account['id'])
        if language not in LANGUAGES:
            raise EnrollmentError('Select a supported language.')
        if any(voice['language'] == language for voice in self.voices(account)):
            raise EnrollmentError('This language already has a voice profile. Select it to resume or call.')
        providers.ready()
        phrases = providers.phrases(language)
        uid = 'vv-' + secrets.token_hex(12)
        settings = providers.settings
        with self.connect() as db:
            db.execute("""INSERT INTO voice_profiles
                (id,account,language,phrases,hiya_owner,hiya_space,hiya_region) VALUES (?,?,?,?,?,?,?)""",
                (uid,root,language,json.dumps(phrases,ensure_ascii=False),settings.hiya_owner,settings.hiya_space,settings.hiya_region))
        return self.select_voice(token,language)

    def enrolled_phone(self, phone, language=None):
        """Shared caller-number lookup for browser calls and future phone transport."""
        if not isinstance(phone, str) or not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
        if not row:
            return None
        if language is None or row['language'] == language:
            return dict(row) if row['state'] == 'complete' else None
        with self.connect() as db:
            voice = db.execute("SELECT id FROM voice_profiles WHERE account=? AND language=? AND state='complete'",
                               (row['id'],language)).fetchone()
        return self.get(voice['id']) if voice else None

    def session(self, uid):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO sessions(token,account,expires) VALUES (?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), uid, time.time()+3600*12))
        return token

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute("SELECT account,voice FROM sessions WHERE token=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise EnrollmentError("Please sign in to continue.")
        profile = self.get(row['voice'] or row['account'])
        if not profile or profile.get('account_id',profile['id']) != row['account']:
            raise EnrollmentError('Please sign in to continue.')
        return profile

    def register(self, email, password, language, providers, *, phone_region, phone_number):
        email = email.strip().casefold()
        if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise EnrollmentError("Enter a valid email address.")
        if not 12 <= len(password) <= 128:
            raise EnrollmentError("Use a password between 12 and 128 characters.")
        if language not in LANGUAGES:
            raise EnrollmentError("Select English, Spanish, Hindi, or Russian.")
        phone = normalize_phone(phone_region, phone_number)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone():
                raise EnrollmentError("Account already exists. Sign in to resume enrollment.")
            if db.execute("SELECT 1 FROM accounts WHERE phone=?", (phone,)).fetchone():
                raise EnrollmentError("This phone number is already associated with an account.")
        providers.ready()
        phrases = providers.phrases(language)
        uid = "vv-" + secrets.token_hex(12)
        settings = providers.settings
        try:
            with self.connect() as db:
                db.execute("""INSERT INTO accounts
                  (id,email,password,language,phrases,hiya_owner,hiya_space,hiya_region,phone,phone_region)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""", (uid,email,password_hash(password),language,
                  json.dumps(phrases,ensure_ascii=False),settings.hiya_owner,
                  settings.hiya_space,settings.hiya_region,phone,phone_region))
        except sqlite3.IntegrityError:
            raise EnrollmentError("An account with this email or phone number already exists.") from None
        return self.session(uid)

    def login(self, email, password):
        if len(password) > 128:
            raise EnrollmentError("Email or password is incorrect.")
        identifier = email.strip().casefold()
        with self.connect() as db:
            if '@' in identifier:
                row = db.execute('SELECT * FROM accounts WHERE email=?', (identifier,)).fetchone()
            else:
                try:
                    number = phonenumbers.parse(identifier, None if identifier.startswith('+') else 'US')
                    phone = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164) if phonenumbers.is_valid_number(number) else ''
                except phonenumbers.NumberParseException:
                    phone = ''
                row = db.execute('SELECT * FROM accounts WHERE phone=?', (phone,)).fetchone()
        salt = row["password"].split(":")[0] if row else "00"*16
        computed = password_hash(password, salt)
        if not row or not hmac.compare_digest(computed, row["password"]):
            raise EnrollmentError("Email or password is incorrect.")
        return self.session(row["id"])

    def update(self, uid, *, audios=None, state=None):
        profile = self.get(uid)
        table = self.profile_table(profile)
        with self.connect() as db:
            if audios is not None:
                db.execute(f"UPDATE {table} SET audios=? WHERE id=?", (json.dumps(audios),uid))
            if state is not None:
                db.execute(f"UPDATE {table} SET state=? WHERE id=?", (state,uid))

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

class Enrollment:
    def __init__(self, accounts, providers):
        self.accounts, self.providers = accounts, providers

    def status(self, account):
        audios = json.loads(account["audios"])
        phrases = json.loads(account["phrases"])
        return {"email": account["email"], "language": account["language"],
                "phone": account["phone"], "phone_region": account["phone_region"],
                "accepted": len(audios), "total": len(phrases), "state": account["state"],
                "phrase": phrases[len(audios)] if len(audios) < len(phrases) else None,
                "voices": self.accounts.voices(account),
                "available_languages": [code for code in LANGUAGES if code not in {v['language'] for v in self.accounts.voices(account)}]}

    def bound(self, account):
        for field in ("hiya_owner", "hiya_space", "hiya_region"):
            if account[field] != getattr(self.providers.settings, field):
                raise EnrollmentError("Hiya configuration changed. Restore the original account's space and region.")

    def record(self, account, index, wav):
        self.bound(account)
        audios = json.loads(account["audios"])
        if account["state"] != "pending" or len(audios) >= 5 or index != len(audios):
            raise EnrollmentError("This phrase was already submitted. Refresh your progress.")
        validate_audio(wav)
        transcript, language = self.providers.transcribe(wav)
        if not transcript.strip() or language is None or language.split("-")[0] != account["language"]:
            raise EnrollmentError("The expected language was not detected. Please repeat the displayed sentence.")
        expected = json.loads(account["phrases"])[index]
        if not self.providers.phrase_matches(expected, transcript, account["language"]):
            raise EnrollmentError("The words did not match the displayed sentence. Please try again.")
        audio = self.providers.upload_checked(wav)
        if audio in audios:
            raise EnrollmentError("Please make a fresh recording for each sentence.")
        audios.append(audio)
        self.accounts.update(account["id"], audios=audios)
        return self.status(self.accounts.get(account["id"]))

    def finish(self, account):
        self.bound(account)
        audios = json.loads(account["audios"])
        if len(audios) != 5:
            raise EnrollmentError("Complete all five recordings first.")
        self.providers.finish(account["id"], audios)
        self.accounts.update(account["id"], state="complete")
        return self.status(self.accounts.get(account["id"]))
