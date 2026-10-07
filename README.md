# VeriVoice AI

Python account enrollment and voice verification integrations for English (`en`),
Spanish (`es`), Hindi (`hi`) and Russian (`ru`).

## Run the enrollment website

A local `.venv` is installed. From this project folder:

```sh
.venv/bin/python -m verivoice.app
```

Open http://127.0.0.1:8000 in Chrome, Edge or another browser supporting microphone
capture at 16 kHz. The server binds only to your computer. Stop with Ctrl+C.
If the server was already running, restart it after editing `.env` or Python files.

Alternatively, use the existing Conda environment:

```sh
conda activate verivoiceai
python -m pip install -r requirements.txt
python -m verivoice.app
```

For a fresh virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Required configuration

Fill the ignored `.env` locally, using `.env.example` as the template:

- `GEMINI_API_KEY`, `DEEPGRAM_API_KEY`, `HIYA_API_KEY`.
- `HIYA_REGION=us` (or your actual region), `HIYA_OWNER` (user/organization handle),
  and `HIYA_SPACE=main` (your existing space).
- `GEMINI_ENROLLMENT_MODEL=gemini-3.8-flash` is the text model for enrollment.
  `GEMINI_MODEL` remains separate for the existing Live speaker adapter.

The checkout did not contain `.env`; a blank private copy has been created.
No old credentials are assumed valid. Keys stay exclusively on the backend.
Environment variables override `.env`. The website lists missing configuration
without exposing values. Your key must allow Hiya audio uploads, authenticity
verification, identities and voiceprints; credentials alone do not prove access.

`HIYA_IDENTITY` and `HIYA_VOICEPRINT` are optional for enrollment. Each account
receives its own generated identity and a `main` voiceprint, persisted in SQLite.
Those two environment fields remain useful for standalone matching examples.

```sh
.venv/bin/python -m verivoice.check_setup
```

## Enrollment flow

1. Enter an email, a password of 12–128 characters, a country calling code and
   national phone number, select a language and consent to the displayed data flow.
   Numbers are validated and stored in E.164 format (for example, `+12025550123`),
   with one account per number. Email and phone ownership are not verified.
2. Gemini creates five distinct sentences in the selected language, targeting
   8–15 seconds each and varied sounds, rhythm and sentence structures. These are
   generated prompts, not a validated phonetic coverage corpus.
3. Record each sentence naturally. The browser captures mono PCM16 WAV at 16 kHz;
   the server requires 5–20 seconds per clip and a bounded upload size.
4. Deepgram's recorded-audio endpoint (`nova-3-general`, `detect_language=true`,
   `mip_opt_out=true`) returns the transcript and dominant language. Unlike live
   conversation, enrollment does not need Flux streaming. The existing Flux
   adapter remains available for future live calls.
5. The detected language must match, Gemini must accept the words as a faithful
   reading, and Hiya must return a performed non-synthetic score of at least 0.5.
   Missing results and provider failures never count as acceptance. This threshold
   is a prototype choice requiring evaluation, not proof of liveness.
6. After five accepted clips, press **Finish voice enrollment**. The backend creates
   the identity and `digital/v1` voiceprint with `minAudios=5`, adds the clips, computes
   the voiceprint, and reads it back. Success requires Hiya state `computed` and five
   attached audios. Uploading alone never completes enrollment.

Progress persists after each accepted clip. Sign in to resume. A failed finalization
can be retried; the backend inspects existing Hiya resources before adding clips.
Recordings rejected after upload may remain in Hiya until its configured retention
expires. An interrupted upload may also leave an unattached audio resource.
If a stored audio expires before you finish, an operator must repair/restart that
pending enrollment; automatic cleanup/re-enrollment is not implemented yet.

## Browser calls

After signing in, click **call verivoice ai**. A new tab opens with Hang up and a
voice-volume slider. Allow microphone access. The call-link click activates audio playback before the new tab opens; the
volume slider changes only playback volume. Volume starts at 30%, is limited
to an 80% gain, and passes through a compressor. Headphone/device volume still
determines actual loudness.

