# VeriVoice AI

Python provider wiring for English (`en`), Hindi (`hi`), Spanish (`es`) and Russian (`ru`).

## Setup

Uses the Conda environment `verivoiceai` (Python 3.11 or newer).

```sh
conda activate verivoiceai
python -m pip install -r requirements.txt
python -m verivoice.check_setup
python -m unittest discover -s tests -v
```

To recreate this environment on another machine, run `conda env create -f environment.yml`.
For commands without activating it, use `conda run -n verivoiceai python ...`.
The workspace editor is configured for your local Conda interpreter at
`/opt/miniconda3/envs/verivoiceai/bin/python`; select `verivoiceai` manually if
your editor already saved a different interpreter or Conda is installed elsewhere.

Credentials live in `.env`, ignored by Git. `.env.example` documents the settings.
Environment variables take precedence. Provider modules don't make calls on import.
Do not send these credentials to a browser or include `.env` in a deployment bundle.

## Separate model integrations

- `verivoice/providers/deepgram_api.py`: Flux Multilingual streaming transcription and
  per-turn language observations. Uses `/v2/listen`, `flux-general-multi`, and repeated
  `language_hint=en&language_hint=hi&language_hint=es&language_hint=ru` query parameters.
- `verivoice/providers/hiya_identity_api.py`: match uploaded speech against an existing
  enrolled identity and voiceprint. Enrollment is an explicit stub.
- `verivoice/providers/hiya_synthesis_api.py`: synthetic-voice verification, both uploaded
  audio and streaming media. A high Hiya synthesis score means **non-synthetic**.
- `verivoice/providers/hiya_client.py`: shared authentication, media upload and HTTP transport.

The adapters use the providers' documented HTTP/WebSocket APIs directly through `httpx`
and `websockets`. No heavyweight models are downloaded, and no training is required.

## Flux language detection

Flux **Multilingual** reports detected languages through `TurnInfo.languages`.
`flux-general-en` is English-only. The supplied shell example used English-only Nova-3
on `/v1/listen`, so it isn't a Flux multilingual example. Hints bias recognition, rather
than implementing a strict allowlist. Evaluate `EndOfTurn` once per `turn_index`; don't
count interim updates as independent observations. Missing language information remains
unknown. Word confidence and end-of-turn confidence are not language confidence.

Usage in a Python backend:

```python
from verivoice.providers.deepgram_api import DeepgramAPI, LanguageObservation

async def analyze_microphone(pcm_chunks):
    # pcm_chunks: async iterable of mono, signed 16-bit little-endian PCM bytes at 16 kHz.
    async for event in DeepgramAPI().stream(pcm_chunks):
        observation = LanguageObservation.from_message(event)
        if observation and observation.event == "EndOfTurn":
            print(observation.transcript, observation.languages)
```

Start with 80 ms PCM chunks (2,560 bytes at 16 kHz). Browser capture must convert audio
into this format; passing compressed WebM bytes or WAV headers as raw PCM is incorrect.
Require sustained language evidence in a future policy instead of penalizing one short,
ambiguous utterance. No confidence-scoring engine is implemented yet.

## Hiya configuration and use

Fill `HIYA_REGION` with the account region (`us` or `eu`) and set `HIYA_OWNER` and
`HIYA_SPACE` from the Audio Intelligence console. The supplied key must be a valid
Audio Intelligence bearer token, with permissions for the desired endpoints; this is
not established by its name or format. Speaker matching additionally requires
`HIYA_IDENTITY` and `HIYA_VOICEPRINT` for an enrolled speaker. Values are placeholders
until account access and enrollment are confirmed.

```python
from verivoice.providers.hiya_client import HiyaClient
from verivoice.providers.hiya_identity_api import HiyaIdentityAPI
from verivoice.providers.hiya_synthesis_api import HiyaSynthesisAPI

with HiyaClient() as client:
    audio = client.upload_audio("sample.wav")  # Explicit upload to the selected Hiya space.
    synthesis = HiyaSynthesisAPI(client).verify(audio["handle"])
    identity = HiyaIdentityAPI(client).verify(audio["handle"])
    print(synthesis.non_synthetic_score, identity.match_score)
```

If a verification is not `performed`, its normalized score remains `None`; retrieve the
result using `get_result()` when the provider finishes processing. Errors are not scores.

For streaming synthetic detection, `HiyaSynthesisAPI.stream()` accepts an async iterable
of chunks from a decodable media stream, such as WAV. It does not assume Hiya can decode
bare headerless PCM. The browser audio encoder and fan-out to both providers are still to
be built. Choose `digital` for browser audio (16 kHz+), `phone` for telephone audio (8 kHz+).

Synthetic detection is not replay detection. The currently documented Hiya score is
non-synthetic confidence, not proof of freshness. Fresh randomized challenges and phrase
verification are future work. Evaluate genuine Hindi/Russian voices separately, because
Hiya's model descriptions emphasize English and Spanish training data.

## Next layers

The browser demo will need HTML/CSS plus JavaScript (or TypeScript) for microphone
permission, audio capture, playback and the dashboard. Python can handle provider calls,
scoring, state and access controls. Neither Twilio nor another phone platform is required
for a browser simulation. A single conversational speaker agent will be connected later.

## References

- [Flux API](https://developers.deepgram.com/reference/speech-to-text/listen-flux)
- [Flux multilingual output](https://deepgram.com/learn/flux-multilingual-technical-deep-dive)
- [Hiya authentication](https://developer.hiya.com/docs/guides/voice-protection/authentication/authenticate-using-api-keys)
- [Hiya identity verification](https://developer.hiya.com/docs/guides/voice-protection/results/perform-a-verification/identity)
- [Hiya authenticity streaming](https://developer.hiya.com/docs/audio-intel/endpoints/authenticity-verifications-streaming)
- [Hiya score interpretation](https://developer.hiya.com/docs/audio-intel/scores)

## Validation completed

- Seven offline tests passed: language configuration, unknown evidence, score direction,
  correct identity request, protected error messages, duplex streaming and timeout cleanup.
- Deepgram accepted a Flux Multilingual WebSocket connection with all four language hints.
- Hiya's US `/user` endpoint accepted the supplied token; US is configured locally.
- No voice recordings were uploaded. Language recognition, identity matching, synthesis
  detection accuracy and endpoint-specific permissions have not yet been tested with audio.
# Gemini conversational speaker

`verivoice/providers/gemini_speaker_api.py` uses Google's `google-genai` SDK
and Gemini Live. The key is in the ignored backend `.env`; never send it to a
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
