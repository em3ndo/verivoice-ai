"""Call policy. Update once per fresh provider result, never by resampling a score."""
from dataclasses import dataclass
from .providers.hiya_client import optional_score

WARNING = "Your voice sounds suspicious, try speaking clearly and be yourself."
REMOVAL = "You have been removed from this call because VeriVoice is not confident that you are this phone number's real owner"
DECLINE = "Sorry, to continue this call you must enroll your phone number with a VeriVoice AI account. Thank you, and have a good day."
PASS_THRESHOLD = .8
REMOVAL_THRESHOLD = .7

@dataclass
class Confidence:
    ca: float | None = None
    ch: float | None = None
    cl: float | None = None
    l: float | None = None
    dominant_other: str | None = None
    dominant_other_name: str | None = None
    registered_language_name: str | None = None
    last_warning: float | None = None
    revoked: bool = False
    a: float | None = None
    s: float | None = None
    s_adjusted: float | None = None
    r: float | None = None
    h: float | None = None

    @property
    def c(self):
        return None if any(value is None for value in (self.ca, self.ch, self.cl)) else min(self.ch, self.ca, self.cl)

    def update(self, instantaneous, synthesis, replay, language=None, *, dominant_other=None,
               dominant_other_name=None, registered_language_name=None):
        score = optional_score(instantaneous)
        s, r = optional_score(synthesis), optional_score(replay)
        l = optional_score(language)
        if score is None or s is None or r is None or l is None:
            return self.snapshot()
        # Validate the complete observation before changing either EMA.
        self.a, self.s, self.r = score, s, r
        self.s_adjusted = s + .5 * (1 - s)
        self.h = min(self.s_adjusted, r)
        self.ca = score if self.ca is None else .5 * score + .5 * self.ca
        self.ch = self.h if self.ch is None else .5 * self.h + .5 * self.ch
        self.l = l
        self.cl = l if self.cl is None else .5 * l + .5 * self.cl
        self.dominant_other = dominant_other
        self.dominant_other_name = dominant_other_name
        self.registered_language_name = registered_language_name
        return self.snapshot()

    def snapshot(self):
        c = self.c
        action = "pending" if c is None or c == REMOVAL_THRESHOLD else "revoke" if c < REMOVAL_THRESHOLD else "warn" if c < PASS_THRESHOLD else "allow"
        return {"type": "confidence", "c": c, "ca": self.ca, "ch": self.ch, "cl": self.cl,
                "a": self.a, "s": self.s, "s_adjusted": self.s_adjusted,
                "r": self.r, "h": self.h, "l": self.l, "action": action,
                "dominant_other": self.dominant_other, "dominant_other_name": self.dominant_other_name}

    def language_warning(self):
        if self.l is None or self.a is None or self.h is None or not self.dominant_other:
            return None
        if self.l < .7 and self.l <= min(self.a, self.h):
            detected = self.dominant_other_name or self.dominant_other
            expected = self.registered_language_name or 'your registered language'
            return (f"{detected} was detected and is not registered for this account. "
                    f"This account is enrolled for {expected}; enroll a separate identity for "
                    f"{detected} if you would also like to speak {detected}.")
        return None

    def notification(self, now):
        if self.revoked or self.c is None:
            return None
        if self.c < REMOVAL_THRESHOLD:
            self.revoked = True
            language = self.language_warning()
            message = "Your call ended because language consistency was too low. " + language if language and self.cl <= min(self.ca, self.ch) else REMOVAL
            return {"type": "removed", "message": message, "close_after": 4}
        language = self.language_warning()
        if REMOVAL_THRESHOLD < self.c < PASS_THRESHOLD or language:
            if self.last_warning is None or now - self.last_warning >= 10:
                self.last_warning = now
                return {"type": "warning", "message": language or WARNING}
        else:
            self.last_warning = None
        return None
