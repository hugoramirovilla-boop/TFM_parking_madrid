from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.emt_realtime import EMTRealtimeResult, build_emt_realtime_from_api
from src.models.ser_parking_proxy import (
    SERParkingProxyResult,
    build_ser_parking_proxy_from_paths,
)
from src.visualization.parking_map import (
    SERPredictionMapResult,
    build_ser_prediction_map_with_emt_realtime,
)


SER_PROXY_PATHS = {
    "m0_profiles_path": Path("data/processed/core/ser/modeling/ser_m0_selected_profiles.parquet"),
    "m0_metadata_path": Path("data/processed/core/ser/modeling/ser_m0_selected_model_metadata.json"),
    "capacidad_ser_path": Path("data/processed/core/ser/ser_barrio_capacidad_anio.parquet"),
    "autorizaciones_path": Path("data/interim/ser/ser_autorizaciones/ser_autorizaciones_clean.parquet"),
    "ivtm_cero_path": Path(
        "data/interim/ser/ser_padron_vehiculos_ivtm_barrio/"
        "ser_padron_vehiculos_ivtm_barrio_clean.parquet"
    ),
    "calendario_laboral_path": Path(
        "data/interim/contexto/contexto_calendario_laboral/"
        "contexto_calendario_laboral_clean.parquet"
    ),
}
EMT_INVENTORY_PATH = Path("data/processed/core/emt/inventario_global_emt.parquet")
DEFAULT_HTML_OUTPUT_PATH = Path("reports/maps/mapa_integrado_ser_proxy_emt_tiempo_real.html")
DEFAULT_PNG_EMT_REALTIME_ZOOM_OUTPUT_PATH = Path(
    "reports/figures/emt_tiempo_real/mapa_emt_tiempo_real_zoom.png"
)
DEFAULT_RAW_XML_OUTPUT_PATH = Path(
    "data/raw/emt/emt_aparcamientos_rotacionales_tiempo_real/"
    "emt_aparcamientos_rotacionales_tiempo_real__latest.xml"
)
DEFAULT_INTERIM_REALTIME_OUTPUT_PATH = Path(
    "data/interim/emt/emt_aparcamientos_rotacionales_tiempo_real/"
    "emt_aparcamientos_rotacionales_tiempo_real_latest.parquet"
)
DEFAULT_INTERIM_JOIN_OUTPUT_PATH = Path(
    "data/interim/emt/emt_aparcamientos_rotacionales_tiempo_real/"
    "emt_realtime_inventory_join_latest.parquet"
)


@dataclass
class OperationalSEREMTMapResult:
    ser_proxy: SERParkingProxyResult
    emt_realtime: EMTRealtimeResult
    map_result: SERPredictionMapResult
    checks: pd.DataFrame
    metadata: dict[str, Any]
    diagnostics: dict[str, pd.DataFrame]
    outputs: pd.DataFrame


