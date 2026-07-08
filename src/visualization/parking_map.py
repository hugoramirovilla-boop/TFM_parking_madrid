from __future__ import annotations

import os
import re
import unicodedata
from html import escape
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from textwrap import wrap
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/private/tmp")

import geopandas as gpd
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import pandas as pd
import pyproj
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import box

try:
    import folium
    from folium.plugins import MarkerCluster, Search
except ImportError as exc:  # pragma: no cover - validated in project env
    raise ImportError(
        "Folium is required to build the interactive SER + EMT map."
    ) from exc


TARGET_CRS = "EPSG:25830"
WEB_CRS = "EPSG:4326"

SER_INPUT_PATHS = {
    "ser_geoportal_limite_ser": Path(
        "data/interim/cartografia/ser_geoportal_limite_ser/"
        "ser_geoportal_limite_ser_clean.parquet"
    ),
    "ser_geoportal_barrios_ser": Path(
        "data/interim/cartografia/ser_geoportal_barrios_ser/"
        "ser_geoportal_barrios_ser_clean.parquet"
    ),
    "ser_geoportal_bandas_aparcamiento": Path(
        "data/interim/cartografia/ser_geoportal_bandas_aparcamiento/"
        "ser_geoportal_bandas_aparcamiento_clean.parquet"
    ),
    "callejero_viales_vigentes": Path(
        "data/interim/cartografia/callejero_viales_vigentes/"
        "callejero_viales_vigentes_clean.parquet"
    ),
    "ser_parquimetros": Path(
        "data/interim/ser/ser_parquimetros/ser_parquimetros_clean.parquet"
    ),
}
EMT_INVENTORY_PATH = Path("data/processed/core/emt/inventario_global_emt.parquet")
SER_CAPACITY_PATH = Path("data/processed/core/ser/ser_barrio_capacidad_anio.parquet")

MIN_COLUMNS = {
    "ser_geoportal_limite_ser": {"geometry"},
    "ser_geoportal_barrios_ser": {"cod_distrito", "cod_barrio", "barrio", "geometry"},
    "ser_geoportal_bandas_aparcamiento": {
        "id_banda",
        "color",
        "numero_plazas",
        "geometry",
    },
    "callejero_viales_vigentes": {"top_id", "nombre_via_completo", "geometry"},
    "ser_parquimetros": {"gis_x", "gis_y", "longitud", "latitud"},
}

COLOR_STYLE = {
    "azul": "#2563eb",
    "verde": "#16a34a",
    "alta_rotacion": "#7c3aed",
    "rojo": "#dc2626",
    "naranja": "#f97316",
}
COLOR_LABEL = {
    "azul": "Azul",
    "verde": "Verde",
    "alta_rotacion": "Alta rotación",
    "rojo": "Azul sanitario",
    "naranja": "Uso disuasorio",
}
CHECK_STATUSES = {"OK", "WARNING", "FAIL"}


def _configure_pyproj_data_dir() -> None:
    candidates = [
        Path("/opt/anaconda3/envs/tfm-parking/share/proj"),
        Path("/opt/anaconda3/share/proj"),
    ]
    for candidate in candidates:
        if (candidate / "proj.db").exists():
            pyproj.datadir.set_data_dir(str(candidate))
            return


_configure_pyproj_data_dir()


@dataclass
class SEREMTMapResult:
    folium_map: Any
    layers: dict[str, gpd.GeoDataFrame]
    checks: pd.DataFrame
    diagnostics: dict[str, pd.DataFrame]
    outputs: pd.DataFrame


@dataclass
class SERPredictionMapResult:
    folium_map: Any
    layers: dict[str, gpd.GeoDataFrame]
    checks: pd.DataFrame
    diagnostics: dict[str, pd.DataFrame]
    outputs: pd.DataFrame
    scenario: dict[str, Any]


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "data_catalog.csv").exists():
            return candidate
    raise FileNotFoundError("data_catalog.csv not found from current path upwards.")


def relpath(path: Path, root: Path | None = None) -> str:
    base = root or find_repo_root()
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _union_geometry(gdf: gpd.GeoDataFrame):
    if hasattr(gdf.geometry, "union_all"):
        return gdf.geometry.union_all()
    return gdf.geometry.unary_union


def _is_missing_geo_metadata_error(exc: Exception) -> bool:
    return "Missing geo metadata" in str(exc)


def _to_numeric_coordinate(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype("string").str.replace(",", ".", regex=False).str.strip(),
        errors="coerce",
    )


def build_parquimetros_geometry_from_coordinates(
    df: pd.DataFrame,
    dataset_id: str = "ser_parquimetros",
) -> gpd.GeoDataFrame:
    required = {"gis_x", "gis_y", "longitud", "latitud"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{dataset_id}: missing coordinate columns: {missing}")

    x = _to_numeric_coordinate(df["gis_x"])
    y = _to_numeric_coordinate(df["gis_y"])
    if x.notna().any() and y.notna().any():
        return gpd.GeoDataFrame(df.copy(), geometry=gpd.points_from_xy(x, y), crs=TARGET_CRS)

    lon = _to_numeric_coordinate(df["longitud"])
    lat = _to_numeric_coordinate(df["latitud"])
    if not (lon.notna().any() and lat.notna().any()):
        raise ValueError(f"{dataset_id}: no valid projected or geographic coordinates.")

    gdf = gpd.GeoDataFrame(df.copy(), geometry=gpd.points_from_xy(lon, lat), crs=WEB_CRS)
    return gdf.to_crs(TARGET_CRS)


def _coerce_geometry_value(value: Any):
    from shapely import wkb, wkt
    from shapely.geometry.base import BaseGeometry

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, BaseGeometry):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return wkb.loads(bytes(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return wkt.loads(text)
        except Exception:
            return wkb.loads(bytes.fromhex(text))
    raise TypeError(f"Unsupported geometry value type: {type(value).__name__}")


def read_layer_parquet(dataset_id: str, path: Path) -> gpd.GeoDataFrame:
    try:
        return gpd.read_parquet(path)
    except ValueError as exc:
        if not _is_missing_geo_metadata_error(exc):
            raise

        df = pd.read_parquet(path)
        if "geometry" in df.columns:
            geometry = df["geometry"].map(_coerce_geometry_value)
            return gpd.GeoDataFrame(df.drop(columns=["geometry"]), geometry=geometry, crs=TARGET_CRS)
        if dataset_id == "ser_parquimetros":
            return build_parquimetros_geometry_from_coordinates(df, dataset_id)
        raise ValueError(f"{dataset_id}: Parquet has no GeoParquet metadata or geometry column.") from exc


def validate_and_read_layer(dataset_id: str, path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{dataset_id}: missing file {path}")

    gdf = read_layer_parquet(dataset_id, path)
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError(f"{dataset_id}: read object is not a GeoDataFrame.")
    if gdf.empty:
        raise ValueError(f"{dataset_id}: empty GeoDataFrame.")
    if gdf.crs is None:
        raise ValueError(f"{dataset_id}: CRS is missing.")
    if "geometry" not in gdf.columns or gdf.geometry.isna().all():
        raise ValueError(f"{dataset_id}: missing or fully null geometry.")

    missing = sorted(MIN_COLUMNS[dataset_id] - set(gdf.columns))
    if missing:
        raise ValueError(f"{dataset_id}: missing required columns: {missing}")
    if gdf.crs.to_epsg() != 25830:
        gdf = gdf.to_crs(TARGET_CRS)
    return gdf


def read_ser_map_layers(root: Path) -> dict[str, gpd.GeoDataFrame]:
    return {
        dataset_id: validate_and_read_layer(dataset_id, root / relative_path)
        for dataset_id, relative_path in SER_INPUT_PATHS.items()
    }


def read_emt_inventory_as_gdf(
    path: Path,
    *,
    expected_emt_entities: int = 85,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, ok: bool, detail: str, critical: bool = True) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "OK" if ok else "FAIL",
                "detail": detail,
                "critical": critical,
            }
        )

    add("emt_file_exists", path.exists(), str(path))
    if not path.exists():
        return gpd.GeoDataFrame(), pd.DataFrame(checks)

    df = pd.read_parquet(path)
    add("emt_not_empty", not df.empty, f"rows={len(df)}")
    add(
        "emt_expected_entities",
        len(df) == expected_emt_entities,
        f"rows={len(df)}; expected={expected_emt_entities}",
    )
    if df.empty:
        return gpd.GeoDataFrame(df), pd.DataFrame(checks)

    required = {"parking_uid", "latitud", "longitud"}
    missing = sorted(required - set(df.columns))
    add("emt_required_columns", not missing, f"missing={missing}")
    if missing:
        return gpd.GeoDataFrame(df), pd.DataFrame(checks)

    uid_notna = df["parking_uid"].notna().all()
    uid_unique = df["parking_uid"].is_unique
    lat = _to_numeric_coordinate(df["latitud"])
    lon = _to_numeric_coordinate(df["longitud"])
    coords_notna = lat.notna().all() and lon.notna().all()
    coords_range = lat.between(40.0, 41.0).all() and lon.between(-4.5, -3.0).all()

    add("emt_parking_uid_not_null", bool(uid_notna), f"nulls={int(df['parking_uid'].isna().sum())}")
    add("emt_parking_uid_unique", bool(uid_unique), f"duplicates={int(df['parking_uid'].duplicated().sum())}")
    add(
        "emt_coordinates_valid",
        bool(coords_notna and coords_range),
        (
            f"lat_nulls={int(lat.isna().sum())}; lon_nulls={int(lon.isna().sum())}; "
            f"lat_range=[{lat.min()}, {lat.max()}]; lon_range=[{lon.min()}, {lon.max()}]"
        ),
    )

    emt = df.copy()
    emt["latitud"] = lat
    emt["longitud"] = lon
    gdf = gpd.GeoDataFrame(emt, geometry=gpd.points_from_xy(lon, lat), crs=WEB_CRS)
    geometry_ok = gdf.geometry.notna().all() and not gdf.geometry.is_empty.any()
    add("emt_geometry_valid", bool(geometry_ok), f"null_geometry={int(gdf.geometry.isna().sum())}")
    return gdf.to_crs(TARGET_CRS), pd.DataFrame(checks)


def make_barrio_key(df: pd.DataFrame) -> pd.Series:
    district = pd.to_numeric(df["cod_distrito"], errors="coerce").astype("Int64")
    barrio_code = pd.to_numeric(df["cod_barrio"], errors="coerce").astype("Int64")
    barrio_num = barrio_code.where(barrio_code < 100, barrio_code % 100)
    return (
        district.astype("string").str.zfill(2)
        + "_"
        + barrio_num.astype("string").str.zfill(2)
    ).mask(district.isna() | barrio_num.isna())


def build_model_compatible_barrios(
    barrios_map: gpd.GeoDataFrame,
    capacidad_target: pd.DataFrame,
    *,
    expected_model_barrios: int = 65,
) -> tuple[gpd.GeoDataFrame, dict[str, pd.DataFrame]]:
    barrios = barrios_map.copy()
    barrios["barrio_key"] = make_barrio_key(barrios)

    model_keys = set(capacidad_target["barrio_key"].dropna().astype(str))
    map_keys = set(barrios["barrio_key"].dropna().astype(str))

    names_by_key = (
        barrios.dropna(subset=["barrio_key"])
        .groupby("barrio_key", as_index=False)
        .agg(
            n_geometrias_origen=("geometry", "size"),
            nombres_cartograficos=("barrio", lambda s: " / ".join(pd.Series(s).dropna().astype(str))),
            cod_barrio=("cod_barrio", "first"),
        )
        .sort_values("barrio_key")
    )
    duplicates = names_by_key.loc[names_by_key["n_geometrias_origen"].gt(1)].copy()

    barrios_model_map = barrios.dissolve(by="barrio_key", as_index=False)
    model_lookup = (
        capacidad_target.loc[:, ["barrio_key", "barrio", "cod_distrito", "cod_barrio"]]
        .drop_duplicates("barrio_key")
        .rename(columns={"barrio": "barrio_modelo"})
    )
    barrios_model_map = barrios_model_map.drop(
        columns=[col for col in ["cod_distrito", "cod_barrio"] if col in barrios_model_map.columns]
    ).merge(model_lookup, on="barrio_key", how="left", validate="one_to_one")
    barrios_model_map = barrios_model_map.merge(
        names_by_key.loc[:, ["barrio_key", "n_geometrias_origen", "nombres_cartograficos"]],
        on="barrio_key",
        how="left",
        validate="one_to_one",
    )
    barrios_model_map["barrio"] = barrios_model_map["barrio_modelo"].fillna(
        barrios_model_map.get("barrio")
    )

    summary = pd.DataFrame(
        [
            {
                "metric": "filas_cartografia_original",
                "value": len(barrios),
            },
            {
                "metric": "barrio_key_unicos_cartografia",
                "value": barrios["barrio_key"].nunique(dropna=True),
            },
            {
                "metric": "filas_tras_disolver",
                "value": len(barrios_model_map),
            },
            {
                "metric": "barrios_modelo",
                "value": len(model_keys),
            },
            {
                "metric": "duplicados_originales_por_barrio_key",
                "value": len(duplicates),
            },
            {
                "metric": "keys_cartografia_no_modelo",
                "value": len(map_keys - model_keys),
            },
            {
                "metric": "keys_modelo_no_cartografia",
                "value": len(model_keys - map_keys),
            },
            {
                "metric": "caso_09_04",
                "value": "Valdezarza / Valdezarza Fase III",
            },
            {
                "metric": "expected_model_barrios",
                "value": expected_model_barrios,
            },
        ]
    )
    key_diff = pd.DataFrame(
        [
            {"direction": "cartografia_no_modelo", "barrio_key": key}
            for key in sorted(map_keys - model_keys)
        ]
        + [
            {"direction": "modelo_no_cartografia", "barrio_key": key}
            for key in sorted(model_keys - map_keys)
        ]
    )
    return barrios_model_map, {
        "barrios_model_compatibility": summary,
        "barrios_source_names_by_key": names_by_key,
        "barrios_duplicate_keys": duplicates,
        "barrios_key_differences": key_diff,
    }


def prepare_ser_map_views(
    layers: dict[str, gpd.GeoDataFrame],
    barrios_model_map: gpd.GeoDataFrame,
    *,
    visual_buffer_m: float = 25,
) -> dict[str, gpd.GeoDataFrame]:
    limite = layers["ser_geoportal_limite_ser"]
    limite_geom = _union_geometry(limite)
    visual_area = limite_geom.buffer(visual_buffer_m)
    callejero = layers["callejero_viales_vigentes"]
    parquimetros = layers["ser_parquimetros"]
    return {
        "limite_map": limite,
        "barrios_map": layers["ser_geoportal_barrios_ser"],
        "barrios_model_map": barrios_model_map,
        "bandas_map": layers["ser_geoportal_bandas_aparcamiento"],
        "callejero_map": callejero.loc[callejero.geometry.intersects(visual_area).fillna(False)].copy(),
        "parquimetros_map": parquimetros.loc[
            parquimetros.geometry.intersects(visual_area).fillna(False)
        ].copy(),
    }


def to_web(gdf: gpd.GeoDataFrame, simplify_m: float | None = None) -> gpd.GeoDataFrame:
    web_gdf = gdf.copy()
    if simplify_m is not None and simplify_m > 0:
        web_gdf["geometry"] = web_gdf.geometry.simplify(simplify_m, preserve_topology=True)
    return web_gdf.to_crs(WEB_CRS)


def add_geojson_layer(
    fmap: folium.Map,
    gdf: gpd.GeoDataFrame,
    name: str,
    style_function: Any,
    tooltip_fields: list[str] | None = None,
    tooltip_aliases: list[str] | None = None,
    popup_fields: list[str] | None = None,
    popup_aliases: list[str] | None = None,
    show: bool = True,
    interactive: bool = True,
) -> folium.GeoJson:
    tooltip = None
    if tooltip_fields:
        fields = [field for field in tooltip_fields if field in gdf.columns]
        if fields:
            aliases = tooltip_aliases if tooltip_aliases and len(tooltip_aliases) == len(fields) else None
            tooltip = folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=False)
    popup = None
    if popup_fields:
        fields = [field for field in popup_fields if field in gdf.columns]
        if fields:
            aliases = popup_aliases if popup_aliases and len(popup_aliases) == len(fields) else None
            popup = folium.GeoJsonPopup(fields=fields, aliases=aliases, labels=True)

    layer = folium.GeoJson(
        data=gdf.to_json(),
        name=name,
        style_function=style_function,
        tooltip=tooltip,
        popup=popup,
        show=show,
        interactive=interactive,
    )
    layer.add_to(fmap)
    return layer


