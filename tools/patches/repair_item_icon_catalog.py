#!/usr/bin/env python3
"""Audit and repair item icons using the live item and commodity catalogs.

The tool previews by default. It scans every RawItemDatas row, derives safe
repairs from unique single-item commodity entries whose icon exists in the
scanned Unreal packages, and keeps a small reviewed fallback for the backpack
whose item and commodity rows both point to a missing icon.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import zlib

MAGIC = bytes.fromhex("f3f3f3f3")
AES_KEY = bytes.fromhex("5447414d451fe92c9a971a0cd1f610fb")

# Most repairs are generated from DefaultCommodityLibrary.ini. This fallback
# is separate because both catalogs agree on 1000006, but that ID is absent
# from the supplied packages; 3000026 is the visually verified No. 3 bag image.
# Match by item name and old icon so the rest of the catalog remains data-driven.
MANUAL_ICON_FALLBACKS = (
    {
        "item_name": "Package",
        "expected_icon_id": 1000006,
        "replacement_icon_id": 3000026,
        "reason": "reviewed fallback: existing No. 3 Backpack image",
    },
)

ITEM_ROW = re.compile(rb"RawItemDatas=\(([^\r\n]*)")
COMMODITY_ROW = re.compile(rb"RawCommodityDatas=\(([^\r\n]*)")
NUMERIC_NAME = re.compile(rb"(?=(\d{5,9})\x00)")


def decrypt_catalog(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("Catalog is not in the expected encrypted PH format")
    if (len(blob) - 4) % 16:
        raise ValueError("Encrypted catalog has an invalid AES block length")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).decryptor()
    payload = decryptor.update(blob[4:]) + decryptor.finalize()
    if len(payload) < 4:
        raise ValueError("Encrypted catalog is truncated")
    expected = struct.unpack_from("<I", payload)[0]
    raw = zlib.decompress(payload[4:])
    if len(raw) != expected:
        raise ValueError("Decompressed catalog length does not match its header")
    return raw


def encrypt_catalog(raw: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    payload = struct.pack("<I", len(raw)) + zlib.compress(raw)
    payload += b"\0" * (-len(payload) % 16)
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).encryptor()
    return MAGIC + encryptor.update(payload) + encryptor.finalize()


def decode_text(raw: bytes) -> tuple[str, str, bytes]:
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le", raw[:2]
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be"), "utf-16-be", raw[:2]
    try:
        raw.decode("utf-8")
        return raw.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        return raw.decode("gbk"), "gbk", b""


def fields(row: bytes) -> dict[str, str]:
    result = {}
    for key, quoted, plain in re.findall(rb'\b(\w+)=(?:"([^"\r\n]*)"|([^,)]*))', row):
        value = quoted if quoted else plain.strip()
        result[key.decode("ascii")] = value.decode("gbk", errors="replace")
    return result


def parse_items(raw: bytes) -> list[dict[str, object]]:
    text, _, _ = decode_text(raw)
    rows: list[dict[str, object]] = []
    encoded = text.encode("gbk", errors="replace")
    for match in ITEM_ROW.finditer(encoded):
        data = fields(match.group(1))
        if "nItemID" not in data:
            continue
        rows.append({
            "item_id": int(data["nItemID"]),
            "item_name": data.get("ItemName", ""),
            "icon_id": int(data["nItemIconID"]) if "nItemIconID" in data else None,
        })
    return rows


def parse_commodity_rows(raw: bytes) -> list[dict[str, object]]:
    """Return every commodity row, preserving multi-item bundles as such."""
    text, _, _ = decode_text(raw)
    encoded = text.encode("gbk", errors="replace")
    rows: list[dict[str, object]] = []
    for match in COMMODITY_ROW.finditer(encoded):
        row = match.group(1)
        data = fields(row)
        if "nCommodityID" not in data or "IconID" not in data:
            continue
        array = re.search(rb"\bnItemIDArray=\(([^)]*)\)", row)
        item_ids = [int(value) for value in re.findall(rb"\d+", array.group(1))] if array else []
        rows.append({
            "commodity_id": int(data["nCommodityID"]),
            "commodity_name": data.get("sItemName", ""),
            "icon_id": int(data["IconID"]),
            "item_ids": item_ids,
        })
    return rows


def parse_direct_commodities(raw: bytes) -> dict[int, set[int]]:
    direct: dict[int, set[int]] = {}
    for row in parse_commodity_rows(raw):
        item_ids = row["item_ids"]
        if len(item_ids) != 1:
            continue
        direct.setdefault(int(item_ids[0]), set()).add(int(row["icon_id"]))
    return direct


def derive_icon_repairs(
    item_raw: bytes,
    commodity_raw: bytes | None,
    asset_ids: set[int],
) -> dict[int, dict[str, object]]:
    """Build safe, data-derived repair candidates for all item rows.

    An automatic candidate requires a single distinct icon ID across that
    item's single-item commodity rows, and that icon must exist in a scanned
    package. Bundle icons never participate. If both catalogs point at the
    same absent image, only an explicit reviewed fallback can resolve it.
    """
    direct = parse_direct_commodities(commodity_raw) if commodity_raw else {}
    repairs: dict[int, dict[str, object]] = {}
    for item in parse_items(item_raw):
        item_id = int(item["item_id"])
        current = item["icon_id"]
        if current is None or (int(current) != 0 and int(current) in asset_ids):
            continue

        direct_ids = direct.get(item_id, set())
        available = {icon for icon in direct_ids if icon != 0 and icon in asset_ids}
        if len(direct_ids) == 1 and len(available) == 1:
            target = next(iter(available))
            if target != current:
                repairs[item_id] = {
                    "expected_icon_id": current,
                    "replacement_icon_id": target,
                    "source": "unique direct single-item commodity icon present in scanned packages",
                }
                continue

        for fallback in MANUAL_ICON_FALLBACKS:
            if (item["item_name"] == fallback["item_name"]
                    and current == fallback["expected_icon_id"]
                    and fallback["replacement_icon_id"] in asset_ids):
                repairs[item_id] = {
                    "expected_icon_id": current,
                    "replacement_icon_id": fallback["replacement_icon_id"],
                    "source": fallback["reason"],
                }
                break
    return repairs


def package_numeric_names(path: Path) -> set[int]:
    """Read numeric FName entries from a UE package name table in one pass."""
    data = path.read_bytes()
    names: set[int] = set()
    for match in NUMERIC_NAME.finditer(data):
        token = match.group(1)
        start = match.start(1)
        # UE3 ANSI FString entries store (character count + NUL) immediately
        # before the text; this filters accidental numeric matches in bulk data.
        if start >= 4 and struct.unpack_from("<i", data, start - 4)[0] == len(token) + 1:
            names.add(int(token))
    return names


def get_catalog_paths(root: Path) -> tuple[Path, Path]:
    candidates = (
        root / "TGame" / "CookedPC" / "Config",
        root / "CookedPC" / "Config",
    )
    for directory in candidates:
        item = directory / "DefaultItemLibrary.ini"
        commodity = directory / "DefaultCommodityLibrary.ini"
        if item.is_file():
            return item, commodity
    raise FileNotFoundError("Could not find Config\\DefaultItemLibrary.ini; use --catalog")


def find_assets(root: Path, extra: list[Path]) -> list[Path]:
    candidates = [
        root / "TGame" / "CookedPC" / name
        for name in (
            "TG_Shop.upk", "TGUI_Texture.upk", "TGUI_Movies.upk",
            "TG_TeamBadge.upk", "TG_TeamBadgeAvatar.upk", "TG_RankIcons.upk",
            "UI_SpecialAwardIcon.upk", "Avatar_All.upk", "DefaultAvatar.upk",
        )
    ]
    candidates += [
        root / "CookedPC" / name
        for name in ("TG_Shop.upk", "TGUI_Texture.upk", "TGUI_Movies.upk")
    ]
    result = []
    for path in [*candidates, *extra]:
        path = path.resolve()
        if path.is_file() and path not in result:
            result.append(path)
    if not result:
        raise FileNotFoundError("No icon packages found; pass one or more --asset-package paths")
    return result


def apply_dynamic_item_repairs(
    raw: bytes,
    repairs: dict[int, dict[str, object]],
    asset_ids: set[int],
) -> tuple[bytes, list[str]]:
    """Apply generated repairs after checking every target still matches its source value."""
    text, encoding, bom = decode_text(raw)
    changes = []
    seen = set()
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "RawItemDatas=(" not in line:
            continue
        match = ITEM_ROW.search(line.encode("gbk", errors="replace"))
        if not match:
            continue
        data = fields(match.group(1))
        if "nItemID" not in data:
            continue
        item_id = int(data["nItemID"])
        repair = repairs.get(item_id)
        if repair is None or "nItemIconID" not in data:
            continue
        seen.add(item_id)
        old_icon = int(repair["expected_icon_id"])
        new_icon = int(repair["replacement_icon_id"])
        icon_id = int(data["nItemIconID"])
        if icon_id == new_icon:
            continue
        if icon_id != old_icon:
            raise ValueError(
                f"Item {item_id} now has icon {icon_id}; expected {old_icon} or already-fixed "
                f"{new_icon}. Refusing to overwrite local edits."
            )
        if new_icon not in asset_ids:
            raise ValueError(f"Replacement icon {new_icon} was not found in scanned packages")
        new_line = re.sub(
            rf"(\bnItemIconID=){old_icon}\b",
            rf"\g<1>{new_icon}",
            line,
            count=1,
        )
        if new_line == line:
            raise ValueError(f"Could not replace icon field for item {item_id}")
        lines[index] = new_line
        changes.append(f"item {item_id}: {old_icon} -> {new_icon} ({repair['source']})")
    if not changes:
        return raw, []
    missing_repairs = set(repairs) - seen
    if missing_repairs:
        raise ValueError(f"Expected item rows are missing for repairs: {sorted(missing_repairs)}")
    return bom + "".join(lines).encode(encoding), changes


def align_direct_commodity_icons(
    raw: bytes,
    repairs: dict[int, dict[str, object]],
    asset_ids: set[int],
) -> tuple[bytes, list[str]]:
    """Align direct commodity rows that still copy the replaced item icon."""
    text, encoding, bom = decode_text(raw)
    changed = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "RawCommodityDatas=(" not in line:
            continue
        match = COMMODITY_ROW.search(line.encode("gbk", errors="replace"))
        if not match:
            continue
        row = match.group(1)
        data = fields(row)
        if "IconID" not in data:
            continue
        item_array = re.search(rb"\bnItemIDArray=\(([^)]*)\)", row)
        item_ids = [int(value) for value in re.findall(rb"\d+", item_array.group(1))] if item_array else []
        if len(item_ids) != 1 or item_ids[0] not in repairs:
            continue
        repair = repairs[item_ids[0]]
        old_icon = int(repair["expected_icon_id"])
        new_icon = int(repair["replacement_icon_id"])
        icon_id = int(data["IconID"])
        if icon_id == old_icon and new_icon in asset_ids:
            new_line = re.sub(
                rf"(\bIconID=){old_icon}\b",
                rf"\g<1>{new_icon}",
                line,
                count=1,
            )
            if new_line != line:
                lines[index] = new_line
                commodity_id = data.get("nCommodityID", "?")
                changed.append(
                    f"commodity {commodity_id} (item {item_ids[0]}): "
                    f"{old_icon} -> {new_icon}"
                )
    if not changed:
        return raw, []
    return bom + "".join(lines).encode(encoding), changed


def write_audit(
    item_raw: bytes,
    commodity_raw: bytes | None,
    package_names: dict[str, set[int]],
    repairs: dict[int, dict[str, object]],
    report: Path,
) -> tuple[int, int, int, int, int, int, int, int]:
    items = parse_items(item_raw)
    commodities = parse_commodity_rows(commodity_raw) if commodity_raw else []
    direct = parse_direct_commodities(commodity_raw) if commodity_raw else {}
    items_by_id = {int(item["item_id"]): item for item in items}
    all_asset_ids = set().union(*package_names.values()) if package_names else set()
    fields_out = [
        "catalog_type", "record_id", "name", "icon_id", "item_ids",
        "related_icon_ids", "icon_resource_status", "packages_with_icon",
        "mapping_status", "suggested_icon_id", "repair_source",
    ]
    missing_icon_field_rows = 0
    with report.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields_out)
        writer.writeheader()

        def emit(record_type, record_id, name, icon_id, item_ids, related_icon_ids,
                 mapping_status, suggestion=None):
            nonlocal missing_icon_field_rows
            if icon_id is None:
                resource_status = "NO_ICON_FIELD"
                packages = ""
                missing_icon_field_rows += 1
            elif int(icon_id) == 0:
                resource_status = "ZERO_ICON_ID"
                packages = ""
            else:
                icon = int(icon_id)
                resource_status = "FOUND" if icon in all_asset_ids else "NOT_FOUND_IN_SCANNED_PACKAGES"
                packages = ";".join(
                    Path(filename).name
                    for filename, ids in package_names.items()
                    if icon in ids
                )
            repair = suggestion or {}
            writer.writerow({
                "catalog_type": record_type,
                "record_id": record_id,
                "name": name,
                "icon_id": "" if icon_id is None else icon_id,
                "item_ids": ";".join(map(str, item_ids)),
                "related_icon_ids": ";".join(map(str, sorted(set(related_icon_ids)))),
                "icon_resource_status": resource_status,
                "packages_with_icon": packages,
                "mapping_status": mapping_status,
                "suggested_icon_id": repair.get("replacement_icon_id", ""),
                "repair_source": repair.get("source", ""),
            })

        item_icon_ids: dict[int, int | None] = {}
        for item in items:
            item_id = int(item["item_id"])
            raw_icon_id = item["icon_id"]
            icon_id = int(raw_icon_id) if raw_icon_id is not None else None
            item_icon_ids[item_id] = icon_id
            direct_ids = sorted(direct.get(item_id, set()))
            if icon_id is None:
                status = "NO_ICON_FIELD"
            elif icon_id == 0:
                status = "ZERO_ICON_ID"
            elif not direct_ids:
                status = "NO_DIRECT_COMMODITY_CROSSCHECK"
            elif icon_id in direct_ids:
                status = "DIRECT_COMMODITY_ICON_MATCH"
            else:
                status = "DIRECT_COMMODITY_ICON_DIFFERS"
            emit("ITEM", item_id, item["item_name"], icon_id, [item_id], direct_ids, status, repairs.get(item_id))

        for commodity in commodities:
            item_ids = [int(value) for value in commodity["item_ids"]]
            related_icons = [item_icon_ids[item_id] for item_id in item_ids
                             if item_id in item_icon_ids and item_icon_ids[item_id] not in (None, 0)]
            if len(item_ids) > 1:
                status = "BUNDLE"
            elif not item_ids:
                status = "NO_ITEM_LINK"
            elif item_ids[0] not in items_by_id:
                status = "ITEM_NOT_IN_ITEM_CATALOG"
            else:
                item_icon = item_icon_ids.get(item_ids[0])
                if item_icon is None:
                    status = "ITEM_HAS_NO_ICON_FIELD"
                elif int(commodity["icon_id"]) == item_icon:
                    status = "SINGLE_ITEM_ICON_MATCH"
                else:
                    status = "SINGLE_ITEM_ICON_DIFFERS"
            suggestion = None
            if len(item_ids) == 1 and item_ids[0] in repairs:
                repair = repairs[item_ids[0]]
                if int(commodity["icon_id"]) == int(repair["expected_icon_id"]):
                    suggestion = repair
            emit(
                "COMMODITY", commodity["commodity_id"], commodity["commodity_name"],
                commodity["icon_id"], item_ids, related_icons, status, suggestion,
            )

    item_nonzero = {int(row["icon_id"]) for row in items if row["icon_id"] not in (None, 0)}
    item_missing = len(item_nonzero - all_asset_ids)
    commodity_nonzero = {int(row["icon_id"]) for row in commodities if int(row["icon_id"]) != 0}
    commodity_missing = len(commodity_nonzero - all_asset_ids)
    matched = sum(1 for item in items if
                  item["icon_id"] not in (None, 0) and direct.get(int(item["item_id"])) and
                  int(item["icon_id"]) in direct[int(item["item_id"])])
    differs = sum(1 for item in items if
                  item["icon_id"] not in (None, 0) and direct.get(int(item["item_id"])) and
                  int(item["icon_id"]) not in direct[int(item["item_id"])])
    return len(items), len(commodities), item_missing, commodity_missing, matched, differs, len(repairs), missing_icon_field_rows


def atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as out:
            temporary = out.name
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, help="Override DefaultItemLibrary.ini path")
    parser.add_argument("--commodities", type=Path, help="Override DefaultCommodityLibrary.ini path")
    parser.add_argument("--asset-package", type=Path, action="append", default=[],
                        help="Extra .upk to scan; may be repeated")
    parser.add_argument("--report", type=Path, default=Path("item_icon_audit.csv"))
    parser.add_argument("--audit-only", action="store_true", help="Write the audit without previewing fixes")
    parser.add_argument("--apply", action="store_true", help="Apply data-derived safe repairs and the reviewed backpack fallback")
    args = parser.parse_args()

    if args.catalog:
        catalog = args.catalog.resolve()
        inferred_commodity = catalog.with_name("DefaultCommodityLibrary.ini")
        commodity = (args.commodities.resolve() if args.commodities else
                     inferred_commodity if inferred_commodity.is_file() else None)
    else:
        catalog, commodity = get_catalog_paths(args.game_root.resolve())
        if args.commodities:
            commodity = args.commodities.resolve()
    if not catalog.is_file():
        raise FileNotFoundError(f"Item catalog not found: {catalog}")
    if commodity is not None and not commodity.is_file():
        commodity = None
    packages = find_assets(args.game_root.resolve(), args.asset_package)
    package_names = {str(path): package_numeric_names(path) for path in packages}
    asset_ids = set().union(*package_names.values())
    original_blob = catalog.read_bytes()
    raw = decrypt_catalog(original_blob)
    commodity_raw = decrypt_catalog(commodity.read_bytes()) if commodity else None
    repairs = derive_icon_repairs(raw, commodity_raw, asset_ids)
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    item_rows, commodity_rows, item_missing, commodity_missing, direct_matches, direct_differs, repair_count, no_icon_field_rows = write_audit(
        raw,
        commodity_raw,
        package_names,
        repairs,
        report,
    )
    print(f"[ITEM-ICON] RawItemDatas rows audited: {item_rows}")
    print(f"[ITEM-ICON] RawCommodityDatas Mall rows audited: {commodity_rows}")
    print(f"[ITEM-ICON] Item icon IDs absent from scanned packages: {item_missing}")
    print(f"[ITEM-ICON] Mall commodity icon IDs absent from scanned packages: {commodity_missing}")
    print(f"[ITEM-ICON] Direct item/commodity matches: {direct_matches}; differing IDs: {direct_differs}")
    print(f"[ITEM-ICON] Safe repair candidates: {repair_count}")
    print(f"[ITEM-ICON] Rows without an icon field (usually model parts): {no_icon_field_rows}")
    print(f"[ITEM-ICON] Audit CSV: {report}")
    print(f"[ITEM-ICON] Packages scanned: {len(package_names)}")

    if args.audit_only:
        return 0
    if commodity is None:
        raise FileNotFoundError(
            "DefaultCommodityLibrary.ini is required to apply coordinated icon fixes; "
            "pass --commodities"
        )
    changed_raw, changes = apply_dynamic_item_repairs(raw, repairs, asset_ids)
    changed_commodity_raw, commodity_changes = align_direct_commodity_icons(
        commodity_raw, repairs, asset_ids
    )
    all_changes = changes + commodity_changes
    if not all_changes:
        print("[ITEM-ICON] No safe repairs remain; catalog is already corrected or needs a reviewed mapping")
        return 0
    for change in all_changes:
        print(f"[ITEM-ICON] Planned: {change}")
    if not args.apply:
        print("[ITEM-ICON] Preview only; rerun with --apply to write both catalogs")
        return 0

    encoded = encrypt_catalog(changed_raw)
    encoded_commodity = encrypt_catalog(changed_commodity_raw)
    if decrypt_catalog(encoded) != changed_raw or decrypt_catalog(encoded_commodity) != changed_commodity_raw:
        raise ValueError("Encrypted catalog failed round-trip verification")
    original_item_blob = catalog.read_bytes()
    original_commodity_blob = commodity.read_bytes()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    item_backup = catalog.with_name(catalog.name + ".backup_" + timestamp)
    commodity_backup = commodity.with_name(commodity.name + ".backup_" + timestamp)
    shutil.copy2(catalog, item_backup)
    shutil.copy2(commodity, commodity_backup)
    try:
        atomic_write(catalog, encoded)
        atomic_write(commodity, encoded_commodity)
        if (decrypt_catalog(catalog.read_bytes()) != changed_raw or
                decrypt_catalog(commodity.read_bytes()) != changed_commodity_raw):
            raise ValueError("Written catalogs failed verification")
    except Exception:
        atomic_write(catalog, original_item_blob)
        atomic_write(commodity, original_commodity_blob)
        raise
    print(f"[ITEM-ICON] Applied and verified. Backups: {item_backup}, {commodity_backup}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ITEM-ICON] ERROR: {exc}")
        raise SystemExit(1)