def build_operational_ser_emt_realtime_map(
    *,
    root: Path | str | None = None,
    scenario_datetime: str | pd.Timestamp | None = None,
    timezone: str = "Europe/Madrid",
    ivtm_reference_year: int = 2025,
    expected_n_barrios: int = 65,
    strict: bool = True,
    write_outputs: bool = True,
    write_realtime_snapshots: bool | None = None,
    html_output_path: Path | str | None = None,
    png_emt_realtime_zoom_output_path: Path | str | None = None,
) -> OperationalSEREMTMapResult:
    """Ejecuta el flujo operativo final SER proxy + EMT tiempo real + mapa."""
    root_path = _find_repo_root() if root is None else Path(root).resolve()
    if write_realtime_snapshots is None:
        write_realtime_snapshots = write_outputs
    if not write_outputs:
        write_realtime_snapshots = False

    scenario_requested = _scenario_datetime_or_now(scenario_datetime, timezone)
    html_path = _resolve_output_path(
        root_path,
        html_output_path,
        DEFAULT_HTML_OUTPUT_PATH,
        enabled=write_outputs,
    )
    png_path = _resolve_output_path(
        root_path,
        png_emt_realtime_zoom_output_path,
        DEFAULT_PNG_EMT_REALTIME_ZOOM_OUTPUT_PATH,
        enabled=write_outputs,
    )
    raw_xml_path = root_path / DEFAULT_RAW_XML_OUTPUT_PATH if write_realtime_snapshots else None
    interim_realtime_path = (
        root_path / DEFAULT_INTERIM_REALTIME_OUTPUT_PATH if write_realtime_snapshots else None
    )
    interim_join_path = (
        root_path / DEFAULT_INTERIM_JOIN_OUTPUT_PATH if write_realtime_snapshots else None
    )

    ser_proxy = build_ser_parking_proxy_from_paths(
        **{key: root_path / value for key, value in SER_PROXY_PATHS.items()},
        scenario_datetime=scenario_requested,
        ivtm_reference_year=ivtm_reference_year,
        output_mode="operational",
        expected_n_barrios=expected_n_barrios,
        interval_alignment="floor",
        strict=strict,
    )
    emt_realtime = build_emt_realtime_from_api(
        inventory_path=root_path / EMT_INVENTORY_PATH,
        raw_xml_output_path=raw_xml_path,
        interim_output_path=interim_realtime_path,
        joined_output_path=interim_join_path,
    )
    map_result = build_ser_prediction_map_with_emt_realtime(
        root=root_path,
        operational=ser_proxy.operational,
        scenario_metadata=ser_proxy.metadata,
        emt_realtime_joined=emt_realtime.joined,
        emt_realtime_metadata=emt_realtime.metadata,
        html_output_path=html_path,
        png_emt_realtime_zoom_output_path=png_path,
        expected_model_barrios=expected_n_barrios,
    )

    outputs = _combine_outputs(root_path, emt_realtime.outputs, map_result.outputs)
    metadata = _build_metadata(
        ser_proxy=ser_proxy,
        emt_realtime=emt_realtime,
        write_outputs=write_outputs,
        write_realtime_snapshots=write_realtime_snapshots,
        html_output_path=html_path,
        png_emt_realtime_zoom_output_path=png_path,
    )
    diagnostics = {
        "ser_proxy_metadata": pd.DataFrame([ser_proxy.metadata]),
        "emt_coverage_summary": emt_realtime.diagnostics.get(
            "coverage_summary", pd.DataFrame()
        ).copy(),
        "map_layer_coverage": _map_layer_coverage(map_result),
        "outputs": outputs.copy(),
    }
    diagnostics.update(
        {
            f"ser_proxy_{key}": value.copy()
            for key, value in ser_proxy.diagnostics.items()
        }
    )
    diagnostics.update(
        {
            f"emt_realtime_{key}": value.copy()
            for key, value in emt_realtime.diagnostics.items()
        }
    )
    diagnostics.update(
        {f"map_{key}": value.copy() for key, value in map_result.diagnostics.items()}
    )

    checks = _combine_checks(
        ser_proxy=ser_proxy,
        emt_realtime=emt_realtime,
        map_result=map_result,
        outputs=outputs,
        write_outputs=write_outputs,
    )
    if strict:
        failures = checks.loc[checks["critical"].eq(True) & checks["status"].eq("FAIL")]
        if not failures.empty:
            detail = "; ".join(
                f"{row.component}.{row.check_id}: {row.detail}"
                for row in failures.itertuples(index=False)
            )
            raise ValueError(f"Fallan checks críticos de orquestación: {detail}")

    return OperationalSEREMTMapResult(
        ser_proxy=ser_proxy,
        emt_realtime=emt_realtime,
        map_result=map_result,
        checks=checks,
        metadata=metadata,
        diagnostics=diagnostics,
        outputs=outputs,
    )


def _find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "data_catalog.csv").exists():
            return candidate
    raise FileNotFoundError("No se encontró data_catalog.csv desde el directorio actual.")


def _scenario_datetime_or_now(
    scenario_datetime: str | pd.Timestamp | None,
    timezone: str,
) -> pd.Timestamp:
    if scenario_datetime is None:
        return pd.Timestamp.now(tz=timezone).tz_localize(None)
    value = pd.Timestamp(scenario_datetime)
    if value.tzinfo is not None:
        return value.tz_convert(timezone).tz_localize(None)
    return value


def _resolve_output_path(
    root: Path,
    requested_path: Path | str | None,
    default_path: Path,
    *,
    enabled: bool,
) -> Path | None:
    if not enabled:
        return None
    path = Path(requested_path) if requested_path is not None else default_path
    return path if path.is_absolute() else root / path


def _component_checks(component: str, checks: pd.DataFrame) -> pd.DataFrame:
    out = checks.copy()
    if out.empty:
        out = pd.DataFrame(columns=["check_id", "status", "detail", "critical"])
    out.insert(0, "component", component)
    return out


def _combine_checks(
    *,
    ser_proxy: SERParkingProxyResult,
    emt_realtime: EMTRealtimeResult,
    map_result: SERPredictionMapResult,
    outputs: pd.DataFrame,
    write_outputs: bool,
) -> pd.DataFrame:
    checks = [
        _component_checks("ser_proxy", ser_proxy.checks),
        _component_checks("emt_realtime", emt_realtime.checks),
        _component_checks("map", map_result.checks),
        _component_checks("orchestration", _output_checks(outputs, write_outputs)),
    ]
    return pd.concat(checks, ignore_index=True, sort=False)


