# VeriVoice AI

VeriVoice is a local voice-verification demo for English, Spanish, Hindi, and Russian.
Create an account, enroll a voice for each language you want to use, and make a
browser microphone call to an imaginary Satoshi Bank customer-service agent.
Hiya checks voice identity, synthesis, and replay; Soniox measures language
consistency; Gemini provides the spoken conversation. Deepgram transcribes enrollment recordings.

This is a browser-call prototype, not a telephone service or production authenticator.
All banking services, ByteCoin balances, and VeriVoice token purchases are fictional.

## What you need

- **Python 3.13** is the tested and recommended version. The code and dependencies
  require Python 3.10 or newer; other Python versions have not been validated here.
- A microphone and a current desktop Chrome or Edge browser. These are the recommended
  browsers for the demo's 16 kHz microphone capture. Headphones help avoid speaker feedback.
- Internet access and working API credentials/credits for **Gemini, Hiya Audio Intelligence,
  Deepgram, and Soniox**. A Gemini consumer subscription does not by itself configure API access.
- An existing Hiya owner and space accessible to your API key. Local registration creates
  identities and voiceprints; it does **not** provision your Hiya owner, space, or billing.
- Git to clone the repository, or download and extract its ZIP. Node.js is optional and
  used only to run the browser regression tests. No npm install or frontend build is needed.

## 1. Download the code

```sh
git clone https://github.com/em3ndo/verivoice-ai.git
cd verivoice-ai
```

