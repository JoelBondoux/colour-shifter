"""The local model path and an explicit, integrity-checked installer."""

import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen


MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "birefnet-lite-512.onnx"
MODEL_URL = (
    "https://huggingface.co/studioludens/birefnet-lite-512/resolve/"
    "962e67969ec6acec9c19fab76acee4f5e22e990c/onnx/model.onnx"
)
MODEL_SHA256 = "1cb0fb360dadd15af77c639085d77a9df67db0c64315560c3de005f676345ac2"


def install_model() -> None:
    if MODEL_PATH.is_file():
        with MODEL_PATH.open("rb") as model:
            if hashlib.file_digest(model, "sha256").hexdigest() == MODEL_SHA256:
                print("The verified model is already installed.")
                return

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading BiRefNet Lite (192 MB)...", flush=True)
    temporary_path = None
    try:
        digest = hashlib.sha256()
        with urlopen(MODEL_URL, timeout=90) as response, NamedTemporaryFile(
            dir=MODEL_PATH.parent, suffix=".download", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                temporary.write(chunk)

        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError("The downloaded model did not match its expected SHA-256.")
        temporary_path.replace(MODEL_PATH)
        print("Model verified and installed. Colour Shifter can now work offline.")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        install_model()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Model setup failed. {error}") from error
