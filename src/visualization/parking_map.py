from __future__ import annotations

import os
from html import escape
from dataclasses import dataclass
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
    "azul": "azul",
    "verde": "verde",
    "alta_rotacion": "alta rotación",
    "rojo": "rojo",
    "naranja": "naranja",
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


def build_ser_emt_base_map(
    layers: dict[str, gpd.GeoDataFrame],
    *,
    web_simplify_m: float = 0.5,
) -> folium.Map:
    limite_web = to_web(layers["limite_map"], web_simplify_m)
    barrios_web = to_web(layers["barrios_model_map"], web_simplify_m).reset_index(drop=True)
    barrios_web = barrios_web.loc[:, ["barrio", "barrio_key", "geometry"]]
    bandas_web = to_web(layers["bandas_map"], web_simplify_m)
    bandas_web["color_label"] = bandas_web["color"].map(COLOR_LABEL).fillna(
        bandas_web["color"].astype("string")
    )
    bandas_web = bandas_web.loc[:, ["color", "color_label", "numero_plazas", "geometry"]]
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
        tooltip_fields=["color_label", "numero_plazas"],
        tooltip_aliases=["Color SER", "Número de plazas"],
        popup_fields=["color_label", "numero_plazas"],
        popup_aliases=["Color SER", "Número de plazas"],
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