The call window also shows the selected microphone and an input-level meter.
Use the microphone selector to switch between built-in and headset microphones.
The initial selection is **System default microphone**. Device-change notifications
reconnect capture when that default input changes; choosing a specific microphone
keeps it selected until it disconnects. A disconnected input falls back to the
system default. Speaker output selection is separate from microphone selection.
Capture uses the strongest input channel if a device exposes multiple channels.
Capture starts a window at audible input, retains 200 ms of leading audio, and
submits after a 600 ms pause or four seconds of continuous audio. At least 1.5
seconds must exceed the quiet floor (0.0008 RMS). The filter does not estimate
background noise from the same response: that could classify a quiet voice as
noise. Hiya determines whether the submitted audio is speech. Recordings are
not amplified, and identity thresholds are unchanged.
The call confirms microphone receipt, Hiya submission, and a below-threshold
result that requires another attempt. Opening playback completion follows the
audio clock, so delayed browser completion callbacks cannot keep input blocked.

The browser supplies the signed-in account's stored E.164 phone number. The backend
uses `Accounts.enrolled_phone` to select the completed enrollment and digital
voiceprint, and checks that this account belongs to the signed-in caller. The phone
lookup can be reused by a future trusted telephony transport. Unknown or unfinished
enrollments receive the enrollment-required Gemini farewell; the call ends after
the browser confirms farewell audio playback. One call per account is permitted.

Microphone PCM16 at 16 kHz is streamed to Gemini Live for conversation. Independently,
fresh response windows (up to four seconds) with at least 1.5 seconds of non-quiet audio are uploaded
to Hiya and compared against the selected identity/voiceprint. Hiya may retain these
call clips under the space's audio-retention policy. No raw audio is saved locally.
Only one identity request runs at a time; if it falls behind, the latest waiting
window replaces older waiting windows to bound memory and avoid a growing backlog.

`A` is Hiya's identity-match score, distinct from its non-synthetic score. The first
valid result initializes `C_A`; each fresh result applies
`C_A = 0.5 * A + 0.5 * previous_C_A`. Hiya returns synthesis `S` and replay `R`
for the same window. Synthesis is adjusted with `S_adjusted = S + 0.5 * (1 - S)`,
mapping `[0, 1]` to `[0.5, 1]`. Replay remains unchanged. `H = min(S_adjusted, R)`
and `C_H = 0.5 * H + 0.5 * previous_C_H`;
the first complete observation initializes each EMA from its instantaneous score.
Soniox labels finalized lexical tokens in the same audio window. `L` is the fraction
matching the registered language among tokens with transcription confidence >= 0.80;
all other languages and untagged qualifying tokens count against it. Low-confidence,
missing-confidence, and invalid-confidence tokens are excluded from both numerator
and denominator. No qualifying tokens means unavailable, not a passing L.
Punctuation, control markers, provisional tokens and translations are excluded.
`C_L = 0.5 * L + 0.5 * previous_C_L`, initialized from the first measured L, and
`C = min(C_H, C_A, C_L)`. Updates arrive at the cadence of completed parallel Hiya and Soniox requests,
not five times per second. Quiet audio does not update the EMA. Before the first
complete identity/synthesis/replay/language observation, confidence is unavailable. A missing/invalid identity, synthesis, replay, or language score or provider failure ends
the call with an availability message rather than treating missing evidence as a pass.

