# Baresip-Speakerphone-MCP

Turn a USB speakerphone (tested: Jabra Speak 510) on a Linux box into a SIP extension that a voice assistant can
drive. [baresip](https://github.com/baresip/baresip) (v4.13.0, built from source) handles SIP and audio, and a small
Python controller adds two things:

- **Caller announcements while it rings:** a ring tone alternating with a spoken "Call from Alice on the work line"
  (or the number, read in digit groups), synthesised by any [Wyoming](https://github.com/OHF-Voice/wyoming) TTS
  server (Piper, Kokoro, ...). The line is taken from a caller-ID name prefix your PBX adds (e.g. `Work `).
- **Call control over MCP** (streamable HTTP at `/mcp`): `phone_status` ("who's calling?"), `phone_answer`,
  `phone_reject`, `phone_hangup`, `phone_mute`, `phone_volume`, `phone_call`, `phone_check_number`.

Pages and intercom calls carrying `Call-Info: answer-after=0` or `Alert-Info: info=alert-autoanswer` are answered
automatically and not announced.

## Dialling safely

The model never formats a phone number. `phone_call(number_or_contact, line=None, confirmed=False)` takes the
number as the user said it ("02 434 5678") or a contact name, normalises it deterministically
(`src/speakerphone/numbers.py`), and on the first call returns `needs_confirmation` with a spoken read-back
("Calling plus nine seven two, two, four three four, five six seven eight on the home line. Shall I dial?"). It
dials only when called again with `confirmed=true`. Numbers in `emergency_numbers` dial at once.

Number rules: locale `IL` (Israel) converts local numbers to E.164 and checks their length (mobile 05x and VoIP 07x
are 10 digits with the 0, landlines 02/03/04/08/09 are 9). It keeps `+972`/`972`, passes `00`/`+` international
numbers through, and dials short codes (1xx) and star numbers (`*2700`) as they are. Any other locale accepts
international format only (a PR adding your country is welcome). Extensions matching `internal_pattern` are dialled
as is.

## Run

```bash
docker build -t baresip-speakerphone-mcp .
docker run -d --name speakerphone --network host --user 1000:1000 --group-add audio \
  -e SIP_PASSWORD=... \
  -e XDG_RUNTIME_DIR=/run/user/1000 -e PULSE_SERVER=/run/user/1000/pulse/native \
  -v /run/user/1000:/run/user/1000 -v ./config:/config:ro \
  baresip-speakerphone-mcp
```

- Audio goes through the host user's PulseAudio/PipeWire socket, so other apps (e.g. a voice-assistant satellite on
  the same speakerphone) keep working alongside it.
- `config/speakerphone.json`: copy `config.example.json`. Every key is in `src/speakerphone/config.py`. Find the
  sink and source names with `pactl list short sinks` / `sources`.
- The password comes from `SIP_PASSWORD` or `SIP_PASSWORD_FILE`, never the JSON file.
- Host networking: baresip listens on `sip_listen` (default `0.0.0.0:5070`, so it can share a host with a PBX on
  5060) and uses `rtp_ports` for media.
- `GET /healthz` returns `{"ok": true, "registered": true}` when baresip is up.
- Test from a shell: `uv run scripts/mcp_call.py http://HOST:8840/mcp phone_status`.

## Notes and gotchas (verified with baresip 4.13.0, MCP Python SDK 2.3)

- baresip's own ring is replaced by `silence.wav` (the controller plays ring + announcement itself through `paplay`).
- **`sip_autoanswer_beep=off` is required.** With the default beep, baresip answers a page only when the beep
  finishes playing, and on the pulse alert device that never happened, so auto-answer calls rang until timeout.
- `mute` is a call-scoped baresip command: it exists only while a call is up, and it toggles, so the controller
  tracks the state itself.
- Registration can complete before the controller connects to ctrl_tcp, so the state is read once from `reginfo`
  on connect, then kept up to date from `REGISTER_OK`/`REGISTER_FAIL` events.
- Distro packages of baresip are old (Debian trixie ships 1.1.0), hence the source build in the `Dockerfile`.
- MCP SDK 2.x runs the server lifespan per session in streamable HTTP, so baresip is started from the HTTP app's
  lifespan instead (`server.py`).

## Tests

`uv run --with pytest pytest -q` (number normalisation and read-back).

MIT licence.
