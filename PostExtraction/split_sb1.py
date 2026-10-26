"""Split decrypted SB1 backups into named JAR/JAM/SP files.

Usage:
    python split_sb1.py INPUT_FILE_OR_DIRECTORY OUTPUT_DIRECTORY
    python split_sb1.py INPUT OUTPUT --header-size 0

Python standard library only. Input directories are searched recursively.
The default clear SB1 header is4096 bytes. Encrypted SB1s must be decrypted
first. Each backup gets its own folder, preserving duplicate package names.
Outputs contain only .jar/.jam/.sp files and one master index.txt. JAM bytes
are preserved exactly; PackageURL is used for naming, never fetched.
"""
import argparse
import hashlib
import io
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(jam):
    result = {}
    for line in jam.decode('cp932', errors='replace').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            result.setdefault(key.strip().lower(), value.strip().strip('\0'))
    return result


def split(raw, header_size):
    data = raw[header_size:]
    require(data[:2] == b'\x01\x04',
            'No decrypted SB1 metadata at the requested header offset; check decryption/header size.')
    limit = int.from_bytes(data[2:6], 'big')
    require(6 <= limit <= len(data), 'Invalid SB1 metadata length')
    cursor = 0
    tags = {}
    while cursor < limit:
        require(cursor + 2 <= limit, 'Truncated metadata tag')
        tag, size = data[cursor:cursor + 2]
        cursor += 2
        if size == 255:
            require(cursor + 2 <= limit, 'Truncated extended tag length')
            size = int.from_bytes(data[cursor:cursor + 2], 'big')
            cursor += 2
        require(cursor + size <= limit and tag not in tags, 'Invalid or duplicate metadata tag')
        tags[tag] = data[cursor:cursor + size]
        cursor += size
    require(3 in tags, 'Missing JAM descriptor')
    jam = tags[3]
    metadata = fields(jam)
    require(metadata.get('appsize', '').isdigit(), 'Missing or invalid JAM AppSize')
    jar_size = int(metadata['appsize'])
    require(limit + jar_size <= len(data), 'JAR extends past the SB1 end')
    jar = data[limit:limit + jar_size]
    scratchpad = data[limit + jar_size:]
    require(jar[:4] == b'PK\x03\x04', 'Missing JAR/ZIP signature')
    sp_value = metadata.get('spsize', '')
    sp_sizes = [int(n.strip()) for n in sp_value.split(',')] if sp_value else []
    require(all(n >= 0 for n in sp_sizes) and sum(sp_sizes) == len(scratchpad),
            'Scratchpad length disagrees with JAM SPsize')
    return (jar, jam, scratchpad), metadata, tags.get(2, b'').decode('cp932', errors='replace')


def audit_zip(data, depth=0):
    require(depth < 8, 'Excessive nested JAR depth')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        bad = archive.testzip()
        require(bad is None, f'JAR CRC failed: {bad}')
        for item in archive.infolist():
            if item.filename.lower().endswith('.jar'):
                nested = archive.read(item)
                if nested[:4] == b'PK\x03\x04':
                    audit_zip(nested, depth + 1)


def leaf(value):
    return value.strip().replace('\\', '/').rsplit('/', 1)[-1]


def jar_name(value):
    name = leaf(value)
    return name if len(name) > 4 and name.lower().endswith('.jar') else None


def package_stem(metadata, download_url, source):
    url = metadata.get('packageurl', '')
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True, encoding='cp932', errors='replace')
    # Download endpoints often use a generic path such as dljar.jar, but the
    # query carries the real filename: a=WAMA.jar, f=game.jar, bin=game.jar.
    for key, value in query:
        name = jar_name(value)
        if name:
            return name[:-4], f'PackageURL query parameter {key}'
    for key, value in query:
        if not value:
            name = jar_name(key)  # e.g. down.php?uid=...&i.jar
            if name:
                return name[:-4], 'PackageURL bare query filename'
    name = jar_name(unquote(parts.path, encoding='cp932', errors='replace'))
    if name:
        return name[:-4], 'PackageURL path'
    # Never pretend an opaque PHP/servlet endpoint is a known JAR filename.
    download_name = leaf(unquote(urlsplit(download_url).path, encoding='cp932', errors='replace'))
    if len(download_name) > 4 and download_name.lower().endswith('.jam'):
        return download_name[:-4], 'fallback: saved JAM download URL'
    return metadata.get('appname') or source.stem, 'fallback: AppName or SB1 filename'


