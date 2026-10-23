"""Pretend to be Plivo: open the bot's telephony WebSocket, stream a caller recording as 8 kHz mu-law, and
report what comes back. Tests the phone path (serializer, 8 kHz audio, call lifecycle) without a Plivo account.

    DEV: uv run python -m agent.bot -t plivo --proxy localhost        # terminal 1
         uv run python scripts/plivo_sim.py path/to/caller.wav [...]  # terminal 2
"""

import argparse
import asyncio
import audioop
import base64
import json
import time
import wave

import websockets

FRAME_MS = 20


def load_ulaw_8k(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm, rate = w.readframes(w.getnframes()), w.getframerate()
    pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    return audioop.lin2ulaw(pcm, 2)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="*")
    ap.add_argument("--url", default="ws://localhost:7860/ws")
    ap.add_argument("--settle", type=float, default=2.5, help="seconds of bot silence that end a reply")
    a = ap.parse_args()
    a_wavs = a.wavs

    async with websockets.connect(a.url, max_size=None) as ws:
        await ws.send(json.dumps({"event": "start", "start": {"streamId": "sim-stream", "callId": "sim-call", "accountId": "sim"}}))
        last_audio = [0.0]
        heard = [0.0]  # total seconds of bot audio
        t_start = time.perf_counter()
        first_audio: list[float | None] = [None]

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                if m.get("event") == "playAudio":
                    n = len(base64.b64decode(m["media"]["payload"]))
                    heard[0] += n / 8000
                    last_audio[0] = time.perf_counter()
                    if first_audio[0] is None:
                        first_audio[0] = last_audio[0] - t_start

        task = asyncio.create_task(reader())

        # Plivo streams a media frame every 20 ms for the whole call (line silence between words); the bot
        # only starts speaking once audio is flowing, so the simulator must do the same.
        silence = base64.b64encode(b"\xff" * 160).decode()
        outgoing: asyncio.Queue[str] = asyncio.Queue()

        async def pump():
            t = time.perf_counter()
            while True:
                try:
                    payload = outgoing.get_nowait()
                except asyncio.QueueEmpty:
                    payload = silence
                await ws.send(json.dumps({"event": "media", "media": {"payload": payload, "contentType": "audio/x-mulaw"}}))
                t += FRAME_MS / 1000
                await asyncio.sleep(max(0, t - time.perf_counter()))

        pump_task = asyncio.create_task(pump())

        async def wait_reply(timeout: float = 45.0) -> float:
            """Wait until the bot has spoken and then been quiet for --settle seconds; return the audio length."""
            start, before = time.perf_counter(), heard[0]
            while time.perf_counter() - start < timeout:
                if heard[0] > before and time.perf_counter() - last_audio[0] > a.settle:
                    break
                await asyncio.sleep(0.1)
            return heard[0] - before

        greeting = await wait_reply()
        print(f"greeting: {greeting:.1f}s of audio, first audio after {first_audio[0]:.1f}s" if first_audio[0] is not None else "greeting: NONE")
        for path in a_wavs:
            data = load_ulaw_8k(path)
            step = 8 * FRAME_MS
            for i in range(0, len(data), step):
                outgoing.put_nowait(base64.b64encode(data[i : i + step]).decode())
            while not outgoing.empty():  # let the recording play out, then 0.8 s of silence ends the turn
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.8)
            print(f"{path.rsplit('/', 1)[-1]}: reply {await wait_reply():.1f}s of audio")
        pump_task.cancel()
        await ws.send(json.dumps({"event": "stop"}))
        await ws.close()
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
