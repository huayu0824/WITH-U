"""Convert a WAV file to ESP32 local-playback PCM (16kHz, 16-bit, mono)."""

import argparse
import audioop
import wave
from pathlib import Path


def convert(source: Path, destination: Path):
    with wave.open(str(source), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if channels == 2:
        frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
    elif channels != 1:
        raise ValueError(f"Only mono/stereo WAV is supported, got {channels} channels")
    if sample_width != 2:
        frames = audioop.lin2lin(frames, sample_width, 2)
        sample_width = 2
    if sample_rate != 16000:
        frames, _ = audioop.ratecv(frames, sample_width, 1, sample_rate, 16000, None)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(frames)
    print(f"Wrote {destination}: {len(frames)} bytes, {len(frames) / 32000:.2f}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Input PCM WAV")
    parser.add_argument("destination", type=Path, help="Output raw .pcm")
    args = parser.parse_args()
    convert(args.source, args.destination)


if __name__ == "__main__":
    main()