def safe_component(value, limit=100):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip().rstrip(' .')
    value = value[:limit].rstrip(' .') or 'unnamed'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def one_line(value):
    return re.sub(r'[\r\n\t\x00]', ' ', str(value))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, help='Decrypted SB1 file or directory containing SB1 files')
    parser.add_argument('output', type=Path, help='Separate export folder; unrelated existing files are rejected')
    parser.add_argument('--header-size', type=lambda x: int(x, 0), default=4096,
                        help='Clear bytes before decrypted payload (default:4096; hexadecimal accepted)')
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    require(args.header_size >= 0, 'Header size must be nonnegative')
    require(source.exists(), f'Input does not exist: {source}')
    require(output != source and output not in source.parents, 'Output must be separate from the source')
    root = source if source.is_dir() else source.parent
    sources = sorted((p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.sb1'),
                     key=lambda p: p.relative_to(root).as_posix().casefold()) if source.is_dir() else [source]
    require(bool(sources), 'No SB1 files found')
    plans = []
    used_folders = set()
    expected = {'index.txt'}
    # Validate all inputs, package names, collisions and existing outputs first.
    for number, path in enumerate(sources, 1):
        raw = path.read_bytes()
        try:
            payloads, metadata, download_url = split(raw, args.header_size)
            audit_zip(payloads[0])
        except (ValueError, zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError(f'{path}: {error}') from error
        relative = path.relative_to(root)
        original_stem, method = package_stem(metadata, download_url, path)
        stem = safe_component(original_stem)
        title = metadata.get('appname', path.stem)
        group = next((p for p in path.parts if re.fullmatch(r'(?i)Card\d+', p)), '')
        folder = Path(group) / safe_component(f'{path.stem}_{title}', 80)
        if folder.as_posix().casefold() in used_folders:
            folder = folder.with_name(folder.name + '_' + digest(relative.as_posix().encode())[:10])
        require(folder.as_posix().casefold() not in used_folders, 'Unresolved output folder collision')
        used_folders.add(folder.as_posix().casefold())
        targets = [folder / (stem + extension) for extension in ('.jar', '.jam', '.sp')]
        for target, payload in zip(targets, payloads):
            dest = output / target
            require(dest.resolve().is_relative_to(output), 'Output path escapes export directory')
            if dest.exists():
                require(dest.is_file() and dest.read_bytes() == payload, f'Conflicting existing output: {dest}')
            expected.add(target.as_posix().casefold())
        plans.append({'source': path, 'relative': relative.as_posix(), 'source_hash': digest(raw),
                      'targets': targets, 'title': title, 'packageurl': metadata.get('packageurl', ''),
                      'method': method, 'renamed': stem != original_stem,
                      'hashes': [digest(p) for p in payloads], 'sizes': [len(p) for p in payloads]})
        if number % 100 == 0:
            print(f'Validated {number}/{len(sources)} SB1 files', flush=True)
    if output.exists():
        require(output.is_dir(), 'Output must be a directory')
        extras = [p.relative_to(output).as_posix() for p in output.rglob('*')
                  if p.is_file() and p.relative_to(output).as_posix().casefold() not in expected]
        require(not extras, f'Output contains unrelated files; choose a fresh folder: {extras[:5]}')
    lines = ['SB1 application export', f'Applications: {len(plans)}', f'Exported payload files: {len(plans)*3}',
             'Master index: index.txt', f'Source root: {root}',
             'Each JAM and concatenated SP is preserved byte-for-byte.', '']
    output.mkdir(parents=True, exist_ok=True)
    for number, plan in enumerate(plans, 1):
        raw = plan['source'].read_bytes()
        require(digest(raw) == plan['source_hash'], f"Input changed during export: {plan['source']}")
        payloads, _, _ = split(raw, args.header_size)
        lines.extend([f"[{number:04d}] {one_line(plan['title'])}", f"Source SB1: {plan['relative']}",
                      f"PackageURL: {one_line(plan['packageurl'])}", f"Filename source: {plan['method']}"])
        if plan['renamed']:
            lines.append('Filename adjusted for Windows filesystem limits.')
        for target, payload, hash_value in zip(plan['targets'], payloads, plan['hashes']):
            dest = output / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(payload)
            require(digest(dest.read_bytes()) == hash_value, f'Export verification failed: {dest}')
            lines.append(f'File: {target.as_posix()} | {len(payload)} bytes | SHA256 {hash_value}')
        lines.append('')
    (output / 'index.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    fallback_count = sum(p['method'].startswith('fallback:') for p in plans)
    print(f'Exported {len(plans)} applications / {len(plans)*3} payload files; {fallback_count} fallback names.')
    print(f'Master index: {output / "index.txt"}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
