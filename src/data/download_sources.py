from __future__ import annotations

import argparse
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import pandas as pd


ALLOWED_FORMATS = {
    "csv", "zip", "xlsx", "xls", "json", "geojson", "xml", "txt", "kmz", "kml"
}

MONTHS = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}

QUARTERS = {
    ("enero", "marzo"): "q1",
    ("abril", "junio"): "q2",
    ("julio", "septiembre"): "q3",
    ("julio", "setiembre"): "q3",
    ("octubre", "diciembre"): "q4",
}


class LinkExtractor(HTMLParser):
    """Extractor simple de enlaces HTML: href + texto visible."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            clean = data.strip()
            if clean:
                self._text.append(clean)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None
            self._text = []


def fetch_html(url: str, timeout: int = 60) -> str:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 TFM-parking-madrid downloader"},
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_downloads_url(url: str) -> str:
    url = str(url).strip()
    if url.endswith("/information"):
        return url.rsplit("/information", 1)[0] + "/downloads"
    if url.endswith("/downloads"):
        return url
    return url.rstrip("/") + "/downloads"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def detect_years(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"\b20\d{2}\b", str(text))}


def parse_period_years(period: str) -> tuple[int | None, int | None]:
    years = sorted(detect_years(str(period)))
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], years[0]
    return years[0], years[-1]


def parse_preferred_formats(value: str) -> set[str]:
    return {x.strip().lower() for x in str(value).split(",") if x.strip()}


def detect_format(resource_text: str) -> str:
    lower = clean_text(resource_text).lower()

    # Orden importante: geojson antes que json.
    ordered_formats = [
        "geojson", "csv", "zip", "xlsx", "xls", "json", "xml", "txt", "kmz", "kml"
    ]

    for fmt in ordered_formats:
        if re.search(rf"\b{fmt}\b", lower):
            return fmt

    return ""


def raw_folder_for_dataset(bloque: str, dataset_id: str) -> str:
    base = {
        "SER": "data/raw/ser",
        "EMT": "data/raw/emt",
        "contexto": "data/raw/contexto",
        "clima": "data/raw/clima",
        "trafico": "data/raw/trafico",
    }.get(str(bloque), "data/raw")

    return str(Path(base) / str(dataset_id))


def infer_period_slug(text: str, target_period: str) -> str:
    lower = clean_text(text).lower()
    years = sorted(detect_years(lower))
    year = str(years[0]) if years else ""

    for (start, end), quarter in QUARTERS.items():
        if start in lower and end in lower:
            return f"{year}_{quarter}" if year else quarter

    if "enero" in lower and "diciembre" in lower:
        return f"{year}_annual" if year else "annual"

    for month_name, month_num in MONTHS.items():
        if re.search(rf"\b{month_name}\b", lower):
            return f"{year}_{month_num}" if year else month_num

    if years:
        if len(years) >= 2:
            return f"{years[0]}_{years[-1]}"
        return str(years[0])

    target = str(target_period).strip().lower()
    if target and target != "nan":
        return target.replace("-", "_").replace(" ", "_")

    return "actual"


def decide_selection(
    dataset_id: str,
    fmt: str,
    resource_text: str,
    target_period: str,
    preferred_formats: set[str],
) -> tuple[bool, str]:
    text = clean_text(resource_text).lower()

    if fmt not in ALLOWED_FORMATS:
        return False, "formato_no_datos"

    if fmt not in preferred_formats:
        return False, f"formato_no_preferido_{fmt}"

    start_year, end_year = parse_period_years(target_period)
    resource_years = detect_years(text)

    if start_year is not None and end_year is not None:
        if resource_years and not any(start_year <= y <= end_year for y in resource_years):
            return False, "anio_fuera_periodo_objetivo"

        if not resource_years:
            return True, "sin_anio_detectado_revisar"

    # Evita duplicar SER tiques 2024: se prefieren los cuatro trimestres frente al anual.
    if dataset_id == "ser_tiques" and "enero" in text and "diciembre" in text:
        return False, "excluido_zip_anual_para_evitar_duplicado"

    return True, "seleccionado"


def suggested_filename(dataset_id: str, fmt: str, resource_text: str, target_period: str) -> str:
    period = infer_period_slug(resource_text, target_period)
    return f"{dataset_id}__{period}.{fmt}"


def make_unique_filenames(df: pd.DataFrame) -> pd.DataFrame:
    seen: dict[str, int] = {}
    new_names = []

    for name in df["suggested_filename"].fillna("").astype(str):
        if not name:
            new_names.append(name)
            continue

        count = seen.get(name, 0)
        seen[name] = count + 1

        if count == 0:
            new_names.append(name)
        else:
            path = Path(name)
            new_names.append(f"{path.stem}__{count + 1}{path.suffix}")

    df = df.copy()
    df["suggested_filename"] = new_names
    return df


def extract_download_resources(downloads_url: str) -> list[dict]:
    html = fetch_html(downloads_url)
    parser = LinkExtractor()
    parser.feed(html)

    rows = []
    current_resource: dict | None = None

    for href, text in parser.links:
        absolute_url = urljoin(downloads_url, href)
        path = urlparse(absolute_url).path

        # Página de recurso: contiene formato y descripción.
        if "/resource/" in path and "/download/" not in path:
            resource_text = clean_text(text)
            fmt = detect_format(resource_text)

            if fmt:
                current_resource = {
                    "resource_text": resource_text,
                    "format": fmt,
                    "resource_page_url": absolute_url,
                }
            else:
                # Algunos recursos tienen enlaces intermedios "Tabla".
                # No reseteamos current_resource para no perder el recurso detectado.
                continue

        # Enlace real de descarga asociado al último recurso detectado.
        elif "/download/" in path and current_resource is not None:
            rows.append(
                {
                    "resource_text": current_resource["resource_text"],
                    "format": current_resource["format"],
                    "resource_page_url": current_resource["resource_page_url"],
                    "resource_url": absolute_url,
                }
            )
            current_resource = None

    return rows


def build_manifest(catalog_path: Path, manifest_path: Path) -> pd.DataFrame:
    catalog = pd.read_csv(catalog_path)

    required = {
        "dataset_id",
        "bloque",
        "tipo_acceso",
        "url_fuente",
        "periodo_dato_objetivo",
        "formato_preferido",
    }

    missing = required - set(catalog.columns)
    if missing:
        raise ValueError(f"Faltan columnas en data_catalog.csv: {sorted(missing)}")

    sources = catalog[
        catalog["tipo_acceso"].astype(str).str.lower().isin(["script", "manual"])
    ].copy()

    rows = []

    for _, src in sources.iterrows():
        dataset_id = src["dataset_id"]
        downloads_url = normalize_downloads_url(src["url_fuente"])
        preferred_formats = parse_preferred_formats(src["formato_preferido"])
        target_period = str(src["periodo_dato_objetivo"])

        print(f"[INFO] Explorando {dataset_id}: {downloads_url}")

        try:
            resources = extract_download_resources(downloads_url)
        except (HTTPError, URLError, TimeoutError) as exc:
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "bloque": src["bloque"],
                    "tipo_acceso": src["tipo_acceso"],
                    "downloads_url": downloads_url,
                    "resource_text": "",
                    "format": "",
                    "preferred_formats": ",".join(sorted(preferred_formats)),
                    "resource_url": "",
                    "selected": False,
                    "selection_reason": f"error_fetch: {exc}",
                    "target_folder": raw_folder_for_dataset(src["bloque"], dataset_id),
                    "suggested_filename": "",
                    "download_status": "not_downloaded",
                }
            )
            continue

        for resource in resources:
            fmt = resource["format"]

            selected, reason = decide_selection(
                dataset_id=dataset_id,
                fmt=fmt,
                resource_text=resource["resource_text"],
                target_period=target_period,
                preferred_formats=preferred_formats,
            )

            rows.append(
                {
                    "dataset_id": dataset_id,
                    "bloque": src["bloque"],
                    "tipo_acceso": src["tipo_acceso"],
                    "downloads_url": downloads_url,
                    "resource_text": resource["resource_text"],
                    "format": fmt,
                    "preferred_formats": ",".join(sorted(preferred_formats)),
                    "resource_url": resource["resource_url"],
                    "selected": selected,
                    "selection_reason": reason,
                    "target_folder": raw_folder_for_dataset(src["bloque"], dataset_id),
                    "suggested_filename": suggested_filename(
                        dataset_id=dataset_id,
                        fmt=fmt,
                        resource_text=resource["resource_text"],
                        target_period=target_period,
                    ),
                    "download_status": "not_downloaded",
                }
            )

        if not resources:
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "bloque": src["bloque"],
                    "tipo_acceso": src["tipo_acceso"],
                    "downloads_url": downloads_url,
                    "resource_text": "",
                    "format": "",
                    "preferred_formats": ",".join(sorted(preferred_formats)),
                    "resource_url": "",
                    "selected": False,
                    "selection_reason": "no_resources_detected",
                    "target_folder": raw_folder_for_dataset(src["bloque"], dataset_id),
                    "suggested_filename": "",
                    "download_status": "not_downloaded",
                }
            )

        time.sleep(0.5)

    manifest = pd.DataFrame(rows)
    manifest = make_unique_filenames(manifest)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, index=False)

    print(f"[OK] Manifest creado: {manifest_path}")
    print(f"[OK] Recursos detectados: {len(manifest)}")
    print(f"[OK] Recursos seleccionados: {int(manifest['selected'].sum())}")

    return manifest


def download_file(url: str, output_path: Path, timeout: int = 300) -> None:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 TFM-parking-madrid downloader"},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with urlopen(req, timeout=timeout) as response:
        with open(output_path, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)


def download_from_manifest(manifest_path: Path) -> pd.DataFrame:
    manifest = pd.read_csv(manifest_path)

    selected = manifest[
        manifest["selected"].astype(str).str.lower().isin(["true", "1", "yes", "sí", "si"])
        & manifest["resource_url"].notna()
        & manifest["resource_url"].astype(str).ne("")
    ].copy()

    print(f"[INFO] Recursos seleccionados para descarga: {len(selected)}")

    for idx, row in selected.iterrows():
        out = Path(row["target_folder"]) / row["suggested_filename"]

        try:
            print(f"[DOWNLOADING] {row['dataset_id']} -> {out}")
            download_file(row["resource_url"], out)
            manifest.loc[idx, "download_status"] = "downloaded"
        except Exception as exc:
            manifest.loc[idx, "download_status"] = f"error: {exc}"

        time.sleep(0.5)

    manifest.to_csv(manifest_path, index=False)
    print(f"[OK] Manifest actualizado: {manifest_path}")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build manifest and download source files.")
    parser.add_argument("--catalog", default="data_catalog.csv")
    parser.add_argument("--manifest", default="reports/tables/download_manifest.csv")
    parser.add_argument("--build-manifest", action="store_true")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    manifest_path = Path(args.manifest)

    if args.build_manifest:
        manifest = build_manifest(catalog_path, manifest_path)
        cols = [
            "dataset_id",
            "tipo_acceso",
            "format",
            "preferred_formats",
            "selected",
            "selection_reason",
            "suggested_filename",
        ]
        print(manifest[cols].to_string(index=False))

    if args.download:
        download_from_manifest(manifest_path)

    if not args.build_manifest and not args.download:
        print("Uso:")
        print("  python src/data/download_sources.py --build-manifest")
        print("  python src/data/download_sources.py --download")


if __name__ == "__main__":
    main()
