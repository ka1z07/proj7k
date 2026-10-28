"""proj7k.generator - 7K charts from audio, written by Mapperatorinator and steered to a target star rating by the
engine (ADR-0040, ADR-0046)."""

from proj7k.generator.audio import Audio, AudioDecodeError, load_audio
from proj7k.generator.generate import GenerationResult, GeneratorOptions, generate_from_file
from proj7k.generator.onset import OnsetEnvelope, onset_envelope

__all__ = [
    "Audio",
    "AudioDecodeError",
    "GenerationResult",
    "GeneratorOptions",
    "OnsetEnvelope",
    "generate_from_file",
    "load_audio",
    "onset_envelope",
]
