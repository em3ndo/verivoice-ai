"""Collect response-aligned PCM windows without learning noise from caller speech."""
import math
import struct

RATE = 16000
FRAME_BYTES = 640  # 20 ms, mono PCM16.
QUIET_FLOOR = .0008
MIN_ACTIVE_SAMPLES = 24000  # 1.5 seconds; Hiya decides whether this is speech.


def audio_level(pcm):
    values = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    return math.sqrt(sum(value * value for value in values) / len(values)) / 32768 if values else 0


def enough_speech(pcm):
    # Only exclude quiet audio here. Estimating noise from this same response
    # raised the threshold above quieter voices and prevented any Hiya upload.
    active = sum(len(frame) // 2 for offset in range(0, len(pcm), FRAME_BYTES)
                 if audio_level(frame := pcm[offset:offset + FRAME_BYTES]) >= QUIET_FLOOR)
    return active >= MIN_ACTIVE_SAMPLES


class VoiceWindows:
    """Keep leading speech together; submit after a pause or four seconds of speech.

    The 200 ms preroll preserves quiet consonants. A 600 ms pause ends a
    response; continuous speech is still evaluated every four seconds.
    """
    def __init__(self):
        self.pending = bytearray()
        self.preroll = bytearray()
        self.buffer = bytearray()
        self.active_samples = 0
        self.quiet_samples = 0
        self.received_samples = 0
        self.total_active_samples = 0
        self.short_responses = 0
        self.at_pause = True

    def feed(self, pcm):
        self.pending.extend(pcm)
        windows = []
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            samples = len(frame) // 2
            active = audio_level(frame) >= QUIET_FLOOR
            self.received_samples += samples
            if active:
                self.total_active_samples += samples
                self.at_pause = False
            if not self.buffer:
                if not active:
                    self.quiet_samples += samples
                    if self.quiet_samples >= 9600:
                        self.at_pause = True
                    self.preroll.extend(frame)
                    del self.preroll[:-6400]
                    continue
                self.buffer.extend(self.preroll)
                self.preroll.clear()
            self.buffer.extend(frame)
            if active:
                self.active_samples += samples
                self.quiet_samples = 0
            else:
                self.quiet_samples += samples
            if self.quiet_samples >= 9600 or len(self.buffer) >= 128000:
                if self.quiet_samples >= 9600:
                    self.at_pause = True
                if self.active_samples >= MIN_ACTIVE_SAMPLES:
                    windows.append(bytes(self.buffer))
                elif self.active_samples:
                    self.short_responses += 1
                self.preroll = self.buffer[-6400:] if self.quiet_samples else bytearray()
                self.buffer = bytearray()
                self.active_samples = self.quiet_samples = 0
        return windows
