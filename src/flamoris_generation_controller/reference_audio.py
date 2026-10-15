"""Bounded reference waveform decoding without external programs."""

import io
import wave

MAX_REFERENCE_BYTES = 8 * 1024**2
MAX_REFERENCE_SECONDS = 30
REFERENCE_CHECKPOINTS = frozenset(
    {"Aratako/Irodori-TTS-v4-Small", "Aratako/Irodori-TTS-v4.1-Small"}
)


def validate_reference_audio(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_REFERENCE_BYTES:
        raise ValueError("Reference WAV must be at most 8 MiB")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            rate = audio.getframerate()
            frames = audio.getnframes()
            if (
                audio.getcomptype() != "NONE"
                or width != 2
                or channels not in (1, 2)
                or rate not in (16000, 22050, 24000, 44100, 48000, 96000)
                or not 0 < frames <= rate * MAX_REFERENCE_SECONDS
            ):
                raise ValueError("Use a PCM16 mono/stereo WAV of at most 30 seconds")
            if len(audio.readframes(frames + 1)) != frames * channels * width:
                raise ValueError("Reference WAV is truncated")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid reference WAV") from exc
    return {"sample_rate": rate, "channels": channels, "duration_seconds": frames / rate}
