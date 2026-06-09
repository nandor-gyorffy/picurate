"""Script to create minimal fixture images for tests."""
import struct
import io
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _make_jpeg_with_exif(
    width=64, height=48,
    date_str="2023:01:07 15:28:33",
    make="TestCamera",
    model="TC100",
    orientation=1,
    lat=None, lon=None,
) -> bytes:
    """Create a minimal JPEG with EXIF data."""
    from PIL import Image, ExifTags
    import piexif

    img = Image.new("RGB", (width, height), color=(100, 150, 200))

    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: make.encode(),
            piexif.ImageIFD.Model: model.encode(),
            piexif.ImageIFD.Orientation: orientation,
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: date_str.encode(),
        },
        "GPS": {},
    }

    if lat is not None and lon is not None:
        def to_dms(deg):
            d = int(abs(deg))
            m = int((abs(deg) - d) * 60)
            s = round(((abs(deg) - d) * 60 - m) * 60 * 100)
            return ((d, 1), (m, 1), (s, 100))

        exif_dict["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: to_dms(lon),
        }

    try:
        exif_bytes = piexif.dump(exif_dict)
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif_bytes)
    except ImportError:
        buf = io.BytesIO()
        img.save(buf, "JPEG")
    return buf.getvalue()


def _make_jpeg_rotated(orientation: int) -> bytes:
    """Create a JPEG that is physically landscape but has EXIF orientation=6 (needs 90° CW rotation)."""
    from PIL import Image
    import io
    try:
        import piexif
        img = Image.new("RGB", (80, 40), color=(200, 100, 50))
        exif_dict = {"0th": {piexif.ImageIFD.Orientation: orientation}, "Exif": {}, "GPS": {}}
        exif_bytes = piexif.dump(exif_dict)
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif_bytes)
        return buf.getvalue()
    except ImportError:
        img = Image.new("RGB", (80, 40), color=(200, 100, 50))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return buf.getvalue()


def create_all():
    FIXTURES.mkdir(parents=True, exist_ok=True)

    # Basic JPEG with EXIF
    (FIXTURES / "basic.jpg").write_bytes(
        _make_jpeg_with_exif(date_str="2023:06:15 10:30:00", make="Canon", model="EOS R5")
    )

    # JPEG with GPS
    (FIXTURES / "gps.jpg").write_bytes(
        _make_jpeg_with_exif(
            date_str="2023:07:20 14:00:00",
            lat=47.5, lon=19.0,
        )
    )

    # JPEG that needs rotation (orientation=6)
    (FIXTURES / "rotated.jpg").write_bytes(_make_jpeg_rotated(6))

    # PNG (no EXIF)
    from PIL import Image
    img = Image.new("RGB", (32, 32), color=(0, 255, 0))
    img.save(str(FIXTURES / "plain.png"))

    # Sample .picasa.ini
    (FIXTURES / ".picasa.ini").write_text(
        "[IMG_001.jpg]\nstar=yes\ncaption=A nice photo\n"
        "[IMG_002.jpg]\nstar=no\n",
        encoding="utf-8",
    )

    # Sample XMP sidecar
    (FIXTURES / "photo.xmp").write_text(
        '<?xml version="1.0"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
        '   <xmp:Rating>4</xmp:Rating>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n',
        encoding="utf-8",
    )

    print("Fixtures created:", list(FIXTURES.iterdir()))


if __name__ == "__main__":
    create_all()
