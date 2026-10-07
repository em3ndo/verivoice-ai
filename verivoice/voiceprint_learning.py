"""Persist qualified call samples and activate computed voiceprint versions."""
import asyncio
import json
import math
import time

from .providers.hiya_client import optional_score
from .providers.hiya_voiceprint_builder import HiyaVoiceprintBuilder

TRAINING_THRESHOLD = .70


class VoiceprintLearning:
    def __init__(self, accounts, settings, *, record, builder=None):
        self.accounts, self.settings, self.record = accounts, settings, record
        self.builder = builder or HiyaVoiceprintBuilder(settings)
        self.tasks = {}
        self.again = set()
        with accounts.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS voiceprint_samples (
              id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
              audio TEXT NOT NULL, verification TEXT NOT NULL, source_voiceprint TEXT NOT NULL,
              identity_score REAL NOT NULL, synthesis_score REAL NOT NULL, replay_score REAL NOT NULL,
              seconds REAL NOT NULL, voice_seconds REAL NOT NULL, created REAL NOT NULL,
              language_score REAL NOT NULL DEFAULT 1,
              UNIQUE(account,audio));
            CREATE TABLE IF NOT EXISTS voiceprint_versions (
              account TEXT NOT NULL, handle TEXT NOT NULL, previous TEXT NOT NULL,
              audios TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(account,handle));
            CREATE TABLE IF NOT EXISTS voiceprint_learning_state (
              account TEXT PRIMARY KEY, last_sample INTEGER NOT NULL DEFAULT 0);
            """)
            columns = {row['name'] for row in db.execute("PRAGMA table_info(voiceprint_samples)")}
            if 'language_score' not in columns:
                db.execute("ALTER TABLE voiceprint_samples ADD COLUMN language_score REAL NOT NULL DEFAULT 1")
            if 'is_security_phrase' not in columns:
                db.execute("ALTER TABLE voiceprint_samples ADD COLUMN is_security_phrase INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_security_phrase_per_account
                ON voiceprint_samples(account) WHERE is_security_phrase=1""")

    def accept(self, account, scores, *, language_score=1.0, is_security_phrase=False):
        values = [optional_score(value) for value in (scores.identity, scores.synthesis, scores.replay, language_score)]
        if any(value is None or value < TRAINING_THRESHOLD for value in values):
            return False
        if any(account[field] != getattr(self.settings, field)
               for field in ("hiya_region", "hiya_owner", "hiya_space")):
            return False
        if not all(isinstance(value, str) and value for value in (scores.audio_handle, scores.verification_handle)):
            return False
        seconds, voiced = scores.audio_seconds, scores.voice_seconds
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (seconds, voiced)):
            return False
        # digital/v1 with minAudios=5 requires >=4.5/5 seconds of voice per audio.
        if not .9 <= voiced <= seconds <= 120:
            return False
        with self.accounts.connect() as db:
            if is_security_phrase:
                current = db.execute('SELECT security_phrase_saved FROM accounts WHERE id=?', (account['id'],)).fetchone()
                if current is None or current[0]:
                    return False
            return db.execute("""INSERT OR IGNORE INTO voiceprint_samples
                (account,audio,verification,source_voiceprint,identity_score,synthesis_score,
                 replay_score,language_score,seconds,voice_seconds,created,is_security_phrase) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (account["id"], scores.audio_handle, scores.verification_handle, account["voiceprint"],
                 *values, seconds, voiced, time.time(), int(is_security_phrase))).rowcount == 1

    def schedule(self, uid, call):
        if uid in self.tasks:
            self.again.add(uid)
            return
        self.tasks[uid] = asyncio.create_task(self._run(uid, call))

    async def _run(self, uid, call):
        try:
            while True:
                self.again.discard(uid)
                try:
                    await asyncio.wait_for(self.refresh(uid, call), 180)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Learning must never interrupt a call or replace a usable
                    # voiceprint with an incomplete version. Retry next call/startup.
                    self.record(call, "voiceprint_update_failed", error_type=type(error).__name__)
                if uid not in self.again:
                    return
        finally:
            self.tasks.pop(uid, None)
            self.again.discard(uid)

    async def refresh(self, uid, call):
        account = self.accounts.get(uid)
        if not account or account["state"] != "complete":
            return
        with self.accounts.connect() as db:
            samples = [dict(row) for row in db.execute("SELECT * FROM voiceprint_samples WHERE account=? ORDER BY id", (uid,))]
            state = db.execute("SELECT last_sample FROM voiceprint_learning_state WHERE account=?", (uid,)).fetchone()
        if not samples or (state and state[0] >= samples[-1]["id"]):
            return
        cutoff = samples[-1]["id"]
        self.record(call, "voiceprint_update_started", candidates=len(samples))
        built = await self.builder.build(account, samples)
        with self.accounts.connect() as db:
            # Switch atomically only if this is still the account and version
            # we built from. In-progress calls keep their original snapshot.
            changed = db.execute("""UPDATE accounts SET voiceprint=? WHERE id=? AND state='complete'
                AND voiceprint=? AND hiya_region=? AND hiya_owner=? AND hiya_space=? AND phone=?""",
                (built.handle, uid, account["voiceprint"], account["hiya_region"], account["hiya_owner"],
                 account["hiya_space"], account["phone"])).rowcount
            if changed != 1:
                raise RuntimeError("Account changed during voiceprint update.")
            if any(sample['is_security_phrase'] and sample['audio'] in built.audios for sample in samples):
                db.execute('UPDATE accounts SET security_phrase_saved=1 WHERE id=?', (uid,))
            if built.handle != account["voiceprint"]:
                db.execute("INSERT OR IGNORE INTO voiceprint_versions VALUES (?,?,?,?,?)",
                    (uid, "main", "", account["audios"], time.time()))
                db.execute("INSERT OR IGNORE INTO voiceprint_versions VALUES (?,?,?,?,?)",
                    (uid, built.handle, account["voiceprint"], json.dumps(built.audios), time.time()))
            db.execute("""INSERT INTO voiceprint_learning_state VALUES (?,?)
                ON CONFLICT(account) DO UPDATE SET last_sample=excluded.last_sample""", (uid, cutoff))
        self.record(call, "voiceprint_updated" if built.added else "voiceprint_update_skipped",
                    additional_recordings=built.added)

    async def resume(self):
        with self.accounts.connect() as db:
            pending = db.execute("""SELECT s.account FROM voiceprint_samples s
                LEFT JOIN voiceprint_learning_state l ON l.account=s.account
                GROUP BY s.account HAVING MAX(s.id)>COALESCE(MAX(l.last_sample),0)""").fetchall()
        for row in pending:
            self.schedule(row[0], "startup")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
