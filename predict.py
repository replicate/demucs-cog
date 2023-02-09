import tempfile
from typing import List
import torch
from cog import BaseModel, BasePredictor, File, Input, Path
from demucs.apply import apply_model
from demucs.audio import save_audio
from demucs.pretrained import get_model
from demucs.separate import load_track
from io import BytesIO

class DemucsStem(BaseModel):
    name: str
    audio: File

class DemucsResponse(BaseModel):
    stems: List[DemucsStem]


class Predictor(BasePredictor):
    def setup(self):
        self.model = get_model("mdx_extra_q")

    def predict(
        self,
        audio: Path = Input(description="The audio file to separate"),
        two_stems: str = Input(
            default=None,
            description="If you want to separate into two stems, enter the name of the stem here.",
            choices=["drums", "bass", "other", "vocals"],
        ),
        int24: bool = Input(
            default=False,
            description="If you want to output 24 bit wav files, set this to true.",
        ),
        float32: bool = Input(
            default=False,
            description="If you want to output 32 bit wav files, set this to true. Keep in mind this is 2x bigger.",
        ),
        clip_mode: str = Input(
            default="rescale",
            description="Strategy for avoiding clipping: rescaling entire signal if necessary (rescale) or hard clipping (clamp)",
            choices=["rescale", "clamp"],
        ),
        mp3: bool = Input(
            default=False,
            description="If you want to convert the output wavs to mp3, set this to true.",
        ),
        mp3_bitrate: int = Input(
            default=320, description="The bitrate of the converted mp3."
        ),
        shifts: int = Input(
            default=1,
            description="Number of random shifts for equivariant stabilization. Increase separation time but improves quality for Demucs. 10 was used in the original paper.",
        ),
        workers: int = Input(
            default=0,
            description="Number of jobs. This can increase memory usage but will be much faster when multiple cores are available.",
        ),
        split: bool = Input(
            default=False,
            description="If you want to split audio in chunks, set this to true. This can use large amounts of memory.",
        ),
        overlap: float = Input(default=0.25, description="Overlap between the splits."),
    ) -> List[Path]:

        self.model.cpu()
        self.model.eval()

        wav = load_track(audio, self.model.audio_channels, self.model.samplerate)

        ref = wav.mean(0)
        wav = (wav - ref.mean()) / ref.std()
        sources = apply_model(
            self.model,
            wav[None],
            device="cuda" if torch.cuda.is_available() else "cpu",
            shifts=shifts,
            split=split,
            overlap=overlap,
            progress=True,
            num_workers=workers,
        )[0]
        sources = sources * ref.std() + ref.mean()

        if mp3:
            ext = "mp3"
        else:
            ext = "wav"

        kwargs = {
            "samplerate": self.model.samplerate,
            "bitrate": mp3_bitrate,
            "clip": clip_mode,
            "as_float": float32,
            "bits_per_sample": 24 if int24 else 16,
        }

        if two_stems is None:
            for source, name in zip(sources, self.model.sources):
                save_audio(source.cpu(), f'{name}.{ext}', **kwargs)
        else:
            sources = list(sources)
            save_audio(
                sources[self.model.sources.index(two_stems)].cpu(), f"{two_stems}.{ext}", **kwargs
            )

            sources.pop(self.model.sources.index(two_stems))

            other_stem = torch.zeros_like(sources[0])
            for i in sources:
                other_stem += i

            save_audio(other_stem.cpu(), f"other.{ext}", **kwargs)

        return [Path(f"{name}.{ext}") for name in self.model.sources]
