"""Automated call against the running bot: plays WAV files as the caller and times the replies.

Start the bot first (uv run python -m agent.bot), then:
    uv run python scripts/webrtc_smoke.py evals/data/audio/clean/u03.wav evals/data/audio/clean/u10.wav

Reports, per question, the time from the caller's last spoken word to the first audible bot audio.
That includes end-of-turn detection, STT, LLM, TTS and the WebRTC jitter buffer, i.e. what a caller
experiences. The bot's own log shows the per-stage breakdown.
"""

import argparse
import asyncio
import fractions
import time
import wave

import httpx
import numpy as np
import soxr
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame

RATE = 48000
FRAME = 960  # 20 ms
AUDIBLE = 150  # mean |sample| (int16) above which a frame counts as speech


def load_wav_48k(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        sr = w.getframerate()
    return (soxr.resample(x, sr, RATE) * 32767).astype(np.int16)


class CallerTrack(MediaStreamTrack):
    """Streams queued audio in real time and silence otherwise."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._buf = np.zeros(0, dtype=np.int16)
        self._pts = 0
        self._t0 = None
        self.ended_at: float | None = None  # wall time when the queued audio finished playing
        self.trailing_silence_s = 0.0

    def say(self, samples: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, samples])
        self.ended_at = None
        # The recordings carry padding after the last word; latency is measured from the last word.
        loud = np.nonzero(np.abs(samples.astype(np.int32)) > 800)[0]
        self.trailing_silence_s = (len(samples) - 1 - loud[-1]) / RATE if len(loud) else 0.0

    async def recv(self) -> AudioFrame:
        if self._t0 is None:
            self._t0 = time.perf_counter()
        wait = self._t0 + self._pts / RATE - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk, self._buf = self._buf[:FRAME], self._buf[FRAME:]
        if len(chunk) < FRAME:
            if len(chunk) and self.ended_at is None:
                self.ended_at = time.perf_counter()
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        elif len(self._buf) == 0:
            self.ended_at = time.perf_counter() + FRAME / RATE
        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, RATE)
        self._pts += FRAME
        return frame


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--url", default="http://localhost:7860")
    ap.add_argument(
        "--settle", type=float, default=1.5, help="seconds of bot silence that end a reply"
    )
    args = ap.parse_args()

    pc = RTCPeerConnection()
    caller = CallerTrack()
    pc.addTrack(caller)
    pc.createDataChannel("chat")
    heard: list[float] = []  # wall times of audible bot frames

    @pc.on("track")
    def on_track(track):
        async def consume():
            while True:
                try:
                    frame = await track.recv()
                except Exception:
                    return
                if float(np.abs(frame.to_ndarray().astype(np.int32)).mean()) > AUDIBLE:
                    heard.append(time.perf_counter())

        asyncio.ensure_future(consume())

    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{args.url}/api/offer",
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        )
        r.raise_for_status()
        ans = r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))

    async def wait_for_reply(since: float, timeout: float = 30.0) -> tuple[float | None, float]:
        """Return (first audible time after `since`, time the reply finished)."""
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            after = [t for t in heard if t > since]
            if after and time.perf_counter() - after[-1] > args.settle:
                return after[0], after[-1]
            await asyncio.sleep(0.05)
        after = [t for t in heard if t > since]
        return (after[0] if after else None), (after[-1] if after else since)

    print("waiting for the greeting...")
    first, last = await wait_for_reply(0.0, timeout=45.0)
    print(
        "  greeting heard" if first else "  NO greeting heard",
        f"({last - first:.1f}s of audio)" if first else "",
    )

    for path in args.wavs:
        caller.say(load_wav_48k(path))
        while caller.ended_at is None or time.perf_counter() < caller.ended_at:
            await asyncio.sleep(0.02)
        ended = caller.ended_at
        first, last = await wait_for_reply(ended - caller.trailing_silence_s)
        ended -= caller.trailing_silence_s  # time of the last spoken word
        if first is None:
            print(f"{path}: no reply within timeout")
        else:
            print(
                f"{path}: first bot audio {first - ended:.2f}s after the caller's last word; reply lasted {last - first:.1f}s"
            )
        await asyncio.sleep(0.5)

    await pc.close()


if __name__ == "__main__":
    asyncio.run(main())