Initial verification passes at `C >= 0.70`. The ongoing warning policy still uses
0.80: strictly `0.7 < C < 0.8` displays the requested warning immediately
and every ten seconds while that condition holds. `C < 0.7` stops media immediately,
shows the removal message and closes the tab after four seconds. Exactly `C = 0.7`
passes initial verification and triggers neither warning nor removal.
The initial verification prompt is spoken before collecting the caller response.
Microphone audio is withheld from Gemini until the combined confidence reaches 0.70, after
which Gemini asks what the caller wants to talk about. Warning-range callers can
keep trying; removal-range callers are disconnected. This checks voice identity,
not whether the exact phrase was repeated. Hang-up, disconnect, logout/session
expiry, and provider failure cancel call processing. Qualified voiceprint samples
remain available for the post-call update described below.

`data/call-diagnostics.json` retains the latest 100 local call events: audio
receipt/duration, opening completion, Hiya submission/results, and call outcome.
It contains no audio, transcripts, credentials, phone numbers, or account IDs.
History resets when the server restarts and the next event is recorded.

### Adaptive voiceprints

For each completed Hiya verification, a recording qualifies for adaptive enrollment
only when **raw identity, synthesis, replay, and instantaneous L are each >= 0.70**.
L is measured by Soniox on that same recording; missing language evidence cannot qualify. Neither
the synthesis adjustment nor any EMA is used for this decision.
The call still requires overall confidence >= 0.70 to open the conversation.
Each account (and its uniquely associated phone number) has a persistent
`security_phrase_saved` flag, initially false. At most one qualifying recording
from the initial security-phrase stage is queued per account. The flag becomes
true only when a successfully computed, activated voiceprint includes that
recording; failed updates leave it false and retry the same pending sample.
Once true, subsequent security-phrase recordings are excluded from adaptation.
Conversation recordings remain eligible. Capture-stage tags stay attached to
queued audio, and the phrase stage ends only after verification and a speech
pause, so delayed results or four-second chunk boundaries cannot relabel phrase
audio as conversation. This identifies the call stage, not the spoken words.
The existing Hiya audio handle is reused, with no duplicate upload. Provider-reported
voice duration must also satisfy digital/v1's per-recording requirement (0.9 seconds
when `minAudios=5`). Missing scores or duration never qualify.

Hiya computed voiceprints are immutable, so this updates an account's profile by
building a new version after the call ends. Each version preserves the original
five recordings and adds the newest eligible recordings that fit the model's
120-second total-duration limit. All eligible sample references/scores are retained
in `voiceprint_samples` in the local account database; they do not change the five
enrollment steps. Audio availability still follows Hiya's retention policy.

The active voiceprint changes atomically only after Hiya confirms the replacement
is computed with the expected recordings. Ongoing calls keep using the version
they started with, while future phone-number lookups use the new version. Old
versions remain in Hiya and `voiceprint_versions` for recovery. The original `main`
voiceprint is preserved. Failed/unfinished updates keep the active version usable
and retry on the next call or server startup; deterministic version names let a
retry resume a partial build. Learning failures do not interrupt conversations.

