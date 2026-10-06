# Item and Mall icon audit

`repair_item_icon_catalog.py` reads the encrypted item and commodity catalogs,
scans Unreal package name tables, and writes one CSV covering every catalog
row. It derives routine repairs from the catalogs and scanned assets instead
of keeping a fixed list of item IDs.

The supplied files contain 563 `RawItemDatas` rows and 517
`RawCommodityDatas` Mall rows, for 1,080 audit rows. The item file has 511
`nItemIconID` fields and 52 model-part rows without that field. The attached
commodity file adds 93 GP rows over its 424-row `.bak` copy. Among 433
single-item commodity rows linked to items in `DefaultItemLibrary.ini`, 432
share the item icon ID and one differs.

## Repair rules

For each item whose icon is zero or absent from scanned packages, the tool
checks its single-item commodity rows. It proposes a data-derived repair only
when those rows agree on one nonzero icon ID and that icon exists in a scanned
package. Bundles are excluded. Existing item icons are left alone even if a
shop commodity uses another icon, since shop artwork can intentionally differ
from inventory artwork.

The supplied catalogs produce one data-derived repair: `SCAR_PHL` item
`110004` points to missing icon `3010004`, while its unique direct commodity
uses available icon `3010006`.

The default backpack needs one reviewed fallback. Both its item row and direct
commodity row point to missing icon `1000006`, so those catalogs cannot infer a
replacement from one another. The available stock image `3000026` is the No. 3
Backpack graphic. The tool updates both references to it; the image shows a
green “3”.

The three supplied shop/UI packages have 45 distinct Mall icon IDs that are
not found in their name tables. Forty belong to badge borders, backgrounds,
and insignia (`3000032`–`3000061` and `3000221`–`3000230`); these may be in the
separate badge packages. The other five are on newly added GP Mall rows. The
normal game-root scan also checks `TG_TeamBadge.upk`, `TG_TeamBadgeAvatar.upk`,
`TG_RankIcons.upk`, and other known UI packages. Missing from the three
uploaded packages does not prove the installed client lacks an icon.

The CSV reports icon presence and item/commodity relationships for all 1,080
rows. It does not visually confirm every rendered texture. Rows without an
icon field and bundles are labeled separately.

## Test the change

Preview against the installed catalogs and generate the audit CSV:

```powershell
py -3.12 .\tools\patches\repair_item_icon_catalog.py --game-root "D:\AssaultFirePH - Copy"
```

To audit an alternate commodity catalog such as the attached patched copy,
pass it explicitly:

```powershell
py -3.12 .\tools\patches\repair_item_icon_catalog.py --game-root "D:\AssaultFirePH - Copy" --commodities "D:\AssaultFirePH - Copy\TGame\CookedPC\Config\DefaultCommodityLibrary(4).ini" --audit-only
```

Apply after checking the preview:

```powershell
py -3.12 .\tools\patches\repair_item_icon_catalog.py --game-root "D:\AssaultFirePH - Copy" --apply
```

The tool makes timestamped backups of both encrypted catalogs and restores
the originals if either write fails verification. Restart `TGame` to reload
the catalogs. Python package `cryptography` is required.
