"""Explicit, synthetic audio comparison; never routes or changes production calls."""
import argparse
import asyncio
import base64
import json
from pathlib import Path
from time import monotonic

from app.core.config import Settings
from app.services.voice_providers import LiveProtocol, RealtimeProtocol


async def evaluate_audio(provider, audio: bytes, settings: Settings, *, connector=None) -> dict:
    if not settings.openai_api_key:
        raise ValueError("evaluation_key_missing")
    if not 0 < len(audio) <= 8000 * 60:
        raise ValueError("evaluation_audio_must_be_raw_pcmu_1_to_60_seconds")
    if connector is None:
        from websockets.asyncio.client import connect
        connector = connect
    live = isinstance(provider, LiveProtocol)
    model = "gpt-live-1" if live else settings.openai_realtime_model
    report = {"provider": "live" if live else "realtime", "model": model,
              "voice": provider.voice if live else "marin", "input_bytes": len(audio),
              "output_audio_bytes": 0, "transcript_fragments": 0,
              "finalized": False, "first_output_seconds": None, "usage": None}
    async with connector(provider.url(model), additional_headers={
        "Authorization": f"Bearer {settings.openai_api_key}",
    }, open_timeout=10, close_timeout=5, max_size=1_000_000) as connection:
        async def wait_for(kind):
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await connection.recv())
                    if event.get("type") == "error":
                        raise ValueError("evaluation_provider_rejected")
                    if event.get("type") == kind:
                        return
        greeting = "Guten Tag bei Beispiel. Bitte schildern Sie Ihr Anliegen."
        if live:
            await connection.send(json.dumps(provider.start(greeting=greeting, company_name="Synthetischer Testbetrieb")))
            await wait_for("session.started")
            await connection.send(json.dumps({"type": "session.commentary.append", "delegation_id": None,
                                               "content": "Begrüße jetzt kurz auf Deutsch, dann höre zu."}))
        else:
            await wait_for("session.created")
            await connection.send(json.dumps({"type": "session.update", "session": {
                "type": "realtime", "model": model, "output_modalities": ["audio"],
                "instructions": LiveProtocol().start(greeting=greeting, company_name="Synthetischer Testbetrieb")["session"]["instructions"],
                "audio": {"input": {"format": {"type": "audio/pcmu"},
                          "transcription": {"model": "gpt-4o-mini-transcribe", "language": "de"},
                          "turn_detection": {"type": "server_vad", "create_response": True, "interrupt_response": True}},
                          "output": {"format": {"type": "audio/pcmu"}, "voice": "marin"}},
                "tools": [], "tool_choice": "none",
            }}))
            await wait_for("session.updated")
            await connection.send(json.dumps({"type": "response.create"}))
        started = monotonic()
        closed = asyncio.Event()

        async def receive():
            while True:
                event = json.loads(await connection.recv())
                kind = event.get("type", "")
                if kind == "error":
                    raise ValueError("evaluation_provider_error")
                if kind in {"session.output_audio.delta", "response.output_audio.delta"}:
                    report["output_audio_bytes"] += len(base64.b64decode(event["delta"], validate=True))
                    if report["first_output_seconds"] is None:
                        report["first_output_seconds"] = round(monotonic() - started, 3)
                if "transcript" in kind or "transcription" in kind:
                    report["transcript_fragments"] += 1
                if live:
                    result = provider.handle_event(event)
                    if result and result["kind"] == "send":
                        await connection.send(json.dumps(result["event"]))
                if kind == "session.closed":
                    report["finalized"] = True
                    report["usage"] = event.get("usage")
                    closed.set()
                    return
                if kind == "response.done":
                    report["usage"] = (event.get("response") or {}).get("usage")

        receiver = asyncio.create_task(receive())
        try:
            # Raw G.711 input, paced in 20 ms frames. Tail silence permits final captions.
            paced_audio = audio + b"\xff" * 24000
            for offset in range(0, len(paced_audio), 160):
                if receiver.done():
                    receiver.result()
                    break
                await connection.send(json.dumps(provider.audio_append(base64.b64encode(paced_audio[offset:offset + 160]).decode("ascii"))))
                await asyncio.sleep(max(0, started + (offset + 160) / 8000 - monotonic()))
            if live:
                await connection.send(json.dumps(provider.close()))
                await asyncio.wait_for(asyncio.shield(receiver), timeout=15)
                if not closed.is_set():
                    raise ValueError("evaluation_finalization_unconfirmed")
            else:
                report["finalized"] = True
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
    return report


def main():
    parser = argparse.ArgumentParser(description="Opt-in-Sprachtest ohne Telefonie, Datenbank oder externe Aktionen. Es entstehen OpenAI-Kosten.")
    parser.add_argument("--provider", choices=["realtime", "live"], required=True)
    parser.add_argument("--voice", choices=["nuri", "juni", "flitz"], default="nuri")
    parser.add_argument("--synthetic-pcmu", type=Path, required=True, help="Ausschließlich synthetisches G.711-µ-law-Testaudio, mono 8 kHz, ohne Dateikopf, maximal 60 Sekunden")
    parser.add_argument("--connect", action="store_true", help="Kostenpflichtige Verbindung ausdrücklich aktivieren")
    args = parser.parse_args()
    if not args.connect:
        parser.error("Für einen kostenpflichtigen Test muss --connect ausdrücklich angegeben werden.")
    try:
        provider = LiveProtocol(voice=args.voice) if args.provider == "live" else RealtimeProtocol()
        report = asyncio.run(evaluate_audio(provider, args.synthetic_pcmu.read_bytes(), Settings()))
    except Exception:
        parser.exit(1, "Sprachtest fehlgeschlagen. Zugang, Audioformat und Anbieterkompatibilität prüfen; keine Produktionsänderung vorgenommen.\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