This expands the enrolled reference audio; it does not retrain Hiya's general
model or guarantee accuracy gains. A false match admitted for adaptation can
contaminate the profile, so evaluate the chosen 0.70 cutoff on genuine and impostor
speech. Hiya documents [immutable computation](https://developer.hiya.com/docs/guides/voice-protection/results/compute-a-voiceprint)
and [model requirements](https://developer.hiya.com/docs/audio-intel/model-index/voiceprint-models).

Gemini receives call audio; this differs from the text-only enrollment workflow.
Browser calls use the synthesis and replay fields returned by the same Hiya
identity verification request; they do not open a separate deepfake stream.
Soniox uses a persistent real-time WebSocket with manual finalization per verification window.
Its token confidence estimates transcription accuracy, so VeriVoice calculates its own
language ratio rather than treating transcription confidence as L. Language identification
can follow sentence context; it is not a precise linguistic classification of every borrowed word.
Deepgram remains in the enrollment workflow. Configure SONIOX_API_KEY and SONIOX_MODEL
(default stt-rt-v5) on the server. No API key is sent to the browser.

When raw L < 0.70 and is the lowest raw component, the warning names the uniquely
dominant unexpected language. Tied language counts use the ordinary warning.
Removal still depends on combined EMA C < 0.70; language gets no separate removal cutoff.
Diagnostics retain token counts, dominant language, L, and provider/aggregation timings,
without transcript text. Full windows are evaluated; no random sampling is used.

## Storage and limitations

- VeriVoice stores email, phone number and selected country, salted scrypt password hashes, selected language, generated
  phrases, Hiya references and hashed session tokens in `data/accounts.sqlite3`.
  The private directory is Git-ignored; raw recordings and transcripts are not
  written to this database or local audio files. Sessions expire after 12 hours.
- Hiya stores uploaded audio (default 90 days, configurable by space) and the computed
  biometric voiceprint. Audio retention and voiceprint persistence are distinct.
- Deepgram receives recordings with model-improvement opt-out. Its docs state these
  requests retain data only for the duration needed to process them.
- During enrollment, Gemini receives the language, phrases and transcripts, never audio, account email,
  password or Hiya IDs. Free-tier input/output can be used for improvement and
  human review. Unexpected personal speech may appear in a transcript; only read
  the displayed nonpersonal sentences.

All account and verification logic is Python. Small HTML/CSS/JavaScript files handle
browser display and microphone access. This is a local, single-worker demo, not a
public authentication service. It has no email confirmation, password recovery,
third-party relying-party integration, or production identity proofing.
One enrollment does not automatically grant access to companies or government systems.
Voiceprints are scoped to the configured Hiya space; future relying parties would
need authorized integration through VeriVoice.

Hiya synthetic detection does not establish that speech is live or defeat replay.
Five phrases are not proof of a person's legal identity, nor a calibrated guarantee
that all clips come from the same speaker. Evaluate enrollment quality and genuine
Hindi/Russian voice matching; Hiya documents substantial English/Spanish training data.

## Modules and verification

- `verivoice/app.py`: local FastAPI server, session cookies and bounded requests.
- `verivoice/enrollment.py`: SQLite accounts, passwords and acceptance policy.
- `verivoice/providers/gemini_enrollment_api.py`: text-only phrase generation/checking.
- `verivoice/providers/deepgram_api.py`: recorded transcription and existing Flux streaming.
- `verivoice/providers/hiya_enrollment_api.py`: audio screening and voiceprint computation.
- `verivoice/providers/hiya_identity_api.py`: future identity matching against enrolled profiles.
- `verivoice/providers/hiya_synthesis_api.py`: uploaded/streaming synthetic detection.
- `verivoice/providers/enrollment_api.py`: composes the three enrollment adapters.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use simulated provider responses; they cover account isolation, password/session
handling, language/phrase/synthesis rejection, duplicate recordings, unfinished
voiceprints, retry behavior and provider request formats. Live enrollment must still
be validated with configured credentials and actual user speech. No account or
voiceprint was created in Hiya during automated testing.

## Provider references

- [Hiya voiceprint computation](https://developer.hiya.com/docs/guides/voice-protection/results/compute-a-voiceprint)
- [Hiya create endpoint](https://developer.hiya.com/docs/audio-intel/endpoints/voiceprints-create)
- [Hiya model requirements](https://developer.hiya.com/docs/audio-intel/model-index/voiceprint-models)
- [Hiya audio retention](https://developer.hiya.com/docs/guides/voice-protection/use-cases/identity-verification/identity-best-practices)
- [Deepgram language detection](https://developers.deepgram.com/docs/language-detection)
- [Deepgram opt-out](https://developers.deepgram.com/docs/the-deepgram-model-improvement-partnership-program)
- [Gemini terms](https://ai.google.dev/gemini-api/terms)

# Gemini conversational speaker

`verivoice/providers/gemini_speaker_api.py` uses Google's `google-genai` SDK
and Gemini Live. Configure the key in the ignored backend `.env`; never send it to a
browser. `GEMINI_MODEL` defaults to `gemini-3.8-live` and can be changed there.

The speaker accepts microphone audio **or** finalized Deepgram transcripts,
plus structured backend confidence reports with complete relevant provider
evidence. Using transcripts avoids sending the same microphone audio to Gemini
for interpretation as well as Deepgram. Gemini produces speech and its text
transcription. It does not calculate authoritative scores or enforce access.

```python
from verivoice.providers.gemini_speaker_api import GeminiSpeakerAPI, ConfidenceReport

async def explain_report(report: ConfidenceReport):
    async with GeminiSpeakerAPI().connect() as speaker:
        await speaker.report(report, announce=True)
        async for event in speaker.receive_turn():
            content = event.server_content
            if content is None:
                continue
            if content.interrupted:
                # Clear the browser's queued audio playback here.
                continue
            if content.output_transcription:
                print(content.output_transcription.text)
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    if part.inline_data:
                        # Forward part.inline_data.data to the browser player:
                        # raw PCM16 little-endian mono at 24 kHz.
                        pass
```

Construct `ConfidenceReport` from the **backend's** calculated confidence,
three component scores, action (`allow`, `warn`, `challenge`, `revoke`, or
`pending`), reasons, optional challenge, and `evidence` dictionary containing
Hiya/Deepgram raw events or parsed result dataclasses. Missing results stay
`None`. Do not include API credentials or HTTP headers in evidence. Hiya's
non-synthetic result is not proof against a recording/replay attack.

For an ongoing conversation, keep the connection open. Run input sending and
receiving concurrently; call `receive_turn()` again after each completed turn.
Use `send_text(transcript)` for finalized Deepgram turns, or `send_audio(pcm)`
for live mono PCM16 at 16 kHz and `end_audio()` when capture pauses.
`report(report)` updates context silently; `announce=True` interrupts generation
and requests a spoken explanation. Do not announce every score update.
The browser routing, confidence engine, and actual access controls still need
to be connected. Prompt instructions help explanation fidelity; security
systems must consume backend reports directly, never Gemini's wording.

Reference: [Google Live API capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities).


### Multiple voice languages and sign-in

The initial page provides separate sign-in and account-creation forms. Sign-in needs
only an email or phone number plus password. Phone sign-in accepts formatted numbers;
non-US numbers must include the international country code. Existing accounts and
voiceprints are preserved by an additive SQLite migration.

Each account owns one unique email and phone number, with up to one voice enrollment
per supported language (English, Spanish, Hindi, Russian). The original enrollment
remains in accounts; additional enrollments live in voice_profiles with independent
Hiya identity IDs, recordings, voiceprints, and security-phrase adaptation flags.
The signed-in page offers only languages without an existing profile for new enrollment.
Pending profiles can be resumed in the voice-language selector. Existing profiles can
be selected for calls, which route by the account phone number and selected language.
Selection belongs to the signed-in session; no profile IDs or Hiya references are
returned in enrollment status or printed in the UI. One call per account is permitted.

After verification, Gemini role-plays as a pretend banker at the imaginary firm
Satoshi Bank. ByteCoin deposits, withdrawals, meme investments and VeriVoice token
purchases are fictional dialogue only, with no real payments, transactions or credit changes.


Spoken call scripts (verification opening, pretend-bank introduction, and enrollment-required
farewell) are translated for English, Spanish, Hindi, and Russian in call_prompts.py.
Gemini receives the selected enrollment language explicitly and is instructed to keep
using it throughout verification and the fictional banking conversation. English's
original verification opening remains unchanged. Brand names remain untranslated.


VeriVoice and ByteCoin/ByteCoins brand tokens are language-neutral and excluded from both numerator and
denominator of L, including bounded recognized aliases and split subword tokens.
Other foreign words still count against the registered language, and brand-only
recordings provide no language evidence. Diagnostics record excluded brand-token
counts without storing transcript text. Arbitrary misrecognitions are not exempted.
