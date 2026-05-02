import argparse
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote


TEXT_ENCODINGS = ("cp932", "shift_jis", "utf-8-sig", "utf-8", "latin-1")


def decode_text(data: bytes) -> str:
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1", errors="replace")


def parse_properties(text: str) -> dict:
    props = {}
    current_key = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        # Manifest continuation line
        if raw_line.startswith(" ") and current_key:
            props[current_key] += raw_line[1:].strip()
            continue

        if ":" in line:
            key, value = line.split(":", 1)
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            continue

        key = key.strip()
        value = value.strip()
        props[key] = value
        current_key = key

    return props


def read_jad(path: Path) -> dict:
    return parse_properties(decode_text(path.read_bytes()))


def read_manifest_from_jar(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path, "r") as z:
            for name in z.namelist():
                if name.upper() == "META-INF/MANIFEST.MF":
                    return parse_properties(decode_text(z.read(name)))
    except zipfile.BadZipFile:
        pass

    return {}


def get_jar_url_filename(jad_props: dict) -> str:
    url = jad_props.get("MIDlet-Jar-URL", "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)

    return unquote(filename)


def normalize(value: str) -> str:
    return value.strip().lower()


def score_match(jad_path: Path, jad_props: dict, jar_path: Path, manifest_props: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    expected_jar_name = get_jar_url_filename(jad_props)

    if expected_jar_name and normalize(jar_path.name) == normalize(expected_jar_name):
        score += 50
        reasons.append(f"JAR filename matches MIDlet-Jar-URL: {expected_jar_name}")

    jad_size = jad_props.get("MIDlet-Jar-Size")
    if jad_size and jad_size.isdigit():
        actual_size = jar_path.stat().st_size
        if int(jad_size) == actual_size:
            score += 40
            reasons.append(f"JAR size matches MIDlet-Jar-Size: {actual_size}")

    compare_fields = [
        "MIDlet-Name",
        "MIDlet-Vendor",
        "MIDlet-Version",
        "MIDlet-Description",
        "MIDlet-Icon",
        "MIDlet-1",
        "MIDlet-OCL",
    ]

    for field in compare_fields:
        jad_value = jad_props.get(field)
        manifest_value = manifest_props.get(field)

        if jad_value and manifest_value and normalize(jad_value) == normalize(manifest_value):
            score += 20
            reasons.append(f"{field} matches")

    return score, reasons


def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.rstrip(". ")

    return name or "Unknown MIDlet"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def main():
    parser = argparse.ArgumentParser(
        description="Match .JAD files to .JAR files using MIDlet-Jar-URL and JAR manifests, then rename both."
    )
    parser.add_argument("folder", help="Folder containing .jad and .jar files")
    parser.add_argument("--recursive", action="store_true", help="Scan folders recursively")
    parser.add_argument("--apply", action="store_true", help="Actually rename files. Default is dry-run.")
    parser.add_argument("--min-score", type=int, default=60, help="Minimum score required to rename a pair")
    args = parser.parse_args()

    root = Path(args.folder)

    if not root.exists():
        print(f"Folder does not exist: {root}")
        return

    pattern = "**/*" if args.recursive else "*"

    jad_files = sorted(p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() == ".jad")
    jar_files = sorted(p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() == ".jar")

    jar_manifests = {}
    for jar in jar_files:
        jar_manifests[jar] = read_manifest_from_jar(jar)

    used_jars = set()

    print(f"Found {len(jad_files)} JAD file(s)")
    print(f"Found {len(jar_files)} JAR file(s)")
    print("Mode:", "APPLY" if args.apply else "DRY-RUN")
    print()

    for jad in jad_files:
        jad_props = read_jad(jad)

        best_jar = None
        best_score = -1
        best_reasons = []

        for jar in jar_files:
            if jar in used_jars:
                continue

            manifest = jar_manifests.get(jar, {})
            score, reasons = score_match(jad, jad_props, jar, manifest)

            if score > best_score:
                best_score = score
                best_jar = jar
                best_reasons = reasons

        midlet_name = jad_props.get("MIDlet-Name") or jad_props.get("MIDlet-1", "").split(",")[0]
        safe_name = sanitize_filename(midlet_name)

        print(f"JAD: {jad.name}")

        if not best_jar or best_score < args.min_score:
            print(f"  No confident match found. Best score: {best_score}")
            print()
            continue

        used_jars.add(best_jar)

        new_jad = unique_path(jad.with_name(f"{safe_name}.jad"))
        new_jar = unique_path(best_jar.with_name(f"{safe_name}.jar"))

        print(f"  Matched JAR: {best_jar.name}")
        print(f"  Score: {best_score}")
        for reason in best_reasons:
            print(f"   - {reason}")

        print(f"  Rename JAD: {jad.name} -> {new_jad.name}")
        print(f"  Rename JAR: {best_jar.name} -> {new_jar.name}")

        if args.apply:
            jad.rename(new_jad)
            best_jar.rename(new_jar)
            print("  Renamed.")

        print()


if __name__ == "__main__":
    main()