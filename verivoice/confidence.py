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
    ch: float = 1.0
    cl: float = 1.0
    last_warning: float | None = None
    revoked: bool = False

    @property
    def c(self):
        return None if self.ca is None else min(self.ch, self.ca, self.cl)

    def update(self, instantaneous):
        score = optional_score(instantaneous)
        if score is not None:
            self.ca = score if self.ca is None else .5 * score + .5 * self.ca
        return self.snapshot()

    def snapshot(self):
        c = self.c
        action = "pending" if c is None or c == REMOVAL_THRESHOLD else "revoke" if c < REMOVAL_THRESHOLD else "warn" if c < PASS_THRESHOLD else "allow"
        return {"type": "confidence", "c": c, "ca": self.ca, "ch": self.ch, "cl": self.cl, "action": action}

    def notification(self, now):
        if self.revoked or self.c is None:
            return None
        if self.c < REMOVAL_THRESHOLD:
            self.revoked = True
            return {"type": "removed", "message": REMOVAL, "close_after": 4}
        if REMOVAL_THRESHOLD < self.c < PASS_THRESHOLD:
            if self.last_warning is None or now - self.last_warning >= 10:
                self.last_warning = now
                return {"type": "warning", "message": WARNING}
        else:
            self.last_warning = None
        return None