def _output_checks(outputs: pd.DataFrame, write_outputs: bool) -> pd.DataFrame:
    rows: list[dict[str, Any]] = [
        {
            "check_id": "write_outputs_policy",
            "status": "OK",
            "detail": f"write_outputs={write_outputs}",
            "critical": True,
        }
    ]
    if not write_outputs:
        rows.append(
            {
                "check_id": "outputs_not_written",
                "status": "OK" if outputs.empty else "FAIL",
                "detail": f"n_outputs={len(outputs)}",
                "critical": True,
            }
        )
    else:
        for row in outputs.itertuples(index=False):
            rows.append(
                {
                    "check_id": f"output_exists_{row.output}",
                    "status": "OK" if bool(row.exists) else "FAIL",
                    "detail": str(row.path),
                    "critical": True,
                }
            )
    return pd.DataFrame(rows, columns=["check_id", "status", "detail", "critical"])


def _combine_outputs(
    root: Path,
    emt_outputs: pd.DataFrame,
    map_outputs: pd.DataFrame,
) -> pd.DataFrame:
    frames = []
    for component, outputs in [
        ("emt_realtime", emt_outputs),
        ("map", map_outputs),
    ]:
        if outputs is None or outputs.empty:
            continue
        out = outputs.copy()
        out.insert(0, "component", component)
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=["component", "output", "path", "exists", "size_mb"])

    combined = pd.concat(frames, ignore_index=True, sort=False)
    output_paths = combined["path"].map(lambda value: _output_path(root, value))
    if "exists" not in combined.columns:
        combined["exists"] = output_paths.map(lambda path: path.exists())
    else:
        combined["exists"] = combined["exists"].fillna(
            output_paths.map(lambda path: path.exists())
        )
    if "size_mb" not in combined.columns:
        combined["size_mb"] = output_paths.map(_output_size_mb)
    else:
        combined["size_mb"] = combined["size_mb"].fillna(
            output_paths.map(_output_size_mb)
        )
    combined["path"] = output_paths.map(lambda path: _display_output_path(root, path))
    return combined.loc[:, ["component", "output", "path", "exists", "size_mb"]]


def _output_path(root: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _display_output_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _output_size_mb(path: Path) -> float | Any:
    if not path.exists():
        return pd.NA
    return round(path.stat().st_size / 1024**2, 3)


def _build_metadata(
    *,
    ser_proxy: SERParkingProxyResult,
    emt_realtime: EMTRealtimeResult,
    write_outputs: bool,
    write_realtime_snapshots: bool,
    html_output_path: Path | None,
    png_emt_realtime_zoom_output_path: Path | None,
) -> dict[str, Any]:
    ser_meta = ser_proxy.metadata
    emt_query_timestamp_utc = emt_realtime.metadata.get("query_timestamp_utc")
    moments = (
        pd.to_datetime(emt_realtime.realtime["moment"], errors="coerce").dropna()
        if "moment" in emt_realtime.realtime
        else pd.Series(dtype="datetime64[ns]")
    )
    return {
        "scenario_datetime_requested": ser_meta.get("scenario_datetime_requested"),
        "scenario_datetime_used": ser_meta.get("scenario_datetime_used"),
        "intervalo_inicio": ser_meta.get("intervalo_inicio"),
        "intervalo_fin": ser_meta.get("intervalo_fin"),
        "intervalo_ajustado_30min": ser_meta.get("intervalo_ajustado_30min"),
        "emt_query_timestamp_utc": emt_query_timestamp_utc,
        "emt_moment_min": moments.min() if not moments.empty else pd.NaT,
        "emt_moment_max": moments.max() if not moments.empty else pd.NaT,
        "write_outputs": bool(write_outputs),
        "write_realtime_snapshots": bool(write_realtime_snapshots),
        "html_output_path": str(html_output_path) if html_output_path is not None else None,
        "png_emt_realtime_zoom_output_path": (
            str(png_emt_realtime_zoom_output_path)
            if png_emt_realtime_zoom_output_path is not None
            else None
        ),
    }


def _map_layer_coverage(map_result: SERPredictionMapResult) -> pd.DataFrame:
    rows = []
    for layer_name in [
        "prediction_barrios",
        "emt_inventory",
        "emt_map",
        "emt_realtime_live_all",
        "emt_realtime_map",
    ]:
        layer = map_result.layers.get(layer_name)
        rows.append(
            {
                "layer": layer_name,
                "n_rows": len(layer) if layer is not None else 0,
                "available": layer is not None,
            }
        )
    return pd.DataFrame(rows)
