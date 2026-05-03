import argparse
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote


MATCH_FIELDS = [
    "MIDlet-Name",
    "MIDlet-Vendor",
    "MIDlet-Version",
    "MIDlet-Icon",
    "MIDlet-Data-Size",
]


def parse_props(text: str) -> dict:
    props = {}

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue

        key, value = line.split(":", 1)
        props[key.strip()] = value.strip()

    return props


def read_jad(path: Path) -> dict:
    return parse_props(path.read_text("utf-8"))


def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path, "r") as z:
        for name in z.namelist():
            if name.upper() == "META-INF/MANIFEST.MF":
                return parse_props(z.read(name).decode("utf-8"))
    return {}


def jar_name_from_jad(jad_props: dict) -> str | None:
    url = jad_props.get("MIDlet-Jar-URL", "").strip()
    if not url:
        return None

    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name

    return name if name.lower().endswith(".jar") else None


def score_match(jad: Path, jad_props: dict, jar: Path, manifest: dict) -> int:
    score = 0

    expected_name = jar_name_from_jad(jad_props)

    if expected_name and jar.name.lower() == expected_name.lower():
        score += 100

    jad_size = jad_props.get("MIDlet-Jar-Size")
    if jad_size and jad_size.isdigit() and int(jad_size) == jar.stat().st_size:
        score += 75

    for field in MATCH_FIELDS:
        if jad_props.get(field) and manifest.get(field):
            if jad_props[field].strip() == manifest[field].strip():
                score += 25

    return score


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    i = 2
    while True:
        new_path = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not new_path.exists():
            return new_path
        i += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-score", type=int, default=75)
    args = parser.parse_args()

    folder = Path(args.folder)

    jad_files = list(folder.glob("*.jad"))
    jar_files = list(folder.glob("*.jar"))

    jar_manifests = {}
    for jar in jar_files:
        try:
            jar_manifests[jar] = read_manifest(jar)
        except Exception as ex:
            print(f"Skipping bad JAR: {jar.name} - {ex}")

    used_jars = set()

    for jad in jad_files:
        try:
            jad_props = read_jad(jad)
        except Exception as ex:
            print(f"Skipping bad JAD: {jad.name} - {ex}")
            continue

        wanted_jar_name = jar_name_from_jad(jad_props)

        if not wanted_jar_name:
            print(f"No MIDlet-Jar-URL filename found in {jad.name}")
            continue

        best_jar = None
        best_score = -1

        for jar, manifest in jar_manifests.items():
            if jar in used_jars:
                continue

            score = score_match(jad, jad_props, jar, manifest)

            if score > best_score:
                best_score = score
                best_jar = jar

        print(f"\nJAD: {jad.name}")
        print(f"Expected output name: {wanted_jar_name}")
        print(f"Best JAR: {best_jar.name if best_jar else 'None'}")
        print(f"Score: {best_score}")

        if not best_jar or best_score < args.min_score:
            print("No confident match found.")
            continue

        used_jars.add(best_jar)

        base_name = Path(wanted_jar_name).stem

        new_jad = unique_path(jad.with_name(base_name + ".jad"))
        new_jar = unique_path(best_jar.with_name(base_name + ".jar"))

        print(f"Rename JAD: {jad.name} -> {new_jad.name}")
        print(f"Rename JAR: {best_jar.name} -> {new_jar.name}")

        if args.apply:
            jad.rename(new_jad)
            best_jar.rename(new_jar)
            print("Renamed.")


if __name__ == "__main__":
    main()