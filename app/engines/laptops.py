"""Laptop identification: turn a messy title into a comparable-pricing key.

A GPU is one standardised part, so "RTX 3080" is one price band. A laptop is a
bundle, so two "ThinkPad" listings can differ 2x. Rather than a full spec
database, this extracts just enough to group *comparable* machines:

* **Gaming laptops** (a dedicated RTX/GTX GPU) are keyed by that GPU alone —
  ``Laptop RTX 4060`` — because the GPU dominates their price and sellers
  describe it reliably.
* **Apple laptops** are keyed by model and chip — ``MacBook Pro M2 Pro``.
* **Everything else** is keyed by family + CPU class + RAM bucket —
  ``Lenovo ThinkPad i7 16GB``.

A title that doesn't give enough to place it (no family, or no CPU/GPU/chip) is
left unresolved rather than guessed: a wrong key produces a wrong "deal".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.models.schema import CanonicalProduct

_LAPTOP_WORDS = re.compile(
    r"\b(laptop|notebook|ultrabook|macbook|chromebook|thinkpad|ideapad|"
    r"thinkbook|yoga|zenbook|vivobook|zephyrus|predator helios|"
    r"predator triton|nitro 5|nitro v ?1\d|aspire|inspiron|"
    r"latitude|xps|alienware|precision|vostro|pavilion|spectre|envy|"
    r"elitebook|probook|zbook|omen|victus|legion|razer blade|surface laptop|"
    r"surface book|surface pro|lg gram)\b", re.IGNORECASE)
# NB: "ROG Strix", "TUF Gaming" and "Nitro+" are also *graphics-card* lines, so
# they only count as laptops when the title says laptop/notebook (above).

LAPTOP_WORDS = _LAPTOP_WORDS          # public alias for the source adapters

# Not a laptop: desktops that share a brand name, or things sold for a laptop.
_NOT_A_LAPTOP = re.compile(
    r"\b(tower|desktop|all[- ]in[- ]one|aio|aurora|gaming pc|pc case|sff|"
    r"mini pc|bag|sleeve|charger|adapter|adaptor|palmrest|hinge|motherboard|"
    r"mainboard|dock|docking|replacement|"
    r"laptop (stand|case|cooler|skin|cover|screen|keyboard|battery|fan))\b",
    re.IGNORECASE)

# Order matters: the first match names the family.
_FAMILIES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), name) for p, name in [
    (r"macbook\s*air", "MacBook Air"), (r"macbook\s*pro", "MacBook Pro"),
    (r"macbook", "MacBook"),
    (r"thinkpad", "Lenovo ThinkPad"), (r"thinkbook", "Lenovo ThinkBook"),
    (r"legion", "Lenovo Legion"), (r"ideapad", "Lenovo IdeaPad"),
    (r"yoga", "Lenovo Yoga"),
    (r"\bxps\b", "Dell XPS"), (r"latitude", "Dell Latitude"),
    (r"inspiron", "Dell Inspiron"), (r"precision", "Dell Precision"),
    (r"vostro", "Dell Vostro"), (r"alienware", "Alienware"),
    (r"zephyrus|rog\s*(strix|flow)", "ASUS ROG"), (r"tuf", "ASUS TUF"),
    (r"zenbook", "ASUS Zenbook"), (r"vivobook", "ASUS Vivobook"),
    (r"spectre", "HP Spectre"), (r"envy", "HP Envy"),
    (r"elitebook", "HP EliteBook"), (r"probook", "HP ProBook"),
    (r"zbook", "HP ZBook"), (r"omen|victus", "HP Omen"),
    (r"pavilion", "HP Pavilion"),
    (r"predator", "Acer Predator"), (r"nitro", "Acer Nitro"),
    (r"aspire", "Acer Aspire"), (r"swift", "Acer Swift"),
    (r"razer\s*blade", "Razer Blade"), (r"surface", "Microsoft Surface"),
    (r"\bgram\b", "LG Gram"), (r"chromebook", "Chromebook"),
]]
_BRANDS: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), name) for p, name in [
    (r"\bdell\b", "Dell"), (r"\bhp\b", "HP"), (r"\blenovo\b", "Lenovo"),
    (r"\basus\b", "ASUS"), (r"\bacer\b", "Acer"), (r"\bmsi\b", "MSI"),
    (r"\brazer\b", "Razer"), (r"\bgigabyte\b", "Gigabyte"),
    (r"\bsamsung\b", "Samsung"), (r"\bmicrosoft\b", "Microsoft"),
]]

_GPU = re.compile(r"\b(rtx|gtx)\s*-?\s*(\d{3,4})\s*(ti|super)?\b", re.IGNORECASE)
_APPLE_CHIP = re.compile(r"\bm([1-5])\b\s*(pro|max|ultra)?", re.IGNORECASE)
_INTEL = re.compile(r"\bi([3579])\b(?:[-\s]?\d{4,5}[a-z]{0,2})?", re.IGNORECASE)
_RYZEN = re.compile(r"\bryzen\s*([3579])\b", re.IGNORECASE)

_STORAGE_TAG = r"(?:ssd|nvme|hdd|m\.?2|pcie|storage|emmc|flash)"
_STORAGE = re.compile(
    rf"(\d{{1,4}})\s*(gb|tb)\s*(?:{_STORAGE_TAG})|(?:{_STORAGE_TAG})\s*(\d{{1,4}})\s*(gb|tb)",
    re.IGNORECASE)
_RAM_TAGGED = re.compile(
    r"(\d{1,3})\s*gb\s*(?:of\s*)?(?:ddr\d?x?|lpddr\d?x?|ram|memory|unified)",
    re.IGNORECASE)
_RAM_SIZES = {4, 8, 12, 16, 18, 24, 32, 36, 48, 64, 96, 128}
_GB_BARE = re.compile(r"(\d{1,3})\s*gb\b", re.IGNORECASE)


@dataclass(frozen=True)
class LaptopInfo:
    key: str
    family: str
    cpu: str = ""
    gpu: str = ""
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None


def extract_ram_gb(title: str) -> Optional[int]:
    """RAM in GB: an explicitly tagged size, else a bare size that isn't storage."""
    tagged = _RAM_TAGGED.search(title)
    if tagged and int(tagged.group(1)) in _RAM_SIZES:
        return int(tagged.group(1))
    storage_spans = [m.span() for m in _STORAGE.finditer(title)]
    for m in _GB_BARE.finditer(title):
        size = int(m.group(1))
        if size in _RAM_SIZES and not any(a <= m.start() < b for a, b in storage_spans):
            return size
    return None


