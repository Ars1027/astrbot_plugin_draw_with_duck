import hashlib
import os
import struct
from pathlib import Path

import numpy as np
from PIL import Image

WATERMARK_SKIP_W_RATIO = 0.40
WATERMARK_SKIP_H_RATIO = 0.08


class DuckDecodeError(Exception):
    """Raised when a duck image cannot be decoded."""


def _extract_payload_with_k(arr: np.ndarray, k: int) -> bytes:
    h, w, c = arr.shape
    skip_w = int(w * WATERMARK_SKIP_W_RATIO)
    skip_h = int(h * WATERMARK_SKIP_H_RATIO)

    mask2d = np.ones((h, w), dtype=bool)
    if skip_w > 0 and skip_h > 0:
        mask2d[:skip_h, :skip_w] = False

    mask3d = np.repeat(mask2d[:, :, None], c, axis=2)
    flat = arr.reshape(-1)
    idxs = np.flatnonzero(mask3d.reshape(-1))
    vals = (flat[idxs] & ((1 << k) - 1)).astype(np.uint8)

    unpacked = np.unpackbits(vals, bitorder="big").reshape(-1, 8)[:, -k:]
    bits = unpacked.reshape(-1)
    if len(bits) < 32:
        raise DuckDecodeError("image data is too small")

    header_len = struct.unpack(">I", np.packbits(bits[:32], bitorder="big").tobytes())[0]
    total_bits = 32 + header_len * 8
    if header_len <= 0 or total_bits > len(bits):
        raise DuckDecodeError("payload length is invalid")

    payload_bits = bits[32:total_bits]
    return np.packbits(payload_bits, bitorder="big").tobytes()


def _generate_key_stream(password: str, salt: bytes, length: int) -> bytes:
    key_material = (password + salt.hex()).encode("utf-8")
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key_material + str(counter).encode("utf-8")).digest())
        counter += 1
    return bytes(out[:length])


def _parse_header(header: bytes, password: str) -> tuple[bytes, str]:
    idx = 0
    if len(header) < 1:
        raise DuckDecodeError("header is corrupted")

    has_password = header[0] == 1
    idx += 1
    pwd_hash = b""
    salt = b""

    if has_password:
        if len(header) < idx + 32 + 16:
            raise DuckDecodeError("password header is corrupted")
        pwd_hash = header[idx : idx + 32]
        idx += 32
        salt = header[idx : idx + 16]
        idx += 16

    if len(header) < idx + 1:
        raise DuckDecodeError("extension header is corrupted")
    ext_len = header[idx]
    idx += 1

    if len(header) < idx + ext_len + 4:
        raise DuckDecodeError("data header is corrupted")
    ext = header[idx : idx + ext_len].decode("utf-8", errors="ignore")
    idx += ext_len

    data_len = struct.unpack(">I", header[idx : idx + 4])[0]
    idx += 4
    data = header[idx:]

    if len(data) != data_len:
        raise DuckDecodeError("payload length mismatch")

    if not has_password:
        return data, ext
    if not password:
        raise DuckDecodeError("duck image requires a password")

    check_hash = hashlib.sha256((password + salt.hex()).encode("utf-8")).digest()
    if check_hash != pwd_hash:
        raise DuckDecodeError("wrong duck decode password")

    key_stream = _generate_key_stream(password, salt, len(data))
    return bytes(a ^ b for a, b in zip(data, key_stream)), ext


def _binpng_bytes_to_mp4_bytes(path: Path) -> bytes:
    img = Image.open(path).convert("RGB")
    arr = np.array(img).astype(np.uint8)
    return arr.reshape(-1, 3).reshape(-1).tobytes().rstrip(b"\x00")


def decode_duck_image(
    duck_image_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    password: str = "",
    base_name: str = "duck_recovered",
) -> tuple[str, str]:
    """Decode a SS_tools duck image and return (output_path, extension)."""
    duck_path = Path(duck_image_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(duck_path).convert("RGB")
    arr = np.array(img).astype(np.uint8)
    last_error: Exception | None = None

    for k in (2, 6, 8):
        try:
            header = _extract_payload_with_k(arr, k)
            raw, ext = _parse_header(header, password)
            break
        except Exception as exc:
            last_error = exc
    else:
        raise DuckDecodeError(f"failed to decode duck payload: {last_error}") from last_error

    safe_ext = (ext or "bin").lstrip(".") or "bin"
    output_path = out_dir / f"{base_name}.{safe_ext}"
    output_path.write_bytes(raw)

    if safe_ext.lower().endswith("binpng"):
        mp4_path = out_dir / f"{base_name}.mp4"
        mp4_path.write_bytes(_binpng_bytes_to_mp4_bytes(output_path))
        output_path.unlink(missing_ok=True)
        return str(mp4_path), "mp4"

    return str(output_path), safe_ext