def _prepare_bandas_web(
    bandas_map: gpd.GeoDataFrame,
    web_simplify_m: float,
) -> gpd.GeoDataFrame:
    bandas_web = to_web(bandas_map, web_simplify_m)
    bandas_web["color_label"] = bandas_web["color"].map(COLOR_LABEL).fillna(
        bandas_web["color"].astype("string")
    )
    columns = ["color", "color_label"]
    if "tipo_aparcamiento_label" in bandas_web.columns:
        columns.append("tipo_aparcamiento_label")
    columns.extend(["numero_plazas", "geometry"])
    return bandas_web.loc[:, columns]


def _bandas_tooltip_fields_aliases(gdf: gpd.GeoDataFrame) -> tuple[list[str], list[str]]:
    fields = ["color_label"]
    aliases = ["Modalidad SER"]
    if "tipo_aparcamiento_label" in gdf.columns:
        fields.append("tipo_aparcamiento_label")
        aliases.append("Tipo de aparcamiento")
    fields.append("numero_plazas")
    aliases.append("Número de plazas")
    return fields, aliases


def add_marker_cluster(
    fmap: folium.Map,
    gdf: gpd.GeoDataFrame,
    *,
    name: str,
    tooltip_fields: list[str] | None = None,
    icon_html: str,
    icon_size: tuple[int, int],
    icon_anchor: tuple[int, int],
    show: bool,
    tooltip_default: str | None = None,
    tooltip_builder: Any | None = None,
) -> MarkerCluster:
    cluster = MarkerCluster(name=name, show=show)
    fields = [field for field in (tooltip_fields or []) if field in gdf.columns]
    for row in gdf.to_crs(WEB_CRS).itertuples(index=False):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if tooltip_builder is not None:
            tooltip = tooltip_builder(row)
        else:
            tooltip = " | ".join(
                str(getattr(row, field)) for field in fields if pd.notna(getattr(row, field))
            )
        tooltip = tooltip or tooltip_default or name
        folium.Marker(
            location=[geom.y, geom.x],
            icon=folium.DivIcon(
                html=icon_html,
                icon_size=icon_size,
                icon_anchor=icon_anchor,
            ),
            tooltip=folium.Tooltip(tooltip, sticky=False),
        ).add_to(cluster)
    cluster.add_to(fmap)
    return cluster


def _build_emt_tooltip(row: Any) -> str:
    parts: list[str] = []

    if hasattr(row, "nombre") and pd.notna(row.nombre):
        parts.append(f"<b>Nombre:</b> {escape(str(row.nombre))}")

    reference_capacity_fields = [
        ("plazas_standard_emt", "EMT estándar"),
        ("plazas_publicas_municipal", "municipal pública"),
        ("plazas_automoviles_municipal", "municipal automóviles"),
    ]
    for field, source_label in reference_capacity_fields:
        if not hasattr(row, field):
            continue
        value = getattr(row, field)
        if pd.isna(value):
            continue
        parts.append(f"<b>Plazas de referencia:</b> {escape(str(value))}")
        parts.append(f"<b>Fuente capacidad:</b> {escape(source_label)}")
        break

    complementary_fields = [
        ("plazas_residentes_municipal", "Plazas residentes municipales"),
        ("plazas_pmr_emt", "Plazas EMT PMR"),
        ("plazas_pmr_municipal", "Plazas municipales PMR"),
        ("plazas_electricas_municipal", "Plazas eléctricas municipales"),
    ]
    for field, alias in complementary_fields:
        if not hasattr(row, field):
            continue
        value = getattr(row, field)
        if pd.isna(value):
            continue
        parts.append(f"<b>{escape(alias)}:</b> {escape(str(value))}")
    return "<br>".join(parts) or "Aparcamiento EMT/off-street"


def _reference_capacity_from_row(row: pd.Series | Any) -> tuple[Any, str | pd.NA]:
    reference_capacity_fields = [
        ("plazas_standard_emt", "EMT estándar"),
        ("plazas_publicas_municipal", "municipal pública"),
        ("plazas_automoviles_municipal", "municipal automóviles"),
    ]
    for field, source_label in reference_capacity_fields:
        if isinstance(row, pd.Series):
            if field not in row.index:
                continue
            value = row[field]
        else:
            if not hasattr(row, field):
                continue
            value = getattr(row, field)
        if pd.isna(value):
            continue
        return value, source_label
    return pd.NA, pd.NA


def _emt_realtime_category(free_valid: Any, pct_free: Any) -> str:
    if pd.isna(free_valid):
        return "disponibilidad_informada_sin_capacidad"
    if pd.isna(pct_free):
        return "disponibilidad_informada_sin_capacidad"
    pct = float(pct_free)
    if pct < 0.30:
        return "baja"
    if pct < 0.70:
        return "media"
    return "alta"


_EMT_NAME_STOPWORDS = {
    "APARCAMIENTO",
    "APARCAMIENTOS",
    "PARKING",
    "DE",
    "DEL",
    "LA",
    "LAS",
    "LOS",
    "EL",
    "EN",
    "Y",
}


def _fold_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def _name_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _fold_text(value).split()
        if len(token) >= 3 and token not in _EMT_NAME_STOPWORDS
    }


def _emt_realtime_name_compatible(row: pd.Series) -> bool:
    if "realtime_name" not in row.index or pd.isna(row.get("realtime_name")):
        return True
    if "nombre" not in row.index or pd.isna(row.get("nombre")):
        return True
    inventory_name = _fold_text(row["nombre"])
    realtime_name = _fold_text(row["realtime_name"])
    if not inventory_name or not realtime_name:
        return True
    inventory_tokens = _name_tokens(row["nombre"])
    realtime_tokens = _name_tokens(row["realtime_name"])
    if inventory_tokens and realtime_tokens and inventory_tokens.intersection(realtime_tokens):
        return True
    return SequenceMatcher(None, inventory_name, realtime_name).ratio() >= 0.62


def _emt_realtime_name_mismatch_mask(df: pd.DataFrame) -> pd.Series:
    if not {"nombre", "realtime_name"}.issubset(df.columns):
        return pd.Series(False, index=df.index)
    live = df["has_live_free"].fillna(False).astype(bool) if "has_live_free" in df else True
    compatible = df.apply(_emt_realtime_name_compatible, axis=1)
    return live & ~compatible


def prepare_emt_realtime_layer(
    emt_realtime_joined: pd.DataFrame,
) -> gpd.GeoDataFrame:
    required = {"has_live_free", "latitud", "longitud", "free_valid"}
    missing = sorted(required - set(emt_realtime_joined.columns))
    if missing:
        raise ValueError(f"Faltan columnas en EMT realtime joined: {missing}")

    live = emt_realtime_joined.loc[
        emt_realtime_joined["has_live_free"].fillna(False).astype(bool)
    ].copy()
    live["latitud"] = pd.to_numeric(live["latitud"], errors="coerce")
    live["longitud"] = pd.to_numeric(live["longitud"], errors="coerce")
    live["free_raw"] = pd.to_numeric(live.get("free_raw"), errors="coerce")
    live["free_valid"] = pd.to_numeric(live["free_valid"], errors="coerce")
    live = live.loc[live["latitud"].notna() & live["longitud"].notna()].copy()
    if {"nombre", "realtime_name"}.issubset(live.columns):
        compatible_mask = live.apply(_emt_realtime_name_compatible, axis=1)
        live = live.loc[compatible_mask].copy()

    capacities = live.apply(_reference_capacity_from_row, axis=1, result_type="expand")
    if capacities.empty:
        live["plazas_referencia"] = pd.NA
        live["fuente_capacidad"] = pd.NA
    else:
        live["plazas_referencia"] = pd.to_numeric(capacities[0], errors="coerce")
        live["fuente_capacidad"] = capacities[1].astype("string")
    live["pct_libre_referencia"] = (
        live["free_valid"] / live["plazas_referencia"]
    ).where(live["plazas_referencia"].gt(0))
    live["pct_libre_referencia_label"] = (
        (live["pct_libre_referencia"] * 100).round().astype("Int64").astype("string") + "%"
    )
    live.loc[live["pct_libre_referencia"].isna(), "pct_libre_referencia_label"] = pd.NA
    live["categoria_disponibilidad_emt"] = [
        _emt_realtime_category(free, pct)
        for free, pct in zip(live["free_valid"], live["pct_libre_referencia"])
    ]
    live["categoria_disponibilidad_emt_label"] = live[
        "categoria_disponibilidad_emt"
    ].map(EMT_REALTIME_CATEGORY_LABELS)
    live["marker_color"] = live["categoria_disponibilidad_emt"].map(
        EMT_REALTIME_CATEGORY_COLORS
    )
    geometry = gpd.points_from_xy(live["longitud"], live["latitud"], crs=WEB_CRS)
    gdf = gpd.GeoDataFrame(live, geometry=geometry, crs=WEB_CRS).to_crs(TARGET_CRS)
    keep_columns = [
        "parking_uid",
        "id_emt",
        "nombre",
        "latitud",
        "longitud",
        "free_raw",
        "free_valid",
        "moment",
        "query_timestamp",
        "coverage_status",
        "plazas_standard_emt",
        "plazas_pmr_emt",
        "plazas_publicas_municipal",
        "plazas_automoviles_municipal",
        "plazas_residentes_municipal",
        "plazas_pmr_municipal",
        "plazas_electricas_municipal",
        "plazas_referencia",
        "fuente_capacidad",
        "pct_libre_referencia",
        "pct_libre_referencia_label",
        "categoria_disponibilidad_emt",
        "categoria_disponibilidad_emt_label",
        "marker_color",
        "geometry",
    ]
    keep_columns = [column for column in keep_columns if column in gdf.columns]
    return gdf.loc[:, keep_columns]


