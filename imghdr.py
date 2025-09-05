# imghdr.py — shim для окружений без стандартного imghdr (Python 3.13)
# Поддерживает базовые типы: jpeg, png, gif, webp, bmp, tiff, ico.

from typing import Optional

def _check_bytes(h: bytes) -> Optional[str]:
    if len(h) >= 2 and h[:2] == b'\xff\xd8':
        return "jpeg"
    if len(h) >= 8 and h[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    if len(h) >= 6 and (h[:6] == b'GIF87a' or h[:6] == b'GIF89a'):
        return "gif"
    if len(h) >= 12 and h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return "webp"
    if len(h) >= 2 and h[:2] == b'BM':
        return "bmp"
    if len(h) >= 4 and (h[:4] in (b'II*\x00', b'MM\x00*')):
        return "tiff"
    if len(h) >= 4 and h[:4] == b'\x00\x00\x01\x00':
        return "ico"
    return None

def what(file, h: bytes | None = None) -> Optional[str]:
    # Если h переданы — используем их
    if h is not None:
        return _check_bytes(bytes(h))
    # Если file — байты
    if isinstance(file, (bytes, bytearray)):
        return _check_bytes(bytes(file))
    # Если file — файловый объект
    try:
        if hasattr(file, "read"):
            pos = None
            try:
                pos = file.tell()
            except Exception:
                pos = None
            data = file.read(32)
            try:
                if pos is not None:
                    file.seek(pos)
            except Exception:
                pass
            if isinstance(data, str):
                data = data.encode("latin-1")
            return _check_bytes(data or b'')
        else:
            # предполагаем путь
            with open(file, "rb") as f:
                data = f.read(32)
            return _check_bytes(data or b'')
    except Exception:
        return None
