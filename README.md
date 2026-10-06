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

1. Enter an email, a password of 12–128 characters, select a language and consent
   to the displayed data flow. Email ownership is not verified in this prototype.
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

## Storage and limitations

- VeriVoice stores email, salted scrypt password hashes, selected language, generated
  phrases, Hiya references and hashed session tokens in `data/accounts.sqlite3`.
  The private directory is Git-ignored; raw recordings and transcripts are not
  written to this database or local audio files. Sessions expire after 12 hours.
- Hiya stores uploaded audio (default 90 days, configurable by space) and the computed
  biometric voiceprint. Audio retention and voiceprint persistence are distinct.
- Deepgram receives recordings with model-improvement opt-out. Its docs state these
  requests retain data only for the duration needed to process them.
- Gemini receives the language, phrases and transcripts, never audio, account email,
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