def extract_storage_gb(title: str) -> Optional[int]:
    m = _STORAGE.search(title)
    if not m:
        return None
    amount = int(m.group(1) or m.group(3))
    unit = (m.group(2) or m.group(4)).lower()
    return amount * 1000 if unit == "tb" else amount


def _ram_bucket(ram: Optional[int]) -> str:
    if ram is None:
        return ""
    if ram <= 8:
        return "8GB"
    if ram <= 16:
        return "16GB"
    return "32GB+"


def _family(title: str) -> str:
    for pattern, name in _FAMILIES:
        if pattern.search(title):
            return name
    for pattern, name in _BRANDS:
        if pattern.search(title):
            return f"{name} laptop"
    return ""


def parse_laptop(title: str) -> Optional[LaptopInfo]:
    """A :class:`LaptopInfo` for a laptop title, or None if it isn't one we can place."""
    if not title or not _LAPTOP_WORDS.search(title) or _NOT_A_LAPTOP.search(title):
        return None

    family = _family(title)
    ram = extract_ram_gb(title)
    storage = extract_storage_gb(title)

    gpu_match = _GPU.search(title)
    gpu = ""
    if gpu_match:
        suffix = (gpu_match.group(3) or "").title()
        gpu = f"{gpu_match.group(1).upper()} {gpu_match.group(2)}" + (
            f" {suffix}" if suffix else "")

    # Apple: model + chip.
    if family.startswith("MacBook"):
        chip = _APPLE_CHIP.search(title)
        if chip:
            cpu = f"M{chip.group(1)}" + (f" {chip.group(2).title()}" if chip.group(2) else "")
            return LaptopInfo(f"{family} {cpu}".replace("MacBook MacBook", "MacBook"),
                              family, cpu, "", ram, storage)
        if re.search(r"\b(intel|core i[3579]|i[3579])\b", title, re.I):
            return LaptopInfo(f"{family} Intel", family, "Intel", "", ram, storage)
        return None

    # Gaming laptop: a dedicated GPU defines the price band.
    if gpu:
        return LaptopInfo(f"Laptop {gpu}", family, "", gpu, ram, storage)

    cpu = ""
    if (m := _INTEL.search(title)):
        cpu = f"i{m.group(1)}"
    elif (m := _RYZEN.search(title)):
        cpu = f"R{m.group(1)}"
    if not family or not cpu:
        return None
    bucket = _ram_bucket(ram)
    key = " ".join(part for part in (family, cpu, bucket) if part)
    return LaptopInfo(key, family, cpu, "", ram, storage)


def resolve_laptop(title: str) -> Optional[CanonicalProduct]:
    """A laptop :class:`CanonicalProduct` for *title*, or None if not a placeable laptop."""
    info = parse_laptop(title)
    if info is None:
        return None
    return CanonicalProduct(standard_name=info.key, category="laptop",
                            resolved_from=title)