def _format_time_value(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Europe/Madrid")
    return ts.strftime("%H:%M:%S")


def _format_datetime_value(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Europe/Madrid")
    return ts.strftime("%d/%m/%Y %H:%M:%S")


def _format_integer_value(value: Any) -> str:
    if pd.isna(value):
        return ""
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return str(int(round(float(numeric))))
    return str(value)


def _build_emt_realtime_tooltip(row: Any) -> str:
    parts: list[str] = []
    fields = [
        ("nombre", "Nombre"),
        ("free_valid", "Plazas libres informadas por API"),
        ("plazas_standard_emt", "Plazas estándar EMT"),
        ("plazas_pmr_emt", "Plazas PMR EMT"),
        ("plazas_residentes_municipal", "Plazas residentes municipales"),
        ("plazas_pmr_municipal", "Plazas PMR municipales"),
        ("plazas_electricas_municipal", "Plazas eléctricas municipales"),
    ]
    for field, alias in fields:
        if not hasattr(row, field):
            continue
        value = getattr(row, field)
        if pd.isna(value):
            continue
        if field == "nombre":
            formatted = str(value)
        else:
            formatted = _format_integer_value(value)
        parts.append(f"<b>{escape(alias)}:</b> {escape(formatted)}")
    return "<br>".join(parts) or "EMT tiempo real"


def add_emt_mixed_marker_cluster(
    fmap: folium.Map,
    inventory_gdf: gpd.GeoDataFrame,
    realtime_gdf: gpd.GeoDataFrame | None = None,
    *,
    name: str = "Aparcamientos EMT/off-street",
    show: bool = True,
) -> MarkerCluster:
    cluster = MarkerCluster(name=name, show=show)
    realtime_ids: set[int] = set()
    if realtime_gdf is not None and not realtime_gdf.empty and "id_emt" in realtime_gdf.columns:
        realtime_ids = set(realtime_gdf["id_emt"].dropna().astype(int))

    inventory = inventory_gdf.copy()
    if "id_emt" not in inventory.columns and "id_emt_referencia" in inventory.columns:
        inventory["id_emt"] = inventory["id_emt_referencia"]
    if realtime_ids and "id_emt" in inventory.columns:
        inventory_ids = pd.to_numeric(inventory["id_emt"], errors="coerce")
        inventory = inventory.loc[
            inventory_ids.isna() | ~inventory_ids.astype("Int64").isin(realtime_ids)
        ].copy()

    emt_icon_html = """
    <div style="
        width: 18px; height: 18px; border-radius: 3px;
        background: #7e22ce; color: white; border: 1px solid white;
        box-shadow: 0 0 2px rgba(0,0,0,.45);
        font-size: 10px; font-weight: 700; line-height: 18px;
        text-align: center; font-family: Arial, sans-serif;">E</div>
    """
    for row in inventory.to_crs(WEB_CRS).itertuples(index=False):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        folium.Marker(
            location=[geom.y, geom.x],
            icon=folium.DivIcon(
                html=emt_icon_html,
                icon_size=(18, 18),
                icon_anchor=(9, 9),
            ),
            tooltip=folium.Tooltip(_build_emt_tooltip(row), sticky=False),
        ).add_to(cluster)

    if realtime_gdf is None or realtime_gdf.empty:
        cluster.add_to(fmap)
        return cluster

    for row in realtime_gdf.to_crs(WEB_CRS).itertuples(index=False):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        color = getattr(row, "marker_color", "#7c3aed")
        free = getattr(row, "free_valid", "")
        icon_html = f"""
        <div style="
            width: 24px; height: 24px; border-radius: 50%;
            background: {escape(str(color))}; color: #111827; border: 2px solid white;
            box-shadow: 0 0 3px rgba(0,0,0,.45);
            font-size: 10px; font-weight: 800; line-height: 20px;
            text-align: center; font-family: Arial, sans-serif;">{escape(str(int(free)) if pd.notna(free) else "E")}</div>
        """
        folium.Marker(
            location=[geom.y, geom.x],
            icon=folium.DivIcon(
                html=icon_html,
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
            tooltip=folium.Tooltip(_build_emt_realtime_tooltip(row), sticky=False),
        ).add_to(cluster)
    cluster.add_to(fmap)
    return cluster


def build_ser_emt_base_map(
    layers: dict[str, gpd.GeoDataFrame],
    *,
    web_simplify_m: float = 0.5,
) -> folium.Map:
    limite_web = to_web(layers["limite_map"], web_simplify_m)
    barrios_web = to_web(layers["barrios_model_map"], web_simplify_m).reset_index(drop=True)
    barrios_web = barrios_web.loc[:, ["barrio", "barrio_key", "geometry"]]
    bandas_web = _prepare_bandas_web(layers["bandas_map"], web_simplify_m)
    bandas_fields, bandas_aliases = _bandas_tooltip_fields_aliases(bandas_web)
    callejero_web = to_web(layers["callejero_map"], web_simplify_m)

    center = _union_geometry(limite_web).centroid
    fmap = folium.Map(
        location=[center.y, center.x],
        zoom_start=13,
        tiles="CartoDB Positron",
        control_scale=True,
    )

    callejero_layer = add_geojson_layer(
        fmap,
        callejero_web,
        "Callejero propio/viales vigentes",
        lambda feature: {
            "color": "#6b7280",
            "weight": 0.25,
            "fillColor": "#9ca3af",
            "fillOpacity": 0.05,
            "opacity": 0.35,
        },
        tooltip_fields=["top_id", "nombre_via_completo"],
        show=False,
    )
    Search(
        layer=callejero_layer,
        geom_type="Polygon",
        search_label="nombre_via_completo",
        placeholder="Buscar calle...",
        collapsed=False,
    ).add_to(fmap)

    add_geojson_layer(
        fmap,
        barrios_web,
        "Barrios SER modelo — límites",
        lambda feature: {"color": "#374151", "weight": 1.1, "fillOpacity": 0, "opacity": 0.85},
        show=True,
        interactive=False,
    )
    add_geojson_layer(
        fmap,
        limite_web,
        "Límite SER",
        lambda feature: {"color": "#000000", "weight": 2.5, "fillOpacity": 0},
        show=True,
        interactive=False,
    )
    add_geojson_layer(
        fmap,
        bandas_web,
        "Bandas SER",
        lambda feature: {
            "color": COLOR_STYLE.get(feature["properties"].get("color"), "#4b5563"),
            "weight": 2.4,
            "opacity": 0.9,
        },
        tooltip_fields=bandas_fields,
        tooltip_aliases=bandas_aliases,
        popup_fields=bandas_fields,
        popup_aliases=bandas_aliases,
        show=True,
    )

    parq_icon_html = """
    <div style="
        width: 16px; height: 16px; border-radius: 50%;
        background: #2563eb; color: white; border: 1px solid white;
        box-shadow: 0 0 2px rgba(0,0,0,.45);
        font-size: 10px; font-weight: 700; line-height: 16px;
        text-align: center; font-family: Arial, sans-serif;">P</div>
    """
    add_marker_cluster(
        fmap,
        layers["parquimetros_map"],
        name="Parquímetros SER",
        icon_html=parq_icon_html,
        icon_size=(16, 16),
        icon_anchor=(8, 8),
        show=False,
        tooltip_default="Parquímetro SER",
    )

    emt_icon_html = """
    <div style="
        width: 18px; height: 18px; border-radius: 3px;
        background: #7e22ce; color: white; border: 1px solid white;
        box-shadow: 0 0 2px rgba(0,0,0,.45);
        font-size: 10px; font-weight: 700; line-height: 18px;
        text-align: center; font-family: Arial, sans-serif;">E</div>
    """
    add_marker_cluster(
        fmap,
        layers["emt_map"],
        name="Aparcamientos EMT/off-street",
        icon_html=emt_icon_html,
        icon_size=(18, 18),
        icon_anchor=(9, 9),
        show=True,
        tooltip_builder=_build_emt_tooltip,
    )

    add_geojson_layer(
        fmap,
        barrios_web,
        "Barrios SER modelo — consulta",
        lambda feature: {
            "color": "#374151",
            "weight": 0.8,
            "fillColor": "#60a5fa",
            "fillOpacity": 0.08,
            "opacity": 0.75,
        },
        tooltip_fields=["barrio", "barrio_key"],
        tooltip_aliases=["Barrio", "Código barrio"],
        popup_fields=["barrio", "barrio_key"],
        popup_aliases=["Barrio", "Código barrio"],
        show=False,
        interactive=True,
    )

    bounds = limite_web.total_bounds
    fmap.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def _set_extent(ax, geom, pad_m: float = 0) -> None:
    minx, miny, maxx, maxy = geom.bounds
    ax.set_xlim(minx - pad_m, maxx + pad_m)
    ax.set_ylim(miny - pad_m, maxy + pad_m)
    ax.set_aspect("equal")
    ax.set_axis_off()


def _plot_bandas(ax, gdf: gpd.GeoDataFrame, linewidth: float, alpha: float, zorder: int) -> None:
    for color, line_color in COLOR_STYLE.items():
        subset = gdf.loc[gdf["color"].eq(color)]
        if subset.empty:
            continue
        subset.plot(
            ax=ax,
            color=line_color,
            linewidth=linewidth,
            alpha=alpha,
            label=COLOR_LABEL.get(color, color),
            zorder=zorder,
        )


def _save_figure(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return path


def _label_barrios(ax, gdf: gpd.GeoDataFrame, fontsize: float = 5.0) -> int:
    labelled = 0
    for row in gdf.itertuples(index=False):
        geom = row.geometry
        name = getattr(row, "barrio", None)
        if geom is None or geom.is_empty or pd.isna(name):
            continue
        point = geom.representative_point()
        label = str(name).title()
        if len(label) > 15:
            label = "\n".join(wrap(label, width=15, max_lines=2))
        text = ax.text(
            point.x,
            point.y,
            label,
            ha="center",
            va="center",
            fontsize=fontsize - 0.4 if len(label) > 15 else fontsize,
            color="#111827",
            alpha=0.9,
            zorder=5,
        )
        text.set_path_effects(
            [path_effects.Stroke(linewidth=1.8, foreground="white"), path_effects.Normal()]
        )
        labelled += 1
    return labelled


def save_ser_emt_static_figure(
    layers: dict[str, gpd.GeoDataFrame],
    output_path: Path,
    *,
    visual_buffer_m: float = 25,
) -> Path:
    limite_geom = _union_geometry(layers["limite_map"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    fig, ax = plt.subplots(figsize=(12, 12))
    layers["callejero_map"].plot(
        ax=ax, color="#f3f4f6", edgecolor="#d1d5db", linewidth=0.12, zorder=1
    )
    layers["barrios_model_map"].boundary.plot(
        ax=ax, color="#6b7280", linewidth=0.45, alpha=0.65, zorder=2
    )
    _plot_bandas(ax, layers["bandas_map"], linewidth=0.65, alpha=0.86, zorder=3)
    layers["emt_map"].plot(
        ax=ax,
        marker="s",
        color="#7e22ce",
        edgecolor="white",
        linewidth=0.35,
        markersize=16,
        alpha=0.9,
        label="EMT/off-street",
        zorder=4,
    )
    layers["parquimetros_map"].plot(
        ax=ax,
        color="#1f2937",
        markersize=1.4,
        alpha=0.28,
        label="parquímetros",
        zorder=4,
    )
    layers["limite_map"].boundary.plot(
        ax=ax, color="#000000", linewidth=1.25, label="límite SER", zorder=5
    )
    _set_extent(ax, visual_area)
    ax.set_title("Mapa integrado SER + EMT/off-street", fontsize=14, pad=16)
    ax.legend(loc="lower left", frameon=True, framealpha=0.92, fontsize=8)
    return _save_figure(fig, output_path)


def save_model_barrios_figure(
    layers: dict[str, gpd.GeoDataFrame],
    output_path: Path,
    *,
    visual_buffer_m: float = 25,
) -> Path:
    limite_geom = _union_geometry(layers["limite_map"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    fig, ax = plt.subplots(figsize=(12, 12))
    layers["callejero_map"].plot(
        ax=ax,
        color="#f8fafc",
        edgecolor="#d1d5db",
        linewidth=0.10,
        alpha=0.85,
        zorder=1,
    )
    layers["barrios_model_map"].boundary.plot(
        ax=ax, color="#374151", linewidth=0.55, alpha=0.9, zorder=2
    )
    layers["limite_map"].boundary.plot(
        ax=ax, color="#000000", linewidth=1.45, label="límite SER", zorder=3
    )
    _label_barrios(ax, layers["barrios_model_map"], fontsize=5.0)
    _set_extent(ax, visual_area)
    ax.set_title("Barrios SER modelo: 65 entidades cartográficas", fontsize=14, pad=16)
    ax.legend(loc="lower left", frameon=True, framealpha=0.92, fontsize=8)
    return _save_figure(fig, output_path)


def _ser_capacity_target(root: Path, target_year: int) -> pd.DataFrame:
    path = root / SER_CAPACITY_PATH
    if not path.exists():
        raise FileNotFoundError(f"SER capacity file not found: {relpath(path, root)}")
    capacidad = pd.read_parquet(path)
    required = {"anio", "barrio_key", "barrio", "plazas_barrio_anio"}
    missing = sorted(required - set(capacidad.columns))
    if missing:
        raise ValueError(f"SER capacity missing columns: {missing}")
    return capacidad.loc[pd.to_numeric(capacidad["anio"], errors="coerce").eq(target_year)].copy()


def _ser_plazas_diagnostic(
    bandas_map: gpd.GeoDataFrame,
    capacidad_target: pd.DataFrame,
) -> pd.DataFrame:
    bandas_total = pd.to_numeric(bandas_map["numero_plazas"], errors="coerce").sum()
    capacidad_total = pd.to_numeric(capacidad_target["plazas_barrio_anio"], errors="coerce").sum()
    diff_abs = abs(float(bandas_total) - float(capacidad_total))
    diff_rel = diff_abs / float(capacidad_total) if capacidad_total else pd.NA
    status = "OK"
    if pd.notna(diff_rel) and diff_rel > 0.01:
        status = "WARNING"
    if pd.notna(diff_rel) and diff_rel > 0.25:
        status = "FAIL"
    return pd.DataFrame(
        [
            {
                "total_numero_plazas_bandas_ser": float(bandas_total),
                "total_plazas_barrio_anio_target": float(capacidad_total),
                "diferencia_absoluta": diff_abs,
                "diferencia_relativa": diff_rel,
                "status": status,
            }
        ]
    )


def _append_check(
    rows: list[dict[str, Any]],
    check_id: str,
    status: str,
    detail: str,
    critical: bool,
) -> None:
    if status not in CHECK_STATUSES:
        raise ValueError(f"Invalid check status: {status}")
    rows.append(
        {
            "check_id": check_id,
            "status": status,
            "detail": detail,
            "critical": critical,
        }
    )


PREDICTION_REQUIRED_COLUMNS = {
    "barrio_key",
    "barrio_nombre",
    "intervalo_inicio",
    "dia_semana_num",
    "fallback_level",
    "prob_aparcar_proxy",
}

PREDICTION_CATEGORY_ORDER = ["baja", "media", "alta"]
PREDICTION_CATEGORY_LABELS = {
    "baja": "Baja",
    "media": "Media",
    "alta": "Alta",
}
PREDICTION_CATEGORY_COLORS = {
    "baja": "#fecaca",
    "media": "#fef3c7",
    "alta": "#bbf7d0",
}
EMT_REALTIME_CATEGORY_COLORS = {
    "baja": "#fca5a5",
    "media": "#fef3c7",
    "alta": "#bbf7d0",
    "disponibilidad_informada_sin_capacidad": "#7c3aed",
}
EMT_REALTIME_CATEGORY_LABELS = {
    "baja": "Baja: 0–29% libres sobre capacidad ref.",
    "media": "Media: 30–69% libres sobre capacidad ref.",
    "alta": "Alta: 70–100% libres sobre capacidad ref.",
    "disponibilidad_informada_sin_capacidad": "Libres informadas sin capacidad ref.",
}
WEEKDAY_LABELS = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def _base_ser_emt_layers_and_diagnostics(
    *,
    root: Path,
    target_year: int,
    expected_model_barrios: int,
    expected_emt_entities: int,
    visual_buffer_m: float,
) -> tuple[dict[str, gpd.GeoDataFrame], list[dict[str, Any]], dict[str, pd.DataFrame]]:
    checks: list[dict[str, Any]] = []
    diagnostics: dict[str, pd.DataFrame] = {}

    ser_paths = {dataset_id: root / relative for dataset_id, relative in SER_INPUT_PATHS.items()}
    ser_files_exist = all(path.exists() for path in ser_paths.values())
    _append_check(
        checks,
        "ser_layers_exist",
        "OK" if ser_files_exist else "FAIL",
        "; ".join(f"{k}={relpath(v, root)}:{v.exists()}" for k, v in ser_paths.items()),
        True,
    )
    layers = read_ser_map_layers(root)
    _append_check(
        checks,
        "ser_layers_not_empty",
        "OK" if all(not gdf.empty for gdf in layers.values()) else "FAIL",
        "; ".join(f"{k}:rows={len(v)}" for k, v in layers.items()),
        True,
    )
    ser_crs_ok = all(gdf.crs is not None and gdf.crs.to_epsg() == 25830 for gdf in layers.values())
    _append_check(
        checks,
        "ser_crs_epsg_25830",
        "OK" if ser_crs_ok else "FAIL",
        "; ".join(f"{k}:epsg={v.crs.to_epsg() if v.crs else None}" for k, v in layers.items()),
        True,
    )

    capacidad_target = _ser_capacity_target(root, target_year)
    if capacidad_target.empty:
        _append_check(
            checks,
            "ser_capacity_target_not_empty",
            "FAIL",
            f"target_year={target_year}",
            True,
        )

    emt_inventory, emt_checks = read_emt_inventory_as_gdf(
        root / EMT_INVENTORY_PATH,
        expected_emt_entities=expected_emt_entities,
    )
    checks.extend(emt_checks.to_dict("records"))
    diagnostics["emt_inventory"] = pd.DataFrame(
        [
            {
                "n_rows": len(emt_inventory),
                "n_parking_uid": (
                    emt_inventory["parking_uid"].nunique()
                    if "parking_uid" in emt_inventory
                    else 0
                ),
                "crs_epsg": emt_inventory.crs.to_epsg() if emt_inventory.crs else None,
            }
        ]
    )

    barrios_model_map, barrio_diagnostics = build_model_compatible_barrios(
        layers["ser_geoportal_barrios_ser"],
        capacidad_target,
        expected_model_barrios=expected_model_barrios,
    )
    diagnostics.update(barrio_diagnostics)

    original_barrios = layers["ser_geoportal_barrios_ser"].copy()
    original_barrios["barrio_key"] = make_barrio_key(original_barrios)
    carto_rows_ok = len(original_barrios) == 66
    carto_keys_ok = original_barrios["barrio_key"].nunique(dropna=True) == expected_model_barrios
    model_rows_ok = len(barrios_model_map) == expected_model_barrios
    model_keys = set(capacidad_target["barrio_key"].dropna().astype(str))
    map_keys = set(barrios_model_map["barrio_key"].dropna().astype(str))
    keys_match = model_keys == map_keys
    dup_0904 = barrio_diagnostics["barrios_duplicate_keys"].loc[
        lambda df: df["barrio_key"].eq("09_04")
    ]
    dup_0904_ok = (
        len(dup_0904) == 1
        and int(dup_0904.iloc[0]["n_geometrias_origen"]) == 2
        and "Valdezarza Fase III" in str(dup_0904.iloc[0]["nombres_cartograficos"])
    )

    _append_check(checks, "barrios_original_rows_66", "OK" if carto_rows_ok else "FAIL", f"rows={len(original_barrios)}", True)
    _append_check(checks, "barrios_original_unique_keys_65", "OK" if carto_keys_ok else "FAIL", f"unique={original_barrios['barrio_key'].nunique(dropna=True)}", True)
    _append_check(checks, "barrios_model_map_rows_65", "OK" if model_rows_ok else "FAIL", f"rows={len(barrios_model_map)}", True)
    _append_check(
        checks,
        "barrios_model_and_map_keys_match",
        "OK" if keys_match else "FAIL",
        f"map_not_model={sorted(map_keys - model_keys)}; model_not_map={sorted(model_keys - map_keys)}",
        True,
    )
    _append_check(
        checks,
        "barrios_duplicate_09_04_documented",
        "OK" if dup_0904_ok else "FAIL",
        dup_0904.to_dict("records"),
        True,
    )

    map_layers = prepare_ser_map_views(
        layers,
        barrios_model_map,
        visual_buffer_m=visual_buffer_m,
    )
    limite_geom = _union_geometry(layers["ser_geoportal_limite_ser"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    emt_map = emt_inventory.loc[
        emt_inventory.geometry.intersects(visual_area).fillna(False)
    ].copy()
    n_emt_outside_visual_area = len(emt_inventory) - len(emt_map)
    diagnostics["emt_spatial_filter"] = pd.DataFrame(
        [
            {
                "n_emt_inventory_total": len(emt_inventory),
                "n_emt_in_visual_area": len(emt_map),
                "n_emt_outside_visual_area": n_emt_outside_visual_area,
                "visual_buffer_m": visual_buffer_m,
            }
        ]
    )
    _append_check(
        checks,
        "emt_outside_visual_area",
        "WARNING" if n_emt_outside_visual_area > 0 else "OK",
        (
            f"outside={n_emt_outside_visual_area}; "
            f"in_visual_area={len(emt_map)}; inventory_total={len(emt_inventory)}"
        ),
        False,
    )
    map_layers["emt_inventory"] = emt_inventory
    map_layers["emt_map"] = emt_map

    plazas_diagnostic = _ser_plazas_diagnostic(map_layers["bandas_map"], capacidad_target)
    diagnostics["ser_plazas"] = plazas_diagnostic
    plazas_status = plazas_diagnostic.iloc[0]["status"]
    _append_check(
        checks,
        "ser_plazas_bandas_vs_capacidad",
        plazas_status,
        plazas_diagnostic.iloc[0].to_dict(),
        plazas_status == "FAIL",
    )
    return map_layers, checks, diagnostics


def _prediction_category_from_pct(pct: int) -> str:
    if pct < 30:
        return "baja"
    if pct < 70:
        return "media"
    return "alta"


def _prepare_prediction_barrios(
    barrios_model_map: gpd.GeoDataFrame,
    operational: pd.DataFrame,
    *,
    expected_model_barrios: int = 65,
) -> tuple[gpd.GeoDataFrame, list[dict[str, Any]], dict[str, pd.DataFrame]]:
    checks: list[dict[str, Any]] = []
    diagnostics: dict[str, pd.DataFrame] = {}

    missing = sorted(PREDICTION_REQUIRED_COLUMNS - set(operational.columns))
    _append_check(
        checks,
        "prediction_required_columns",
        "OK" if not missing else "FAIL",
        f"missing={missing}",
        True,
    )
    _append_check(
        checks,
        "prediction_not_empty",
        "OK" if not operational.empty else "FAIL",
        f"rows={len(operational)}",
        True,
    )
    if missing or operational.empty:
        return barrios_model_map.copy(), checks, diagnostics

    prediction = operational.copy()
    prediction["barrio_key"] = prediction["barrio_key"].astype("string")
    prediction["intervalo_inicio"] = pd.to_datetime(prediction["intervalo_inicio"], errors="coerce")
    prediction["prob_aparcar_proxy"] = pd.to_numeric(
        prediction["prob_aparcar_proxy"],
        errors="coerce",
    )

    key_not_null = prediction["barrio_key"].notna().all()
    key_unique = prediction["barrio_key"].is_unique
    n_prediction_keys = prediction["barrio_key"].nunique(dropna=True)
    prob_not_null = prediction["prob_aparcar_proxy"].notna().all()
    prob_range = prediction["prob_aparcar_proxy"].between(0, 1, inclusive="both").all()
    single_interval = prediction["intervalo_inicio"].nunique(dropna=True) == 1

    _append_check(checks, "prediction_barrio_key_not_null", "OK" if key_not_null else "FAIL", f"nulls={int(prediction['barrio_key'].isna().sum())}", True)
    _append_check(checks, "prediction_unique_barrio_key", "OK" if key_unique else "FAIL", f"duplicates={int(prediction['barrio_key'].duplicated().sum())}", True)
    _append_check(checks, "prediction_expected_barrios", "OK" if n_prediction_keys == expected_model_barrios else "FAIL", f"barrios={n_prediction_keys}; expected={expected_model_barrios}", True)
    _append_check(checks, "prediction_prob_not_null", "OK" if prob_not_null else "FAIL", f"nulls={int(prediction['prob_aparcar_proxy'].isna().sum())}", True)
    _append_check(checks, "prediction_prob_range_0_1", "OK" if prob_range else "FAIL", f"min={prediction['prob_aparcar_proxy'].min()}; max={prediction['prob_aparcar_proxy'].max()}", True)
    _append_check(checks, "prediction_single_interval", "OK" if single_interval else "FAIL", f"intervals={prediction['intervalo_inicio'].dropna().astype(str).unique().tolist()}", True)

    pred_cols = [
        "barrio_key",
        "barrio_nombre",
        "intervalo_inicio",
        "dia_semana_num",
        "fallback_level",
        "prob_aparcar_proxy",
    ]
    prediction = prediction.loc[:, pred_cols].copy()

    barrios = barrios_model_map.copy()
    barrios["barrio_key"] = barrios["barrio_key"].astype("string")
    prediction_keys = set(prediction["barrio_key"].dropna().astype(str))
    map_keys = set(barrios["barrio_key"].dropna().astype(str))
    map_not_prediction = sorted(map_keys - prediction_keys)
    prediction_not_map = sorted(prediction_keys - map_keys)

    prediction_layer = barrios.merge(
        prediction,
        on="barrio_key",
        how="left",
        validate="one_to_one",
    )
    missing_after_join = int(prediction_layer["prob_aparcar_proxy"].isna().sum())
    join_ok = missing_after_join == 0 and not map_not_prediction and not prediction_not_map
    _append_check(
        checks,
        "prediction_join_all_barrios",
        "OK" if join_ok else "FAIL",
        (
            f"missing_after_join={missing_after_join}; "
            f"map_not_prediction={map_not_prediction}; prediction_not_map={prediction_not_map}"
        ),
        True,
    )
    _append_check(
        checks,
        "prediction_no_missing_map_keys",
        "OK" if not map_not_prediction else "FAIL",
        f"map_not_prediction={map_not_prediction}",
        True,
    )

    pct = (prediction_layer["prob_aparcar_proxy"] * 100).round().astype("Int64")
    prediction_layer["prob_aparcar_proxy_pct"] = pct
    prediction_layer["prob_aparcar_proxy_label"] = pct.astype("string") + "%"
    prediction_layer["categoria_prob_aparcar"] = pct.astype("int64").map(_prediction_category_from_pct)
    prediction_layer["categoria_prob_aparcar_label"] = prediction_layer[
        "categoria_prob_aparcar"
    ].map(PREDICTION_CATEGORY_LABELS)
    prediction_layer["fill_color"] = prediction_layer["categoria_prob_aparcar"].map(
        PREDICTION_CATEGORY_COLORS
    )
    prediction_layer["barrio"] = prediction_layer["barrio_nombre"].fillna(
        prediction_layer["barrio"]
    )

    diagnostics["prediction_join"] = pd.DataFrame(
        [
            {
                "n_barrios_mapa": len(barrios),
                "n_barrios_operational": len(prediction),
                "n_barrios_join": len(prediction_layer),
                "missing_after_join": missing_after_join,
                "map_not_prediction": ", ".join(map_not_prediction),
                "prediction_not_map": ", ".join(prediction_not_map),
            }
        ]
    )
    diagnostics["prediction_categories"] = (
        prediction_layer.groupby(
            ["categoria_prob_aparcar", "categoria_prob_aparcar_label"],
            dropna=False,
        )
        .size()
        .reindex(
            pd.MultiIndex.from_tuples(
                [(key, PREDICTION_CATEGORY_LABELS[key]) for key in PREDICTION_CATEGORY_ORDER],
                names=["categoria_prob_aparcar", "categoria_prob_aparcar_label"],
            ),
            fill_value=0,
        )
        .reset_index(name="n_barrios")
    )
    q = prediction_layer["prob_aparcar_proxy"].quantile([0, 0.25, 0.5, 0.75, 1])
    diagnostics["prediction_distribution"] = pd.DataFrame(
        [
            {
                "n": int(prediction_layer["prob_aparcar_proxy"].count()),
                "min": q.loc[0],
                "p25": q.loc[0.25],
                "mean": prediction_layer["prob_aparcar_proxy"].mean(),
                "p50": q.loc[0.5],
                "p75": q.loc[0.75],
                "max": q.loc[1],
            }
        ]
    )
    return prediction_layer, checks, diagnostics


def _scenario_from_operational(
    operational: pd.DataFrame,
    scenario_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = scenario_metadata or {}
    interval_start = pd.Timestamp(
        metadata.get("intervalo_inicio")
        or operational["intervalo_inicio"].dropna().iloc[0]
    )
    interval_end_value = metadata.get("intervalo_fin")
    interval_end = (
        pd.Timestamp(interval_end_value)
        if interval_end_value is not None
        else interval_start + pd.Timedelta(minutes=30)
    )
    requested_value = metadata.get("scenario_datetime_requested")
    requested = pd.Timestamp(requested_value) if requested_value is not None else pd.NaT
    weekday_value = metadata.get("dia_semana_num_m0")
    if weekday_value is None and "dia_semana_num" in operational:
        weekday_value = operational["dia_semana_num"].dropna().iloc[0]
    weekday_num = int(weekday_value) if weekday_value is not None and pd.notna(weekday_value) else None
    weekday_label = WEEKDAY_LABELS.get(weekday_num, pd.NA)
    return {
        "fecha": interval_start.date().isoformat(),
        "fecha_label": interval_start.strftime("%d/%m/%Y"),
        "hora_solicitada": (
            requested.strftime("%H:%M") if pd.notna(requested) else None
        ),
        "intervalo_inicio": interval_start,
        "intervalo_fin": interval_end,
        "intervalo_label": f"{interval_start.strftime('%H:%M')}–{interval_end.strftime('%H:%M')}",
        "dia_semana_num": weekday_num,
        "dia_semana": weekday_label,
        "nota": "Escala proxy relativa, no probabilidad observada de encontrar plaza.",
    }


def _add_prediction_labels(
    fmap: folium.Map,
    prediction_layer: gpd.GeoDataFrame,
) -> folium.FeatureGroup:
    group = folium.FeatureGroup(name="Etiquetas prob. aparcar proxy", show=True)
    label_gdf = prediction_layer.to_crs(WEB_CRS).copy()
    for row in label_gdf.itertuples(index=False):
        geom = row.geometry
        label = getattr(row, "prob_aparcar_proxy_label", None)
        if geom is None or geom.is_empty or pd.isna(label):
            continue
        point = geom.representative_point()
        html = f"""
        <div style="
            font-family: Arial, sans-serif;
            font-weight: 700;
            font-size: 11px;
            color: #111827;
            text-shadow: -1px -1px 0 #ffffff, 1px -1px 0 #ffffff,
                         -1px 1px 0 #ffffff, 1px 1px 0 #ffffff;
            white-space: nowrap;
            transform: translate(-50%, -50%);
        ">{escape(str(label))}</div>
        """
        folium.Marker(
            location=[point.y, point.x],
            icon=folium.DivIcon(html=html, icon_size=(1, 1), icon_anchor=(0, 0)),
            interactive=False,
        ).add_to(group)
    group.add_to(fmap)
    return group


def _add_prediction_map_controls(fmap: folium.Map, scenario: dict[str, Any]) -> None:
    hora_solicitada = scenario.get("hora_solicitada")
    hora_line = (
        f"<div><b>Hora solicitada:</b> {escape(str(hora_solicitada))}</div>"
        if hora_solicitada
        else ""
    )
    scenario_html = f"""
    <div style="
        position: fixed;
        top: 74px;
        left: 10px;
        right: auto;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #d1d5db;
        border-radius: 6px;
        padding: 10px 12px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        color: #111827;
        box-shadow: 0 1px 4px rgba(0,0,0,0.18);
        max-width: 280px;
        pointer-events: none;
    ">
        <div style="font-weight:700; margin-bottom:6px;">Escenario SER — facilidad proxy de aparcamiento</div>
        <div><b>Fecha:</b> {escape(str(scenario.get("fecha_label", "")))}</div>
        {hora_line}
        <div><b>Intervalo usado:</b> {escape(str(scenario.get("intervalo_label", "")))}</div>
        <div><b>Día:</b> {escape(str(scenario.get("dia_semana", "")))}</div>
        <div style="margin-top:6px;"><b>Nota:</b> escala proxy relativa, no probabilidad observada.</div>
    </div>
    """
    search_css = """
    <style>
        .leaflet-control-search {
            margin-top: 180px !important;
        }
    </style>
    """
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 28px;
        right: 14px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #d1d5db;
        border-radius: 6px;
        padding: 10px 12px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        color: #111827;
        box-shadow: 0 1px 4px rgba(0,0,0,0.18);
    ">
        <div style="font-weight:700; margin-bottom:6px;">Facilidad proxy SER</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fecaca;border:1px solid #9ca3af;margin-right:6px;"></span>Baja: 0–29%</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fef3c7;border:1px solid #9ca3af;margin-right:6px;"></span>Media: 30–69%</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#bbf7d0;border:1px solid #9ca3af;margin-right:6px;"></span>Alta: 70–100%</div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(search_css))
    fmap.get_root().html.add_child(folium.Element(scenario_html))
    fmap.get_root().html.add_child(folium.Element(legend_html))


def _add_prediction_emt_realtime_map_controls(
    fmap: folium.Map,
    scenario: dict[str, Any],
) -> None:
    hora_solicitada = scenario.get("hora_solicitada")
    hora_line = (
        f"<div><b>Hora solicitada:</b> {escape(str(hora_solicitada))}</div>"
        if hora_solicitada
        else ""
    )
    emt_query = scenario.get("emt_query_timestamp_label") or "sin dato"
    emt_moment = scenario.get("emt_moment_label") or "sin dato vivo"
    residual_legend_line = (
        '<div><span style="display:inline-block;width:14px;height:10px;background:#7c3aed;border:1px solid #9ca3af;margin-right:6px;"></span>Libres informadas sin capacidad ref.</div>'
        if scenario.get("emt_has_residual_capacity_category")
        else ""
    )
    scenario_html = f"""
    <div style="
        position: fixed;
        top: 74px;
        left: 10px;
        right: auto;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #d1d5db;
        border-radius: 6px;
        padding: 10px 12px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        color: #111827;
        box-shadow: 0 1px 4px rgba(0,0,0,0.18);
        max-width: 330px;
        pointer-events: none;
    ">
        <div style="font-weight:700; margin-bottom:6px;">SER proxy + EMT tiempo real</div>
        <div style="font-weight:700; margin-top:4px;">SER proxy</div>
        <div><b>Fecha:</b> {escape(str(scenario.get("fecha_label", "")))}</div>
        {hora_line}
        <div><b>Intervalo SER usado:</b> {escape(str(scenario.get("intervalo_label", "")))}</div>
        <div style="font-weight:700; margin-top:6px;">EMT tiempo real</div>
        <div><b>Consulta API:</b> {escape(str(emt_query))}</div>
        <div><b>Dato EMT:</b> {escape(str(emt_moment))}</div>
        <div style="margin-top:6px;"><b>Nota:</b> SER es una escala proxy estimada; EMT es disponibilidad viva parcial observada en la API.</div>
    </div>
    """
    search_css = """
    <style>
        .leaflet-control-search {
            margin-top: 230px !important;
        }
    </style>
    """
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 28px;
        right: 14px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #d1d5db;
        border-radius: 6px;
        padding: 10px 12px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        color: #111827;
        box-shadow: 0 1px 4px rgba(0,0,0,0.18);
    ">
        <div style="font-weight:700; margin-bottom:6px;">Facilidad proxy SER</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fecaca;border:1px solid #9ca3af;margin-right:6px;"></span>Baja: 0–29%</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fef3c7;border:1px solid #9ca3af;margin-right:6px;"></span>Media: 30–69%</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#bbf7d0;border:1px solid #9ca3af;margin-right:6px;"></span>Alta: 70–100%</div>
        <div style="font-weight:700; margin:8px 0 4px;">EMT tiempo real</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fca5a5;border:1px solid #9ca3af;margin-right:6px;"></span>Baja: 0–29% libres sobre capacidad ref.</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#fef3c7;border:1px solid #9ca3af;margin-right:6px;"></span>Media: 30–69% libres sobre capacidad ref.</div>
        <div><span style="display:inline-block;width:14px;height:10px;background:#bbf7d0;border:1px solid #9ca3af;margin-right:6px;"></span>Alta: 70–100% libres sobre capacidad ref.</div>
        {residual_legend_line}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(search_css))
    fmap.get_root().html.add_child(folium.Element(scenario_html))
    fmap.get_root().html.add_child(folium.Element(legend_html))


def build_ser_prediction_base_map(
    layers: dict[str, gpd.GeoDataFrame],
    scenario: dict[str, Any],
    *,
    web_simplify_m: float = 0.5,
    emt_realtime_layer: gpd.GeoDataFrame | None = None,
    show_emt_inventory: bool = True,
    controls_builder: Any | None = None,
) -> folium.Map:
    limite_web = to_web(layers["limite_map"], web_simplify_m)
    prediction_web = to_web(layers["prediction_barrios"], web_simplify_m).reset_index(drop=True)
    prediction_web = prediction_web.loc[
        :,
        [
            "barrio",
            "barrio_key",
            "prob_aparcar_proxy_label",
            "categoria_prob_aparcar_label",
            "fill_color",
            "geometry",
        ],
    ]
    barrios_web = to_web(layers["barrios_model_map"], web_simplify_m).reset_index(drop=True)
    barrios_web = barrios_web.loc[:, ["barrio", "barrio_key", "geometry"]]
    bandas_web = _prepare_bandas_web(layers["bandas_map"], web_simplify_m)
    bandas_fields, bandas_aliases = _bandas_tooltip_fields_aliases(bandas_web)
    callejero_web = to_web(layers["callejero_map"], web_simplify_m)

    center = _union_geometry(limite_web).centroid
    fmap = folium.Map(
        location=[center.y, center.x],
        zoom_start=13,
        tiles="CartoDB Positron",
        control_scale=True,
    )
    callejero_layer = add_geojson_layer(
        fmap,
        callejero_web,
        "Callejero propio/viales vigentes",
        lambda feature: {
            "color": "#6b7280",
            "weight": 0.25,
            "fillColor": "#9ca3af",
            "fillOpacity": 0.05,
            "opacity": 0.35,
        },
        tooltip_fields=["top_id", "nombre_via_completo"],
        show=False,
    )
    Search(
        layer=callejero_layer,
        geom_type="Polygon",
        search_label="nombre_via_completo",
        placeholder="Buscar calle...",
        collapsed=False,
    ).add_to(fmap)

    add_geojson_layer(
        fmap,
        prediction_web,
        "Barrios SER predicción proxy",
        lambda feature: {
            "color": "#374151",
            "weight": 0.55,
            "fillColor": feature["properties"].get("fill_color", "#f3f4f6"),
            "fillOpacity": 0.52,
            "opacity": 0.65,
        },
        tooltip_fields=[
            "barrio",
            "barrio_key",
            "prob_aparcar_proxy_label",
            "categoria_prob_aparcar_label",
        ],
        tooltip_aliases=[
            "Barrio",
            "Código barrio",
            "Prob. aparcar proxy",
            "Categoría",
        ],
        popup_fields=[
            "barrio",
            "barrio_key",
            "prob_aparcar_proxy_label",
            "categoria_prob_aparcar_label",
        ],
        popup_aliases=[
            "Barrio",
            "Código barrio",
            "Prob. aparcar proxy",
            "Categoría",
        ],
        show=True,
    )
    add_geojson_layer(
        fmap,
        barrios_web,
        "Barrios SER modelo — límites",
        lambda feature: {"color": "#374151", "weight": 1.1, "fillOpacity": 0, "opacity": 0.85},
        show=True,
        interactive=False,
    )
    add_geojson_layer(
        fmap,
        limite_web,
        "Límite SER",
        lambda feature: {"color": "#000000", "weight": 2.5, "fillOpacity": 0},
        show=True,
        interactive=False,
    )
    add_geojson_layer(
        fmap,
        bandas_web,
        "Bandas SER",
        lambda feature: {
            "color": COLOR_STYLE.get(feature["properties"].get("color"), "#4b5563"),
            "weight": 2.4,
            "opacity": 0.9,
        },
        tooltip_fields=bandas_fields,
        tooltip_aliases=bandas_aliases,
        popup_fields=bandas_fields,
        popup_aliases=bandas_aliases,
        show=True,
    )

    parq_icon_html = """
    <div style="
        width: 16px; height: 16px; border-radius: 50%;
        background: #2563eb; color: white; border: 1px solid white;
        box-shadow: 0 0 2px rgba(0,0,0,.45);
        font-size: 10px; font-weight: 700; line-height: 16px;
        text-align: center; font-family: Arial, sans-serif;">P</div>
    """
    add_marker_cluster(
        fmap,
        layers["parquimetros_map"],
        name="Parquímetros SER",
        icon_html=parq_icon_html,
        icon_size=(16, 16),
        icon_anchor=(8, 8),
        show=False,
        tooltip_default="Parquímetro SER",
    )

    if emt_realtime_layer is not None and not emt_realtime_layer.empty:
        add_emt_mixed_marker_cluster(
            fmap,
            layers["emt_map"],
            emt_realtime_layer,
            name="Aparcamientos EMT/off-street",
            show=True,
        )
    else:
        emt_icon_html = """
        <div style="
            width: 18px; height: 18px; border-radius: 3px;
            background: #7e22ce; color: white; border: 1px solid white;
            box-shadow: 0 0 2px rgba(0,0,0,.45);
            font-size: 10px; font-weight: 700; line-height: 18px;
            text-align: center; font-family: Arial, sans-serif;">E</div>
        """
        add_marker_cluster(
            fmap,
            layers["emt_map"],
            name="Aparcamientos EMT/off-street",
            icon_html=emt_icon_html,
            icon_size=(18, 18),
            icon_anchor=(9, 9),
            show=show_emt_inventory,
            tooltip_builder=_build_emt_tooltip,
        )
    _add_prediction_labels(fmap, layers["prediction_barrios"])
    if controls_builder is None:
        controls_builder = _add_prediction_map_controls
    controls_builder(fmap, scenario)
    bounds = limite_web.total_bounds
    fmap.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def save_ser_prediction_static_figure(
    layers: dict[str, gpd.GeoDataFrame],
    output_path: Path,
    scenario: dict[str, Any],
    *,
    visual_buffer_m: float = 25,
) -> Path:
    limite_geom = _union_geometry(layers["limite_map"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    fig, ax = plt.subplots(figsize=(14, 14))
    layers["callejero_map"].plot(
        ax=ax,
        color="#f8fafc",
        edgecolor="#d1d5db",
        linewidth=0.10,
        alpha=0.85,
        zorder=1,
    )
    for category in PREDICTION_CATEGORY_ORDER:
        subset = layers["prediction_barrios"].loc[
            layers["prediction_barrios"]["categoria_prob_aparcar"].eq(category)
        ]
        if subset.empty:
            continue
        subset.plot(
            ax=ax,
            color=PREDICTION_CATEGORY_COLORS[category],
            edgecolor="#374151",
            linewidth=0.45,
            alpha=0.85,
            label=f"{PREDICTION_CATEGORY_LABELS[category]}",
            zorder=2,
        )
    layers["barrios_model_map"].boundary.plot(
        ax=ax, color="#374151", linewidth=0.50, alpha=0.9, zorder=3
    )
    layers["limite_map"].boundary.plot(
        ax=ax, color="#000000", linewidth=1.45, label="límite SER", zorder=4
    )
    for row in layers["prediction_barrios"].itertuples(index=False):
        geom = row.geometry
        pct_label = getattr(row, "prob_aparcar_proxy_label", None)
        barrio_name = getattr(row, "barrio", None)
        if geom is None or geom.is_empty or pd.isna(pct_label) or pd.isna(barrio_name):
            continue
        name_lines = wrap(str(barrio_name).title(), width=12, max_lines=2)
        label = "\n".join([*name_lines, str(pct_label)])
        point = geom.representative_point()
        text = ax.text(
            point.x,
            point.y,
            label,
            ha="center",
            va="center",
            fontsize=5.0,
            fontweight="bold",
            color="#111827",
            zorder=5,
        )
        text.set_path_effects(
            [path_effects.Stroke(linewidth=1.8, foreground="white"), path_effects.Normal()]
        )
    _set_extent(ax, visual_area)
    ax.set_title(
        (
            "Facilidad proxy SER por barrio "
            f"({scenario.get('fecha_label')} {scenario.get('intervalo_label')})\n"
            "Escala proxy relativa; no probabilidad observada de encontrar plaza."
        ),
        fontsize=13,
        pad=16,
    )
    legend_handles = [
        Patch(
            facecolor=PREDICTION_CATEGORY_COLORS[key],
            edgecolor="#374151",
            label=f"{PREDICTION_CATEGORY_LABELS[key]}",
        )
        for key in PREDICTION_CATEGORY_ORDER
    ]
    ax.legend(
        handles=legend_handles,
        title="Facilidad proxy SER",
        loc="lower left",
        frameon=True,
        framealpha=0.92,
        fontsize=8,
        title_fontsize=9,
    )
    return _save_figure(fig, output_path)


def _short_parking_name(value: Any, width: int = 14) -> str:
    if value is None or pd.isna(value):
        return "EMT"
    text = str(value).replace("Aparcamiento", "").replace("aparcamiento", "").strip()
    lines = wrap(text.title(), width=width, max_lines=2)
    return "\n".join(lines) if lines else "EMT"


def _dense_points_window(
    gdf: gpd.GeoDataFrame,
    *,
    radius_m: float = 1800,
    min_half_window_m: float = 2200,
) -> tuple[float, float, float, float]:
    if gdf.empty:
        raise ValueError("No hay puntos EMT tiempo real para calcular el zoom.")
    if len(gdf) == 1:
        point = gdf.geometry.iloc[0]
        return (
            point.x - min_half_window_m,
            point.y - min_half_window_m,
            point.x + min_half_window_m,
            point.y + min_half_window_m,
        )
    counts = []
    for geom in gdf.geometry:
        counts.append(int(gdf.geometry.distance(geom).le(radius_m).sum()))
    center = gdf.geometry.iloc[int(pd.Series(counts).idxmax())]
    window = max(radius_m * 1.25, min_half_window_m)
    return (
        center.x - window,
        center.y - window,
        center.x + window,
        center.y + window,
    )


def _emt_realtime_label_offsets(
    gdf: gpd.GeoDataFrame,
    *,
    ax: plt.Axes,
    fontsize: float = 6.4,
) -> dict[Any, tuple[int, int]]:
    candidate_offsets = [(0, 10), (0, -10), (10, 8), (-10, 8), (10, -8), (-10, -8), (14, 0), (-14, 0)]
    if gdf.empty:
        return {}

    placement_order = (
        gdf.loc[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
        .assign(_label_sort_x=lambda df: df.geometry.x, _label_sort_y=lambda df: df.geometry.y)
        .sort_values(["_label_sort_y", "_label_sort_x"], ascending=[False, True], kind="mergesort")
        .index
        .tolist()
    )
    point_to_pixel = ax.figure.dpi / 72
    placed_boxes: list[tuple[float, float, float, float]] = []
    selected_offsets: dict[Any, tuple[int, int]] = {}

    for idx in placement_order:
        row = gdf.loc[idx]
        geom = row.geometry
        label = _emt_realtime_label_text(row)
        candidates = []
        for offset in candidate_offsets:
            box = _label_box_for_offset(
                ax=ax,
                xy=(geom.x, geom.y),
                label=label,
                offset=offset,
                fontsize=fontsize,
                point_to_pixel=point_to_pixel,
            )
            overlap = sum(_box_overlap_area(box, placed_box) for placed_box in placed_boxes)
            distance = (offset[0] ** 2 + offset[1] ** 2) ** 0.5
            candidates.append((overlap, distance, offset, box))
        candidates.sort(key=lambda item: (item[0] > 0, item[0], item[1]))
        _, _, offset, box = candidates[0]
        selected_offsets[idx] = offset
        placed_boxes.append(box)
    return selected_offsets


def _emt_realtime_label_text(row: Any) -> str:
    name = _short_parking_name(getattr(row, "nombre", None))
    free = getattr(row, "free_valid", pd.NA)
    return f"{name}\n{int(free) if pd.notna(free) else 's/d'} libres"


def _label_box_for_offset(
    *,
    ax: plt.Axes,
    xy: tuple[float, float],
    label: str,
    offset: tuple[int, int],
    fontsize: float,
    point_to_pixel: float,
) -> tuple[float, float, float, float]:
    x, y = ax.transData.transform(xy)
    x += offset[0] * point_to_pixel
    y += offset[1] * point_to_pixel
    lines = label.splitlines() or [label]
    width = max(len(line) for line in lines) * fontsize * 0.58 * point_to_pixel
    height = len(lines) * fontsize * 1.25 * point_to_pixel
    pad = 1.5 * point_to_pixel
    ha, va = _label_alignment_for_offset(offset)

    if ha == "left":
        x0, x1 = x, x + width
    elif ha == "right":
        x0, x1 = x - width, x
    else:
        x0, x1 = x - width / 2, x + width / 2

    if va == "bottom":
        y0, y1 = y, y + height
    elif va == "top":
        y0, y1 = y - height, y
    else:
        y0, y1 = y - height / 2, y + height / 2
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _box_overlap_area(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> float:
    x_overlap = max(0.0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0]))
    y_overlap = max(0.0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1]))
    return x_overlap * y_overlap


def _label_alignment_for_offset(offset: tuple[int, int]) -> tuple[str, str]:
    dx, dy = offset
    ha = "center"
    if dx > 0:
        ha = "left"
    elif dx < 0:
        ha = "right"

    va = "center"
    if dy > 0:
        va = "bottom"
    elif dy < 0:
        va = "top"
    return ha, va


def save_emt_realtime_zoom_figure(
    layers: dict[str, gpd.GeoDataFrame],
    output_path: Path,
    scenario: dict[str, Any],
    *,
    radius_m: float = 1800,
) -> Path:
    emt_live = layers.get("emt_realtime_map")
    if emt_live is None or emt_live.empty:
        raise ValueError("No hay aparcamientos EMT con ocupación viva para guardar PNG.")
    minx, miny, maxx, maxy = _dense_points_window(emt_live, radius_m=radius_m)
    window_geom = box(minx, miny, maxx, maxy)
    emt_zoom = emt_live.loc[emt_live.geometry.intersects(window_geom).fillna(False)].copy()
    if emt_zoom.empty:
        emt_zoom = emt_live.copy()

    fig, ax = plt.subplots(figsize=(10, 10))
    if "callejero_map" in layers:
        callejero_zoom = layers["callejero_map"].loc[
            layers["callejero_map"].geometry.intersects(window_geom).fillna(False)
        ]
        if not callejero_zoom.empty:
            callejero_zoom.plot(
                ax=ax,
                color="#f8fafc",
                edgecolor="#d1d5db",
                linewidth=0.18,
                alpha=0.9,
                zorder=1,
            )
    if "limite_map" in layers:
        layers["limite_map"].boundary.plot(
            ax=ax,
            color="#111827",
            linewidth=1.0,
            alpha=0.75,
            zorder=2,
        )
    if "bandas_map" in layers:
        bandas_zoom = layers["bandas_map"].loc[
            layers["bandas_map"].geometry.intersects(window_geom).fillna(False)
        ].copy()
        if not bandas_zoom.empty:
            for color_key, color_value in COLOR_STYLE.items():
                subset = bandas_zoom.loc[bandas_zoom["color"].eq(color_key)]
                if subset.empty:
                    continue
                subset.plot(
                    ax=ax,
                    color=color_value,
                    linewidth=1.1,
                    alpha=0.55,
                    zorder=3,
                )

    plotted_categories: list[str] = []
    for category, label in EMT_REALTIME_CATEGORY_LABELS.items():
        subset = emt_zoom.loc[emt_zoom["categoria_disponibilidad_emt"].eq(category)]
        if subset.empty:
            continue
        plotted_categories.append(category)
        subset.plot(
            ax=ax,
            color=EMT_REALTIME_CATEGORY_COLORS[category],
            edgecolor="#111827",
            linewidth=0.8,
            markersize=110,
            label=label,
            alpha=0.98,
            zorder=6,
        )

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    label_fontsize = 6.3
    label_offsets = _emt_realtime_label_offsets(
        emt_zoom,
        ax=ax,
        fontsize=label_fontsize,
    )
    for row in emt_zoom.itertuples(index=True):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        label = _emt_realtime_label_text(row)
        offset = label_offsets.get(row.Index, (0, 10))
        ha, va = _label_alignment_for_offset(offset)
        text = ax.annotate(
            label,
            xy=(geom.x, geom.y),
            xytext=offset,
            textcoords="offset points",
            ha=ha,
            va=va,
            fontsize=label_fontsize,
            fontweight="bold",
            color="#111827",
            clip_on=True,
            zorder=7,
        )
        text.set_path_effects(
            [path_effects.Stroke(linewidth=2.1, foreground="white"), path_effects.Normal()]
        )

    ax.set_axis_off()
    query_label = scenario.get("emt_query_timestamp_label") or "consulta sin hora"
    ax.set_title(
        (
            f"EMT tiempo real — disponibilidad viva ({query_label})\n"
            "Snapshot vivo parcial; no histórico ni predicción."
        ),
        fontsize=13,
        pad=14,
    )
    legend_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor=EMT_REALTIME_CATEGORY_COLORS[category],
            markeredgecolor="#111827",
            markeredgewidth=0.8,
            markersize=8,
            label=EMT_REALTIME_CATEGORY_LABELS[category],
        )
        for category in plotted_categories
    ]
    ax.legend(
        handles=legend_handles,
        title="Disponibilidad EMT",
        loc="lower left",
        frameon=True,
        framealpha=0.92,
        fontsize=8,
        title_fontsize=9,
    )
    return _save_figure(fig, output_path)


def build_ser_prediction_map_from_operational(
    *,
    operational: pd.DataFrame,
    scenario_metadata: dict[str, Any] | None = None,
    root: Path | None = None,
    target_year: int = 2026,
    expected_model_barrios: int = 65,
    expected_emt_entities: int = 85,
    visual_buffer_m: float = 25,
    web_simplify_m: float = 0.5,
    html_output_path: Path | None = None,
    png_output_path: Path | None = None,
) -> SERPredictionMapResult:
    root = (root or find_repo_root()).resolve()
    layers, checks, diagnostics = _base_ser_emt_layers_and_diagnostics(
        root=root,
        target_year=target_year,
        expected_model_barrios=expected_model_barrios,
        expected_emt_entities=expected_emt_entities,
        visual_buffer_m=visual_buffer_m,
    )
    prediction_barrios, prediction_checks, prediction_diagnostics = _prepare_prediction_barrios(
        layers["barrios_model_map"],
        operational,
        expected_model_barrios=expected_model_barrios,
    )
    checks.extend(prediction_checks)
    diagnostics.update(prediction_diagnostics)
    preliminary_checks_df = pd.DataFrame(
        checks,
        columns=["check_id", "status", "detail", "critical"],
    )
    preliminary_failing = preliminary_checks_df.loc[
        preliminary_checks_df["critical"].eq(True)
        & preliminary_checks_df["status"].eq("FAIL")
    ]
    if not preliminary_failing.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}"
            for row in preliminary_failing.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    scenario = _scenario_from_operational(operational, scenario_metadata)
    diagnostics["scenario"] = pd.DataFrame([scenario])
    layers["prediction_barrios"] = prediction_barrios

    checks_df = pd.DataFrame(checks, columns=["check_id", "status", "detail", "critical"])
    failing_critical = checks_df.loc[checks_df["critical"].eq(True) & checks_df["status"].eq("FAIL")]
    if not failing_critical.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}" for row in failing_critical.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    fmap = build_ser_prediction_base_map(
        layers,
        scenario,
        web_simplify_m=web_simplify_m,
    )

    outputs_rows: list[dict[str, Any]] = []
    if html_output_path is not None:
        html_output_path = root / html_output_path if not html_output_path.is_absolute() else html_output_path
        html_output_path.parent.mkdir(parents=True, exist_ok=True)
        fmap.save(html_output_path)
        outputs_rows.append(
            {"output": "html_prediccion_proxy", "path": relpath(html_output_path, root)}
        )
        _append_check(
            checks,
            "html_prediction_generated",
            "OK" if html_output_path.exists() else "FAIL",
            relpath(html_output_path, root),
            True,
        )
    if png_output_path is not None:
        png_output_path = root / png_output_path if not png_output_path.is_absolute() else png_output_path
        save_ser_prediction_static_figure(
            layers,
            png_output_path,
            scenario,
            visual_buffer_m=visual_buffer_m,
        )
        outputs_rows.append(
            {"output": "png_prediccion_proxy", "path": relpath(png_output_path, root)}
        )
        _append_check(
            checks,
            "png_prediction_generated",
            "OK" if png_output_path.exists() else "FAIL",
            relpath(png_output_path, root),
            True,
        )

    checks_df = pd.DataFrame(checks, columns=["check_id", "status", "detail", "critical"])
    failing_critical = checks_df.loc[checks_df["critical"].eq(True) & checks_df["status"].eq("FAIL")]
    if not failing_critical.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}" for row in failing_critical.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    outputs = pd.DataFrame(outputs_rows, columns=["output", "path"])
    if not outputs.empty:
        outputs["exists"] = outputs["path"].map(lambda p: (root / p).exists())
        outputs["size_mb"] = outputs["path"].map(
            lambda p: round((root / p).stat().st_size / 1024**2, 3) if (root / p).exists() else pd.NA
        )

    return SERPredictionMapResult(
        folium_map=fmap,
        layers=layers,
        checks=checks_df,
        diagnostics=diagnostics,
        outputs=outputs,
        scenario=scenario,
    )


def _emt_realtime_scenario_fields(
    emt_realtime_layer: gpd.GeoDataFrame,
    emt_realtime_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = emt_realtime_metadata or {}
    query_value = metadata.get("query_timestamp_utc")
    if query_value is None and "query_timestamp" in emt_realtime_layer.columns:
        query_values = emt_realtime_layer["query_timestamp"].dropna()
        query_value = query_values.iloc[0] if not query_values.empty else None

    moment_label = None
    if "moment" in emt_realtime_layer.columns:
        moments = pd.to_datetime(emt_realtime_layer["moment"], errors="coerce").dropna()
        if not moments.empty:
            moment_min = moments.min()
            moment_max = moments.max()
            if moment_min == moment_max:
                moment_label = _format_datetime_value(moment_max)
            else:
                moment_label = (
                    f"{_format_datetime_value(moment_min)} – "
                    f"{_format_datetime_value(moment_max)}"
                )
    return {
        "emt_query_timestamp": query_value,
        "emt_query_timestamp_label": _format_datetime_value(query_value),
        "emt_moment_label": moment_label,
    }


def build_ser_prediction_map_with_emt_realtime(
    *,
    root: Path,
    operational: pd.DataFrame,
    scenario_metadata: dict[str, Any],
    emt_realtime_joined: pd.DataFrame,
    emt_realtime_metadata: dict[str, Any],
    html_output_path: Path | None = None,
    png_emt_realtime_zoom_output_path: Path | None = None,
    target_year: int | None = None,
    visual_buffer_m: float = 25,
    web_simplify_m: float = 0.5,
    expected_model_barrios: int = 65,
    expected_emt_entities: int = 85,
) -> SERPredictionMapResult:
    root = root.resolve()
    if target_year is None:
        target_year = int(scenario_metadata.get("target_year", 2026))

    layers, checks, diagnostics = _base_ser_emt_layers_and_diagnostics(
        root=root,
        target_year=target_year,
        expected_model_barrios=expected_model_barrios,
        expected_emt_entities=expected_emt_entities,
        visual_buffer_m=visual_buffer_m,
    )
    prediction_barrios, prediction_checks, prediction_diagnostics = _prepare_prediction_barrios(
        layers["barrios_model_map"],
        operational,
        expected_model_barrios=expected_model_barrios,
    )
    checks.extend(prediction_checks)
    diagnostics.update(prediction_diagnostics)

    emt_realtime_name_mismatches = emt_realtime_joined.loc[
        _emt_realtime_name_mismatch_mask(emt_realtime_joined)
    ].copy()
    emt_realtime_layer = prepare_emt_realtime_layer(emt_realtime_joined)
    limite_geom = _union_geometry(layers["limite_map"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    realtime_in_visual_mask = emt_realtime_layer.geometry.intersects(visual_area).fillna(False)
    emt_realtime_map = emt_realtime_layer.loc[realtime_in_visual_mask].copy()
    emt_realtime_outside = emt_realtime_layer.loc[~realtime_in_visual_mask].copy()
    layers["prediction_barrios"] = prediction_barrios
    layers["emt_realtime_live_all"] = emt_realtime_layer
    layers["emt_realtime_map"] = emt_realtime_map
    realtime_ids = (
        set(emt_realtime_map["id_emt"].dropna().astype(int))
        if "id_emt" in emt_realtime_map.columns
        else set()
    )
    emt_inventory_for_final = layers["emt_map"].copy()
    if "id_emt" not in emt_inventory_for_final.columns and "id_emt_referencia" in emt_inventory_for_final.columns:
        emt_inventory_for_final["id_emt"] = emt_inventory_for_final["id_emt_referencia"]
    if realtime_ids and "id_emt" in emt_inventory_for_final.columns:
        inventory_ids = pd.to_numeric(emt_inventory_for_final["id_emt"], errors="coerce")
        n_inventory_static_shown = int(
            (inventory_ids.isna() | ~inventory_ids.astype("Int64").isin(realtime_ids)).sum()
        )
    else:
        n_inventory_static_shown = len(emt_inventory_for_final)
    diagnostics["emt_realtime_layer"] = pd.DataFrame(
        [
            {
                "n_joined_rows": len(emt_realtime_joined),
                "n_live_joined_before_name_filter": int(
                    emt_realtime_joined["has_live_free"].fillna(False).astype(bool).sum()
                )
                if "has_live_free" in emt_realtime_joined
                else pd.NA,
                "n_live_excluded_name_mismatch": len(emt_realtime_name_mismatches),
                "n_live_rows": len(emt_realtime_layer),
                "n_live_rows_visible": len(emt_realtime_map),
                "n_inventory_static_shown_without_live": n_inventory_static_shown,
                "n_live_with_capacity_reference": int(
                    emt_realtime_map["plazas_referencia"].notna().sum()
                )
                if "plazas_referencia" in emt_realtime_map
                else 0,
                "n_live_with_pct_reference": int(
                    emt_realtime_map["pct_libre_referencia"].notna().sum()
                )
                if "pct_libre_referencia" in emt_realtime_map
                else 0,
            }
        ]
    )
    diagnostics["emt_realtime_spatial_filter"] = pd.DataFrame(
        [
            {
                "n_emt_realtime_live_total": len(emt_realtime_layer),
                "n_emt_realtime_live_in_visual_area": len(emt_realtime_map),
                "n_emt_realtime_live_outside_visual_area": len(emt_realtime_outside),
                "visual_buffer_m": visual_buffer_m,
            }
        ]
    )
    excluded_columns = ["id_emt", "nombre", "free_valid", "latitud", "longitud"]
    diagnostics["emt_realtime_outside_visual_area"] = emt_realtime_outside.loc[
        :, [column for column in excluded_columns if column in emt_realtime_outside.columns]
    ].copy()
    mismatch_columns = [
        "parking_uid",
        "id_emt",
        "nombre",
        "realtime_name",
        "realtime_address",
        "free_valid",
        "moment",
    ]
    diagnostics["emt_realtime_name_mismatch_excluded"] = emt_realtime_name_mismatches.loc[
        :,
        [column for column in mismatch_columns if column in emt_realtime_name_mismatches.columns],
    ].copy()
    if "categoria_disponibilidad_emt" in emt_realtime_map:
        diagnostics["emt_realtime_categories"] = (
            emt_realtime_map.groupby(
                ["categoria_disponibilidad_emt", "categoria_disponibilidad_emt_label"],
                dropna=False,
            )
            .size()
            .reset_index(name="n_aparcamientos")
        )
    else:
        diagnostics["emt_realtime_categories"] = pd.DataFrame(
            columns=[
                "categoria_disponibilidad_emt",
                "categoria_disponibilidad_emt_label",
                "n_aparcamientos",
            ]
        )
    _append_check(
        checks,
        "emt_realtime_live_layer_not_empty",
        "OK" if not emt_realtime_map.empty else "WARNING",
        f"live_rows_visible={len(emt_realtime_map)}; live_rows_total={len(emt_realtime_layer)}",
        False,
    )
    _append_check(
        checks,
        "emt_realtime_outside_visual_area",
        "WARNING" if len(emt_realtime_outside) else "OK",
        f"outside={len(emt_realtime_outside)}; in_visual_area={len(emt_realtime_map)}",
        False,
    )

    scenario = _scenario_from_operational(operational, scenario_metadata)
    scenario.update(_emt_realtime_scenario_fields(emt_realtime_map, emt_realtime_metadata))
    scenario["emt_has_residual_capacity_category"] = bool(
        "categoria_disponibilidad_emt" in emt_realtime_map.columns
        and emt_realtime_map["categoria_disponibilidad_emt"]
        .eq("disponibilidad_informada_sin_capacidad")
        .any()
    )
    diagnostics["scenario"] = pd.DataFrame([scenario])

    checks_df = pd.DataFrame(checks, columns=["check_id", "status", "detail", "critical"])
    failing_critical = checks_df.loc[
        checks_df["critical"].eq(True) & checks_df["status"].eq("FAIL")
    ]
    if not failing_critical.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}" for row in failing_critical.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    fmap = build_ser_prediction_base_map(
        layers,
        scenario,
        web_simplify_m=web_simplify_m,
        emt_realtime_layer=emt_realtime_map,
        show_emt_inventory=False,
        controls_builder=_add_prediction_emt_realtime_map_controls,
    )

    outputs_rows: list[dict[str, Any]] = []
    if html_output_path is not None:
        html_output_path = root / html_output_path if not html_output_path.is_absolute() else html_output_path
        html_output_path.parent.mkdir(parents=True, exist_ok=True)
        fmap.save(html_output_path)
        outputs_rows.append(
            {"output": "html_ser_emt_tiempo_real_proxy", "path": relpath(html_output_path, root)}
        )
        _append_check(
            checks,
            "html_ser_emt_realtime_proxy_generated",
            "OK" if html_output_path.exists() else "FAIL",
            relpath(html_output_path, root),
            True,
        )
    if png_emt_realtime_zoom_output_path is not None:
        png_path = (
            root / png_emt_realtime_zoom_output_path
            if not png_emt_realtime_zoom_output_path.is_absolute()
            else png_emt_realtime_zoom_output_path
        )
        save_emt_realtime_zoom_figure(layers, png_path, scenario)
        outputs_rows.append(
            {"output": "png_emt_tiempo_real_zoom", "path": relpath(png_path, root)}
        )
        _append_check(
            checks,
            "png_emt_realtime_zoom_generated",
            "OK" if png_path.exists() else "FAIL",
            relpath(png_path, root),
            True,
        )

    checks_df = pd.DataFrame(checks, columns=["check_id", "status", "detail", "critical"])
    failing_critical = checks_df.loc[
        checks_df["critical"].eq(True) & checks_df["status"].eq("FAIL")
    ]
    if not failing_critical.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}" for row in failing_critical.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    outputs = pd.DataFrame(outputs_rows, columns=["output", "path"])
    if not outputs.empty:
        outputs["exists"] = outputs["path"].map(lambda p: (root / p).exists())
        outputs["size_mb"] = outputs["path"].map(
            lambda p: round((root / p).stat().st_size / 1024**2, 3)
            if (root / p).exists()
            else pd.NA
        )

    return SERPredictionMapResult(
        folium_map=fmap,
        layers=layers,
        checks=checks_df,
        diagnostics=diagnostics,
        outputs=outputs,
        scenario=scenario,
    )


def build_ser_emt_map_from_paths(
    *,
    root: Path | None = None,
    target_year: int = 2026,
    expected_model_barrios: int = 65,
    expected_emt_entities: int = 85,
    visual_buffer_m: float = 25,
    web_simplify_m: float = 0.5,
    html_output_path: Path | None = None,
    png_output_path: Path | None = None,
    barrios_model_png_output_path: Path | None = None,
) -> SEREMTMapResult:
    root = (root or find_repo_root()).resolve()
    checks: list[dict[str, Any]] = []
    diagnostics: dict[str, pd.DataFrame] = {}
    outputs_rows: list[dict[str, Any]] = []

    ser_paths = {dataset_id: root / relative for dataset_id, relative in SER_INPUT_PATHS.items()}
    ser_files_exist = all(path.exists() for path in ser_paths.values())
    _append_check(
        checks,
        "ser_layers_exist",
        "OK" if ser_files_exist else "FAIL",
        "; ".join(f"{k}={relpath(v, root)}:{v.exists()}" for k, v in ser_paths.items()),
        True,
    )
    layers = read_ser_map_layers(root)
    _append_check(
        checks,
        "ser_layers_not_empty",
        "OK" if all(not gdf.empty for gdf in layers.values()) else "FAIL",
        "; ".join(f"{k}:rows={len(v)}" for k, v in layers.items()),
        True,
    )
    ser_crs_ok = all(gdf.crs is not None and gdf.crs.to_epsg() == 25830 for gdf in layers.values())
    _append_check(
        checks,
        "ser_crs_epsg_25830",
        "OK" if ser_crs_ok else "FAIL",
        "; ".join(f"{k}:epsg={v.crs.to_epsg() if v.crs else None}" for k, v in layers.items()),
        True,
    )

    capacidad_target = _ser_capacity_target(root, target_year)
    if capacidad_target.empty:
        _append_check(
            checks,
            "ser_capacity_target_not_empty",
            "FAIL",
            f"target_year={target_year}",
            True,
        )

    emt_inventory, emt_checks = read_emt_inventory_as_gdf(
        root / EMT_INVENTORY_PATH,
        expected_emt_entities=expected_emt_entities,
    )
    checks.extend(emt_checks.to_dict("records"))
    diagnostics["emt_inventory"] = pd.DataFrame(
        [
            {
                "n_rows": len(emt_inventory),
                "n_parking_uid": (
                    emt_inventory["parking_uid"].nunique()
                    if "parking_uid" in emt_inventory
                    else 0
                ),
                "crs_epsg": emt_inventory.crs.to_epsg() if emt_inventory.crs else None,
            }
        ]
    )

    barrios_model_map, barrio_diagnostics = build_model_compatible_barrios(
        layers["ser_geoportal_barrios_ser"],
        capacidad_target,
        expected_model_barrios=expected_model_barrios,
    )
    diagnostics.update(barrio_diagnostics)

    original_barrios = layers["ser_geoportal_barrios_ser"].copy()
    original_barrios["barrio_key"] = make_barrio_key(original_barrios)
    carto_rows_ok = len(original_barrios) == 66
    carto_keys_ok = original_barrios["barrio_key"].nunique(dropna=True) == expected_model_barrios
    model_rows_ok = len(barrios_model_map) == expected_model_barrios
    model_keys = set(capacidad_target["barrio_key"].dropna().astype(str))
    map_keys = set(barrios_model_map["barrio_key"].dropna().astype(str))
    keys_match = model_keys == map_keys
    dup_0904 = barrio_diagnostics["barrios_duplicate_keys"].loc[
        lambda df: df["barrio_key"].eq("09_04")
    ]
    dup_0904_ok = (
        len(dup_0904) == 1
        and int(dup_0904.iloc[0]["n_geometrias_origen"]) == 2
        and "Valdezarza Fase III" in str(dup_0904.iloc[0]["nombres_cartograficos"])
    )

    _append_check(checks, "barrios_original_rows_66", "OK" if carto_rows_ok else "FAIL", f"rows={len(original_barrios)}", True)
    _append_check(checks, "barrios_original_unique_keys_65", "OK" if carto_keys_ok else "FAIL", f"unique={original_barrios['barrio_key'].nunique(dropna=True)}", True)
    _append_check(checks, "barrios_model_map_rows_65", "OK" if model_rows_ok else "FAIL", f"rows={len(barrios_model_map)}", True)
    _append_check(
        checks,
        "barrios_model_and_map_keys_match",
        "OK" if keys_match else "FAIL",
        f"map_not_model={sorted(map_keys - model_keys)}; model_not_map={sorted(model_keys - map_keys)}",
        True,
    )
    _append_check(
        checks,
        "barrios_duplicate_09_04_documented",
        "OK" if dup_0904_ok else "FAIL",
        dup_0904.to_dict("records"),
        True,
    )

    map_layers = prepare_ser_map_views(
        layers,
        barrios_model_map,
        visual_buffer_m=visual_buffer_m,
    )
    limite_geom = _union_geometry(layers["ser_geoportal_limite_ser"])
    visual_area = limite_geom.buffer(visual_buffer_m)
    emt_map = emt_inventory.loc[
        emt_inventory.geometry.intersects(visual_area).fillna(False)
    ].copy()
    n_emt_outside_visual_area = len(emt_inventory) - len(emt_map)
    diagnostics["emt_spatial_filter"] = pd.DataFrame(
        [
            {
                "n_emt_inventory_total": len(emt_inventory),
                "n_emt_in_visual_area": len(emt_map),
                "n_emt_outside_visual_area": n_emt_outside_visual_area,
                "visual_buffer_m": visual_buffer_m,
            }
        ]
    )
    _append_check(
        checks,
        "emt_outside_visual_area",
        "WARNING" if n_emt_outside_visual_area > 0 else "OK",
        (
            f"outside={n_emt_outside_visual_area}; "
            f"in_visual_area={len(emt_map)}; inventory_total={len(emt_inventory)}"
        ),
        False,
    )
    map_layers["emt_inventory"] = emt_inventory
    map_layers["emt_map"] = emt_map

    plazas_diagnostic = _ser_plazas_diagnostic(map_layers["bandas_map"], capacidad_target)
    diagnostics["ser_plazas"] = plazas_diagnostic
    plazas_status = plazas_diagnostic.iloc[0]["status"]
    _append_check(
        checks,
        "ser_plazas_bandas_vs_capacidad",
        plazas_status,
        plazas_diagnostic.iloc[0].to_dict(),
        plazas_status == "FAIL",
    )

    fmap = build_ser_emt_base_map(map_layers, web_simplify_m=web_simplify_m)

    if html_output_path is not None:
        html_output_path = root / html_output_path if not html_output_path.is_absolute() else html_output_path
        html_output_path.parent.mkdir(parents=True, exist_ok=True)
        fmap.save(html_output_path)
        outputs_rows.append({"output": "html_integrado", "path": relpath(html_output_path, root)})
        _append_check(
            checks,
            "html_generated",
            "OK" if html_output_path.exists() else "FAIL",
            relpath(html_output_path, root),
            True,
        )
    if png_output_path is not None:
        png_output_path = root / png_output_path if not png_output_path.is_absolute() else png_output_path
        save_ser_emt_static_figure(
            map_layers,
            png_output_path,
            visual_buffer_m=visual_buffer_m,
        )
        outputs_rows.append({"output": "png_integrado", "path": relpath(png_output_path, root)})
        _append_check(
            checks,
            "png_integrated_generated",
            "OK" if png_output_path.exists() else "FAIL",
            relpath(png_output_path, root),
            True,
        )
    if barrios_model_png_output_path is not None:
        barrios_model_png_output_path = (
            root / barrios_model_png_output_path
            if not barrios_model_png_output_path.is_absolute()
            else barrios_model_png_output_path
        )
        save_model_barrios_figure(
            map_layers,
            barrios_model_png_output_path,
            visual_buffer_m=visual_buffer_m,
        )
        outputs_rows.append(
            {"output": "png_barrios_modelo", "path": relpath(barrios_model_png_output_path, root)}
        )
        _append_check(
            checks,
            "png_model_barrios_generated",
            "OK" if barrios_model_png_output_path.exists() else "FAIL",
            relpath(barrios_model_png_output_path, root),
            True,
        )

    checks_df = pd.DataFrame(checks, columns=["check_id", "status", "detail", "critical"])
    failing_critical = checks_df.loc[checks_df["critical"].eq(True) & checks_df["status"].eq("FAIL")]
    if not failing_critical.empty:
        detail = "; ".join(
            f"{row.check_id}: {row.detail}" for row in failing_critical.itertuples(index=False)
        )
        raise ValueError(f"Critical checks failed: {detail}")

    outputs = pd.DataFrame(outputs_rows, columns=["output", "path"])
    if not outputs.empty:
        outputs["exists"] = outputs["path"].map(lambda p: (root / p).exists())
        outputs["size_mb"] = outputs["path"].map(
            lambda p: round((root / p).stat().st_size / 1024**2, 3) if (root / p).exists() else pd.NA
        )

    return SEREMTMapResult(
        folium_map=fmap,
        layers=map_layers,
        checks=checks_df,
        diagnostics=diagnostics,
        outputs=outputs,
    )
