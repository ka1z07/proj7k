"""proj7k.generator - 7K charts from audio, steered to a target star rating by the engine (ADR-0027)."""

from proj7k.generator.audio import Audio, AudioDecodeError, load_audio
from proj7k.generator.generate import GenerationResult, GeneratorOptions, generate, generate_from_file
from proj7k.generator.onset import OnsetEnvelope, onset_envelope
from proj7k.generator.tempo import TempoEstimate, estimate_tempo

__all__ = [
    "Audio",
    "AudioDecodeError",
    "GenerationResult",
    "GeneratorOptions",
    "OnsetEnvelope",
    "TempoEstimate",
    "estimate_tempo",
    "generate",
    "generate_from_file",
    "load_audio",
    "onset_envelope",
]