Alternatively, download the ZIP from the [repository](https://github.com/em3ndo/verivoice-ai),
extract it, and open a terminal in the extracted project folder. Every command below
must run from that folder, where `requirements.txt` and `.env.example` are located.
A fresh download includes no API keys, accounts, recordings, or preinstalled environment.

## 2. Install dependencies

Use **one** of these installation methods.

### macOS or Linux

Check that `python3 --version` shows your intended Python version. Use `python3.13`
in the first command instead if Python 3.13 is installed under that name.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
cp .env.example .env
```

### Windows PowerShell

Install Python 3.13 first, including the Python launcher, then run:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

Activation is unnecessary with these commands. For later commands shown as `python`,
use `.\.venv\Scripts\python.exe` on Windows unless you activated the environment yourself.

### Conda alternative

From the project folder:

```sh
conda env create -f environment.yml
conda activate verivoiceai
```

Then copy `.env.example` to `.env` using your shell's command above. `environment.yml`
installs Python 3.13 and the same `requirements.txt`; do not create another virtual environment.
If the Conda environment already exists, activate it and run
`python -m pip install -r requirements.txt` to update its Python dependencies.

**Do not overwrite an existing `.env` when updating an installation.** Add missing
settings from `.env.example` to your existing file instead.

## 3. Configure the providers

Open `.env` in a text editor and replace the empty values with your own credentials.
The file is read automatically; no export command is necessary. Existing process
or shell environment variables take precedence over `.env`.

| Setting | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key for enrollment text and live audio conversation |
| `DEEPGRAM_API_KEY` | Recorded enrollment transcription |
| `SONIOX_API_KEY` | Live-call transcription and token language labels |
| `HIYA_API_KEY` | Hiya Audio Intelligence uploads, verifications, identities, and voiceprints |
| `HIYA_REGION` | `us` or `eu`, matching your actual Hiya resources |
| `HIYA_OWNER` | Your existing Hiya user/organization **handle**, not your VeriVoice email |
| `HIYA_SPACE` | Your existing accessible space handle; the template uses `main` |
| `GEMINI_MODEL` | Audio-capable Gemini **Live** model; default `gemini-3.8-live` |
| `GEMINI_ENROLLMENT_MODEL` | Text/structured-output model; default `gemini-3.8-flash` |
| `SONIOX_MODEL` | Soniox real-time transcription model; default `stt-rt-v5` |

Provider setup references:

- [Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key) and
  [Live API](https://ai.google.dev/gemini-api/docs/live). Enrollment and conversation
  use different models; choose models available to your API project if the defaults fail.
- [Deepgram console](https://console.deepgram.com/) for an API key and usage balance.
- [Soniox console](https://console.soniox.com/) for an API key and organization balance.
- [Hiya Audio Intelligence guide](https://developer.hiya.com/docs/guides/voice-protection/introduction)
  for API access and resource setup. Your key must permit the relevant operations
  on the configured owner, region, and space. Space and region remain associated
  with enrolled voice profiles; changing them later can make those profiles unusable.

`HIYA_IDENTITY` and `HIYA_VOICEPRINT` are optional settings for standalone provider
adapters. Leave them blank for the website: each language enrollment creates its own
identity and initial `main` voiceprint. `HIYA_AUTHENTICITY_MODEL=digital` controls
standalone authenticity/enrollment screening; changing it to `phone` does not add
telephone support. Browser identity verification uses the enrolled digital voiceprint.

Keep `.env` and the private `data/` directory out of source control. Their exclusions
are already in `.gitignore`. Each provider may charge for its own requests.

## 4. Check configuration and start

With your environment active (or using the Windows executable above):

```sh
python -m verivoice.check_setup
python -m verivoice.app
```

The configuration check makes **no network requests** and prints no API key values.
It checks presence of required settings and the Hiya region; it cannot validate keys,
provider balances, model availability, or Hiya permissions. Successful local setup
is not proof that a live call will succeed.

Open **http://127.0.0.1:8000/**. The server listens only on your computer, using one
worker. Grant microphone access and allow the call popup. Use the same hostname
throughout the session; `localhost` and `127.0.0.1` have separate browser cookies.
The server may start silently because routine logs are disabled; leave the terminal
running and open the URL above. Stop the server with **Ctrl+C**. Restart after changing `.env` or Python code, and
refresh the page after frontend changes.

For later launches on macOS/Linux, reactivate the environment with
`source .venv/bin/activate`, or run `.venv/bin/python -m verivoice.app` directly.
The site cannot be opened simply by double-clicking its HTML file.

## 5. Enroll and make your first call

1. Choose **Create an account** from the sign-in page. Enter email, a password of
   at least 12 characters, phone number/country code, language, and consent.
2. Record each of the five displayed sentences naturally for 5–20 seconds.
   Each recording must pass language, phrase, and synthetic-speech checks.
3. Click **Finish voice enrollment**. Wait for confirmation that enrollment succeeded.
4. Click **📞 Call Satoshi Bank customer service**. Choose your actual microphone
   in the call window, and repeat the spoken verification phrase in the selected language.
   In English: **“With VeriVoice, my voice is my password.”**
5. After initial verification succeeds, Gemini begins the fictional banking conversation.
   Hang up with the call window's button. The volume slider controls only Gemini playback.

Returning users need **email or phone number plus password**. Phone sign-in accepts
US national numbers; non-US numbers must include the country code, such as `+44 …`.
Registration validates phone formatting but does not independently verify phone or email ownership.

Use **Want to register a new voice? Click here.** to enroll another supported language.
Only languages without an existing profile are offered. Use the voice-language selector
for completed profiles or to resume a pending enrollment. All profiles share one account
and phone number; each has an independent Hiya identity and voiceprint. Calls select
that profile using the stored phone number and the session's selected language.

## Scores and adaptive enrollment

`C = min(C_A, C_H, C_L)`. Each component uses EMA weight 0.5 on each new result.
Identity uses raw Hiya A; humanness uses `H = min(S + 0.5(1 - S), R)`; language uses
Soniox's proportion of qualifying tokens matching the selected language. VeriVoice and
ByteCoin brand variants are language-neutral. Scores are prototype indices, not calibrated
probabilities or guarantees of identity.

Initial verification passes at **C ≥ 0.70**. A continuing score strictly between 0.70
and 0.80 triggers periodic warnings; **C < 0.70 ends the call**. Exactly 0.70 passes
initial verification and causes neither warning nor removal. Updates follow completed
speech-window requests, not a fixed five updates per second.

Adaptive enrollment is **enabled automatically**: recordings qualify only when raw
A, S, R, and instantaneous L are each at least 0.70 and Hiya's voice-duration requirement
is met. A replacement voiceprint is computed after the call; original enrollment clips
are preserved. At most one qualifying initial-phrase recording is learned per language
profile. This expands reference audio rather than retraining Hiya's underlying model.
There is currently no settings switch to disable learning. For spoof tests, use a separate
test account/phone number or deliberately disable learning in the code before testing;
an incorrectly accepted recording could otherwise enter your trusted voiceprint.

See [implementation details and limitations](docs/architecture.md) for the complete
policy, data flow, provider adapters, and retention behavior.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `ModuleNotFoundError` | Run from the project root and use the environment where you installed requirements. |
| Missing configuration | Check `.env` is named exactly that, not `.env.txt`; restart and check environment overrides. |
| Account creation or Finish fails | Check Gemini text-model access, Deepgram balance, and Hiya owner/space/region permissions. Saved progress remains available to retry. |
| Call reports provider unavailable | Verify all three call providers: Gemini Live access/quota, Hiya verifications, and Soniox balance. A configured key is not proof of available credit. |
| Soniox balance exhausted | Fund the organization associated with the configured Soniox key, then retry a fresh call. |
| No microphone audio | Grant microphone permission, select the right input device, and look for movement on the level meter. Speaker selection does not select a microphone. |
| Speech arrives but no check starts | Speak for at least 1.5 seconds above the quiet floor and pause; four-second windows also trigger checks. Very short responses may not be evaluated. |
| Gemini audio is silent / no call window | Allow popups, check call and system volume, and start from the call button. Try Chrome or Edge. |
| Genuine voice is rejected | Inspect separate identity, authenticity, and language scores. Try consistent microphone conditions; do not assume every rejection proves synthesis. |
| Address already in use | Stop the previous local server before starting a new one. |

`data/call-diagnostics.json` contains recent score/timing/error-type events, not audio,
transcripts, passwords, API keys, phone numbers, or account IDs. It records only a bounded
recent history; restarting resets the in-memory history. Some provider errors retain only
the exception type, so a precise root cause may remain unavailable.

## Verify the installation

With dependencies installed, these tests use simulated providers and do not require
real keys or spend provider credits:

```sh
python -m pip check
python -m unittest discover -s tests -v
```

With Node.js 18 or newer installed, the optional browser regression checks are:

```sh
node --test tests/account_browser.test.cjs tests/call_browser.test.cjs tests/microphone_worklet.test.cjs
```

These checks cannot prove provider access or biometric accuracy. Complete a real
five-recording enrollment and a genuine-voice call to validate your own provider setup.

## Updates and local data

Back up your private `data/` directory and preserve `.env` before updating. Stop the server,
update the code, reinstall `requirements.txt` in the same environment, and restart. Local
schema additions preserve existing accounts and profiles. The database is created
under `data/accounts.sqlite3` on first startup; no separate database server is required.
Do not delete it to resolve an API outage: doing so loses local accounts and references
but does not delete corresponding recordings or voiceprints from Hiya.
