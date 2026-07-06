from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from src.models.ser_historical_baseline import (
    load_barrio_lookup,
    load_m0_profiles,
    make_map_scenarios,
    predict_m0_from_profiles,
)


W_M0 = 0.60
W_ESTRUCTURA = 0.40
W_RESIDENTES_IN_ESTRUCTURA = 0.75
W_CERO_IN_ESTRUCTURA = 0.25
INDEX_METHOD_ID = "indice_ajustado_principal_m0_060_estructura_040"
INTERPRETATION = (
    "prob_aparcar_proxy es una escala proxy relativa; no es una probabilidad "
    "real observada de encontrar plaza."
)

OPERATIONAL_COLUMNS = [
    "barrio_key",
    "barrio_nombre",
    "intervalo_inicio",
    "dia_semana_num",
    "fallback_level",
    "prob_aparcar_proxy",
]

TECHNICAL_COLUMNS = [
    "barrio_key",
    "barrio_nombre",
    "intervalo_inicio",
    "dia_semana_num",
    "intervalo_30min_id",
    "m0_pred",
    "m0_score",
    "fallback_level",
    "fallback_order",
    "plazas_barrio_anio",
    "n_aut_residente_activas",
    "ratio_residentes_plaza",
    "residentes_score",
    "residentes_missing_after_join",
    "n_turismos_distintivo_0",
    "ratio_cero_plaza",
    "cero_score",
    "componente_presion_no_observada",
    "indice_dificultad_ser_ajustado",
    "prob_aparcar_proxy",
    "index_method_id",
    "w_m0",
    "w_estructura",
    "w_residentes_in_estructura",
    "w_cero_in_estructura",
    "target_year",
    "ivtm_reference_year",
    "capacidad_missing_after_join",
    "ivtm_missing_after_join",
    "intervalo_ajustado_30min",
]


@dataclass
class SERParkingProxyResult:
    operational: pd.DataFrame
    top_ranking: pd.DataFrame | None
    technical: pd.DataFrame | None
    normalizers: pd.DataFrame | None
    checks: pd.DataFrame
    metadata: dict[str, Any]
    diagnostics: dict[str, pd.DataFrame]


def build_ser_parking_proxy_from_paths(
    *,
    m0_profiles_path: str | Path,
    m0_metadata_path: str | Path,
    capacidad_ser_path: str | Path,
    autorizaciones_path: str | Path,
    ivtm_cero_path: str | Path,
    calendario_laboral_path: str | Path,
    scenario_datetime: str | pd.Timestamp,
    ivtm_reference_year: int = 2025,
    barrio_key: str | Sequence[str] | None = None,
    top_n: int | None = None,
    ranking_by: Literal["facilidad", "dificultad"] = "facilidad",
    output_mode: Literal["operational", "technical", "both"] = "operational",
    expected_n_barrios: int = 65,
    interval_alignment: Literal["floor", "error"] = "floor",
    strict: bool = True,
) -> SERParkingProxyResult:
    """Construye el proxy SER desde rutas locales, sin escribir salidas."""
    profiles_df, m0_metadata = load_m0_profiles(m0_profiles_path, m0_metadata_path)
    capacidad_ser = pd.read_parquet(Path(capacidad_ser_path))
    autorizaciones = pd.read_parquet(Path(autorizaciones_path))
    ivtm_cero = pd.read_parquet(Path(ivtm_cero_path))
    calendario_laboral = pd.read_parquet(Path(calendario_laboral_path))
    scenario_meta, _, _ = _validate_calendar_scenario(
        calendario_laboral=calendario_laboral,
        scenario_datetime=scenario_datetime,
        interval_alignment=interval_alignment,
    )
    load_barrio_lookup(capacidad_ser_path, year=int(scenario_meta["target_year"]))

    return build_ser_parking_proxy(
        profiles_df=profiles_df,
        m0_metadata=m0_metadata,
        capacidad_ser=capacidad_ser,
        autorizaciones=autorizaciones,
        ivtm_cero=ivtm_cero,
        calendario_laboral=calendario_laboral,
        scenario_datetime=scenario_datetime,
        ivtm_reference_year=ivtm_reference_year,
        barrio_key=barrio_key,
        top_n=top_n,
        ranking_by=ranking_by,
        output_mode=output_mode,
        expected_n_barrios=expected_n_barrios,
        interval_alignment=interval_alignment,
        strict=strict,
    )


def build_ser_parking_proxy(
    *,
    profiles_df: pd.DataFrame,
    m0_metadata: dict[str, Any],
    capacidad_ser: pd.DataFrame,
    autorizaciones: pd.DataFrame,
    ivtm_cero: pd.DataFrame,
    calendario_laboral: pd.DataFrame,
    scenario_datetime: str | pd.Timestamp,
    ivtm_reference_year: int = 2025,
    barrio_key: str | Sequence[str] | None = None,
    top_n: int | None = None,
    ranking_by: Literal["facilidad", "dificultad"] = "facilidad",
    output_mode: Literal["operational", "technical", "both"] = "operational",
    expected_n_barrios: int = 65,
    interval_alignment: Literal["floor", "error"] = "floor",
    strict: bool = True,
) -> SERParkingProxyResult:
    """Genera `prob_aparcar_proxy` SER por barrio para un escenario de 30 minutos."""
    _validate_options(output_mode, ranking_by, interval_alignment)

    scenario_meta, calendar_checks, calendar_diag = _validate_calendar_scenario(
        calendario_laboral=calendario_laboral,
        scenario_datetime=scenario_datetime,
        interval_alignment=interval_alignment,
    )
    scenario_used = pd.Timestamp(scenario_meta["scenario_datetime_used"])
    target_year = int(scenario_meta["target_year"])

    barrio_lookup = _load_barrio_lookup_from_df(capacidad_ser, target_year)
    map_scenarios = make_map_scenarios(scenario_used, barrio_lookup)
    m0_pred = predict_m0_from_profiles(
        map_scenarios,
        profiles_df,
        barrio_lookup=barrio_lookup,
        intervalo_col="intervalo_inicio",
        pred_col="m0_pred",
        interval_alignment="floor",
    )

    capacidad_target = capacidad_ser.loc[
        pd.to_numeric(capacidad_ser["anio"], errors="coerce").eq(target_year)
    ].copy()
    residentes_barrio, residentes_diag = _compute_resident_authorizations_by_barrio(
        autorizaciones, scenario_used
    )
    ivtm_cero_barrio, ivtm_diag = _compute_ivtm_cero_by_barrio(
        ivtm_cero, ivtm_reference_year
    )

    base = _build_proxy_base_table(
        m0_pred=m0_pred,
        capacidad_target=capacidad_target,
        residentes_barrio=residentes_barrio,
        ivtm_cero_barrio=ivtm_cero_barrio,
        target_year=target_year,
        ivtm_reference_year=ivtm_reference_year,
    )
    normalizers, normalizers_valid = _compute_normalizers(profiles_df, base)
    technical_full = _apply_scores_and_index(base, normalizers)

    requested_keys = _normalize_requested_barrio_keys(barrio_key)
    top_ranking, top_n_clipped = _build_top_ranking(technical_full, top_n, ranking_by)

    metadata = _build_metadata(
        m0_metadata=m0_metadata,
        scenario_meta=scenario_meta,
        ivtm_reference_year=ivtm_reference_year,
        expected_n_barrios=expected_n_barrios,
        interval_alignment=interval_alignment,
    )

    diagnostics = {
        "calendar_scenario": calendar_diag,
        "residentes_ambito": residentes_diag["ambito"],
        "residentes_compuestos": residentes_diag["compuestos"],
        "residentes_no_barrio_summary": residentes_diag["no_barrio_summary"],
        "ivtm": ivtm_diag,
        "base_join": _base_join_diagnostics(base),
    }

    checks = _build_checks(
        calendar_checks=calendar_checks,
        technical=technical_full,
        capacidad_target=capacidad_target,
        ivtm_reference_year=ivtm_reference_year,
        ivtm_ref_rows=int(ivtm_diag.loc[ivtm_diag["metric"].eq("filas_ivtm_anio_referencia"), "value"].iloc[0]),
        normalizers=normalizers,
        normalizers_valid=normalizers_valid,
        m0_metadata=m0_metadata,
        scenario_datetime_used=scenario_used,
        expected_n_barrios=expected_n_barrios,
        requested_keys=requested_keys,
        top_n=top_n,
        top_n_clipped=top_n_clipped,
        residentes_missing_count=int(technical_full["residentes_missing_after_join"].sum()),
        compuestos_activos=int(
            residentes_diag["no_barrio_summary"]
            .loc[
                residentes_diag["no_barrio_summary"]["metric"].eq(
                    "filas_residentes_activas_no_barrio"
                ),
                "value",
            ]
            .iloc[0]
        ),
    )

    fail_critical = checks["critical"] & checks["status"].eq("FAIL")
    if strict and fail_critical.any():
        failed = checks.loc[fail_critical, ["check_id", "detail"]]
        raise ValueError(
            "Fallan checks críticos del proxy SER: "
            + "; ".join(f"{r.check_id}: {r.detail}" for r in failed.itertuples())
        )

    operational = _select_operational_output(technical_full, requested_keys)
    technical = (
        technical_full.loc[:, TECHNICAL_COLUMNS].copy()
        if output_mode in {"technical", "both"}
        else None
    )
    normalizers_out = normalizers.copy() if output_mode in {"technical", "both"} else None

    return SERParkingProxyResult(
        operational=operational,
        top_ranking=top_ranking,
        technical=technical,
        normalizers=normalizers_out,
        checks=checks,
        metadata=metadata,
        diagnostics=diagnostics,
    )


def _validate_options(
    output_mode: str,
    ranking_by: str,
    interval_alignment: str,
) -> None:
    if output_mode not in {"operational", "technical", "both"}:
        raise ValueError("output_mode debe ser 'operational', 'technical' o 'both'.")
    if ranking_by not in {"facilidad", "dificultad"}:
        raise ValueError("ranking_by debe ser 'facilidad' o 'dificultad'.")
    if interval_alignment not in {"floor", "error"}:
        raise ValueError("interval_alignment debe ser 'floor' o 'error'.")


def _validate_calendar_scenario(
    *,
    calendario_laboral: pd.DataFrame,
    scenario_datetime: str | pd.Timestamp,
    interval_alignment: Literal["floor", "error"],
) -> tuple[dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    """Valida calendario, codificación semanal y ventana SER observable."""
    required = {
        "fecha",
        "anio",
        "dia_semana_num",
        "tipo_dia_raw",
        "es_laborable",
        "es_sabado",
        "es_domingo",
        "es_festivo",
    }
    missing = required - set(calendario_laboral.columns)
    if missing:
        raise ValueError(f"Faltan columnas en calendario_laboral: {', '.join(sorted(missing))}.")

    requested = pd.Timestamp(scenario_datetime)
    used = requested.floor("30min")
    interval_end = used + pd.Timedelta(minutes=30)
    adjusted = requested != used

    if interval_alignment == "error" and adjusted:
        raise ValueError(
            "scenario_datetime debe caer exactamente al inicio de un intervalo de "
            "30 minutos cuando interval_alignment='error'."
        )

    cal = calendario_laboral.copy()
    cal["fecha"] = pd.to_datetime(cal["fecha"], errors="coerce").dt.normalize()
    scenario_date = used.normalize()
    row = cal.loc[cal["fecha"].eq(scenario_date)]
    if row.empty:
        raise ValueError(f"La fecha {scenario_date.date()} no existe en calendario_laboral.")
    if len(row) > 1:
        raise ValueError(f"La fecha {scenario_date.date()} está duplicada en calendario_laboral.")

    item = row.iloc[0]
    year_in_range = 2023 <= int(used.year) <= 2026
    if not year_in_range:
        raise ValueError(f"El año del escenario debe estar dentro de 2023-2026; recibido {used.year}.")

    dia_cal = int(item["dia_semana_num"])
    dia_m0 = int(used.dayofweek)
    weekday_ok = dia_cal - 1 == dia_m0
    if not weekday_ok:
        raise ValueError(
            "dia_semana_num del calendario no es coherente con la fecha real: "
            f"calendario={dia_cal}, m0={dia_m0}."
        )

    es_festivo = bool(item["es_festivo"])
    es_domingo = bool(item["es_domingo"])
    es_sabado = bool(item["es_sabado"])
    month = int(used.month)
    day = int(used.day)

    if es_domingo or es_festivo:
        ser_valid = False
        window_label = "sin_servicio_domingo_o_festivo"
    elif month == 8 or (month == 12 and day in {24, 31}) or es_sabado:
        ser_valid = _interval_contained(used, interval_end, "09:00", "15:00")
        window_label = "09:00-15:00"
    else:
        ser_valid = _interval_contained(used, interval_end, "09:00", "21:00")
        window_label = "09:00-21:00"

    if not ser_valid:
        raise ValueError(
            "El intervalo solicitado no cae dentro de una ventana SER observable: "
            f"intervalo=[{used}, {interval_end}); ventana={window_label}."
        )

    meta = {
        "scenario_datetime_requested": requested,
        "scenario_datetime_used": used,
        "intervalo_inicio": used,
        "intervalo_fin": interval_end,
        "interval_assignment": "contained_in_30min_interval",
        "intervalo_ajustado_30min": bool(adjusted),
        "target_year": int(used.year),
        "dia_semana_num_m0": dia_m0,
        "dia_semana_num_calendario": dia_cal,
        "tipo_dia_calendario": item["tipo_dia_raw"],
        "es_laborable": bool(item["es_laborable"]),
        "es_sabado": es_sabado,
        "es_domingo": es_domingo,
        "es_festivo": es_festivo,
        "ventana_ser_validada": window_label,
    }
    checks = [
        _check("scenario_in_calendar_2023_2026", year_in_range, f"year={used.year}", True),
        _check("scenario_in_ser_observable_window", ser_valid, f"ventana={window_label}", True),
        _check(
            "calendar_weekday_encoding_consistent",
            weekday_ok,
            f"calendario={dia_cal}; m0={dia_m0}",
            True,
        ),
    ]
    diag = pd.DataFrame(
        [
            {
                "fecha": scenario_date,
                "dia_semana_num_calendario": dia_cal,
                "dia_semana_num_m0": dia_m0,
                "tipo_dia_calendario": item["tipo_dia_raw"],
                "es_laborable": bool(item["es_laborable"]),
                "es_sabado": es_sabado,
                "es_domingo": es_domingo,
                "es_festivo": es_festivo,
                "ventana_ser_validada": window_label,
            }
        ]
    )
    return meta, checks, diag


def _interval_contained(
    start: pd.Timestamp,
    end: pd.Timestamp,
    window_start: str,
    window_end: str,
) -> bool:
    day = start.normalize()
    ws_hour, ws_minute = map(int, window_start.split(":"))
    we_hour, we_minute = map(int, window_end.split(":"))
    ws = day + pd.Timedelta(hours=ws_hour, minutes=ws_minute)
    we = day + pd.Timedelta(hours=we_hour, minutes=we_minute)
    return bool(start >= ws and end <= we)


def _make_barrio_key(
    df: pd.DataFrame,
    distrito_col: str = "cod_distrito",
    num_barrio_col: str = "num_barrio",
    cod_barrio_col: str = "cod_barrio",
) -> pd.Series:
    """Construye claves SER `DD_BB` desde columnas reales de distrito y barrio."""
    if distrito_col not in df.columns:
        raise ValueError(f"Falta la columna {distrito_col}.")

    cod_distrito = pd.to_numeric(df[distrito_col], errors="coerce")
    if num_barrio_col in df.columns:
        num_barrio = pd.to_numeric(df[num_barrio_col], errors="coerce")
    else:
        num_barrio = pd.Series(np.nan, index=df.index)

    if cod_barrio_col in df.columns:
        cod_barrio = pd.to_numeric(df[cod_barrio_col], errors="coerce")
        num_barrio = num_barrio.fillna(cod_barrio.mod(100))

    valid = cod_distrito.notna() & num_barrio.notna()
    key = pd.Series(pd.NA, index=df.index, dtype="string")
    key.loc[valid] = (
        cod_distrito.loc[valid].astype("Int64").astype(str).str.zfill(2)
        + "_"
        + num_barrio.loc[valid].astype("Int64").astype(str).str.zfill(2)
    )
    return key


def _compute_resident_authorizations_by_barrio(
    autorizaciones: pd.DataFrame,
    scenario_datetime_used: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    required = {
        "tipo_autorizacion",
        "ambito_espacial",
        "fecha_activacion",
        "fecha_vigencia",
        "cod_distrito",
    }
    missing = required - set(autorizaciones.columns)
    if missing:
        raise ValueError(f"Faltan columnas en autorizaciones: {', '.join(sorted(missing))}.")

    aut = autorizaciones.copy()
    aut["barrio_key"] = _make_barrio_key(aut)
    aut["fecha_activacion"] = pd.to_datetime(aut["fecha_activacion"], errors="coerce")
    aut["fecha_vigencia"] = pd.to_datetime(aut["fecha_vigencia"], errors="coerce")

    scenario_date = scenario_datetime_used.normalize()
    is_residente = aut["tipo_autorizacion"].eq("RESIDENTE")
    is_barrio = aut["ambito_espacial"].eq("barrio")
    is_active = (
        aut["fecha_activacion"].dt.normalize().le(scenario_date)
        & aut["fecha_vigencia"].dt.normalize().ge(scenario_date)
    )

    aut_residentes_activas = aut.loc[is_residente & is_barrio & is_active].copy()
    residentes_barrio = (
        aut_residentes_activas.dropna(subset=["barrio_key"])
        .groupby("barrio_key", as_index=False)
        .size()
        .rename(columns={"size": "n_aut_residente_activas"})
    )

    ambito_diag = (
        aut.loc[is_residente]
        .assign(activa_escenario=is_active)
        .groupby("ambito_espacial", dropna=False)
        .agg(
            filas_residentes=("tipo_autorizacion", "size"),
            filas_activas_escenario=("activa_escenario", "sum"),
            n_barrios_key=("barrio_key", "nunique"),
        )
        .reset_index()
        .sort_values("filas_residentes", ascending=False, kind="mergesort")
    )

    if "barrio" in aut.columns:
        compuestos_diag = (
            aut.loc[is_residente & aut["ambito_espacial"].eq("barrio_compuesto_ser")]
            .assign(activa_escenario=is_active)
            .groupby("barrio", dropna=False)
            .agg(
                filas_residentes=("tipo_autorizacion", "size"),
                filas_activas_escenario=("activa_escenario", "sum"),
            )
            .reset_index()
            .sort_values("filas_residentes", ascending=False, kind="mergesort")
        )
    else:
        compuestos_diag = pd.DataFrame()

    no_barrio_summary = pd.DataFrame(
        [
            {
                "metric": "filas_residentes_no_barrio",
                "value": int((is_residente & ~is_barrio).sum()),
            },
            {
                "metric": "filas_residentes_activas_no_barrio",
                "value": int((is_residente & ~is_barrio & is_active).sum()),
            },
            {
                "metric": "filas_residentes_no_barrio_son_compuestos",
                "value": bool(
                    aut.loc[is_residente & ~is_barrio, "ambito_espacial"]
                    .dropna()
                    .eq("barrio_compuesto_ser")
                    .all()
                ),
            },
        ]
    )

    return residentes_barrio, {
        "ambito": ambito_diag,
        "compuestos": compuestos_diag,
        "no_barrio_summary": no_barrio_summary,
    }


def _compute_ivtm_cero_by_barrio(
    ivtm_cero: pd.DataFrame,
    ivtm_reference_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"anio", "cod_distrito", "n_turismos_distintivo_0"}
    missing = required - set(ivtm_cero.columns)
    if missing:
        raise ValueError(f"Faltan columnas en ivtm_cero: {', '.join(sorted(missing))}.")

    ivtm = ivtm_cero.copy()
    ivtm["barrio_key"] = _make_barrio_key(ivtm)
    ivtm_ref = ivtm.loc[pd.to_numeric(ivtm["anio"], errors="coerce").eq(ivtm_reference_year)].copy()
    if ivtm_ref.empty:
        raise ValueError(
            f"No hay registros IVTM para ivtm_reference_year={ivtm_reference_year}. "
            "Usa un año disponible en la tabla IVTM limpia."
        )

    by_barrio = (
        ivtm_ref.dropna(subset=["barrio_key"])
        .groupby("barrio_key", as_index=False)["n_turismos_distintivo_0"]
        .sum()
    )
    diag = pd.DataFrame(
        [
            {"metric": "ivtm_reference_year", "value": ivtm_reference_year},
            {"metric": "filas_ivtm_anio_referencia", "value": len(ivtm_ref)},
            {"metric": "barrios_ivtm_totales", "value": by_barrio["barrio_key"].nunique()},
            {
                "metric": "suma_n_turismos_distintivo_0",
                "value": int(by_barrio["n_turismos_distintivo_0"].fillna(0).sum()),
            },
        ]
    )
    return by_barrio, diag


def _build_proxy_base_table(
    *,
    m0_pred: pd.DataFrame,
    capacidad_target: pd.DataFrame,
    residentes_barrio: pd.DataFrame,
    ivtm_cero_barrio: pd.DataFrame,
    target_year: int,
    ivtm_reference_year: int,
) -> pd.DataFrame:
    capacidad_join = capacidad_target.loc[
        :, ["barrio_key", "cod_distrito", "cod_barrio", "barrio", "plazas_barrio_anio"]
    ].copy()

    base = (
        m0_pred.merge(capacidad_join, on="barrio_key", how="left", validate="one_to_one")
        .merge(residentes_barrio, on="barrio_key", how="left", validate="one_to_one")
        .merge(ivtm_cero_barrio, on="barrio_key", how="left", validate="one_to_one")
    )
    base["capacidad_missing_after_join"] = base["plazas_barrio_anio"].isna()
    base["residentes_missing_after_join"] = base["n_aut_residente_activas"].isna()
    base["ivtm_missing_after_join"] = base["n_turismos_distintivo_0"].isna()

    base["n_aut_residente_activas"] = base["n_aut_residente_activas"].fillna(0).astype("int64")
    base["n_turismos_distintivo_0"] = base["n_turismos_distintivo_0"].fillna(0).astype("int64")
    base["ratio_residentes_plaza"] = base["n_aut_residente_activas"] / base["plazas_barrio_anio"]
    base["ratio_cero_plaza"] = base["n_turismos_distintivo_0"] / base["plazas_barrio_anio"]
    base["target_year"] = target_year
    base["ivtm_reference_year"] = ivtm_reference_year
    return base


def _compute_normalizers(
    profiles_df: pd.DataFrame,
    base: pd.DataFrame,
) -> tuple[pd.DataFrame, bool]:
    m0_source = profiles_df.loc[
        profiles_df["profile_level"].eq("barrio_dow_interval"), "profile_prediction"
    ]
    residentes_source = base.loc[
        ~base["residentes_missing_after_join"], "ratio_residentes_plaza"
    ]
    normalizers = pd.DataFrame(
        [
            _normalizer_row(
                "m0_score",
                m0_source,
                "m0_profiles_barrio_dow_interval",
                0.00,
                1.00,
                "historical_minmax",
            ),
            _normalizer_row(
                "residentes_score",
                residentes_source,
                "tabla_base_ratio_residentes_plaza_excluye_missing",
                0.01,
                0.99,
                "valid_p01_p99",
            ),
            _normalizer_row(
                "cero_score",
                base["ratio_cero_plaza"],
                "tabla_base_ratio_cero_plaza",
                0.00,
                1.00,
                "scenario_minmax",
            ),
        ]
    )
    numeric_cols = ["lower", "p50", "upper", "min", "max", "n"]
    valid = bool(
        np.isfinite(normalizers[numeric_cols].to_numpy(dtype="float64")).all()
        and normalizers["upper"].gt(normalizers["lower"]).all()
    )
    return normalizers, valid


def _normalizer_row(
    variable: str,
    values: pd.Series,
    source: str,
    lower_q: float,
    upper_q: float,
    method: str,
) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        raise ValueError(f"No hay valores válidos para normalizar {variable}.")
    lower = clean.quantile(lower_q)
    upper = clean.quantile(upper_q)
    return {
        "variable": variable,
        "method": method,
        "source": source,
        "lower_q": lower_q,
        "upper_q": upper_q,
        "lower": lower,
        "p50": clean.quantile(0.50),
        "upper": upper,
        "min": clean.min(),
        "max": clean.max(),
        "n": len(clean),
        "n_below_lower": int((clean < lower).sum()),
        "n_above_upper": int((clean > upper).sum()),
    }


def _scale_with_normalizer(series: pd.Series, lower: float, upper: float) -> pd.Series:
    if upper <= lower:
        raise ValueError(f"Normalizador inválido: upper={upper} <= lower={lower}.")
    return ((series - lower) / (upper - lower)).clip(0, 1)


def _apply_scores_and_index(base: pd.DataFrame, normalizers: pd.DataFrame) -> pd.DataFrame:
    result = base.copy()
    bounds = normalizers.set_index("variable")[["lower", "upper"]]
    result["m0_score"] = _scale_with_normalizer(
        result["m0_pred"], bounds.loc["m0_score", "lower"], bounds.loc["m0_score", "upper"]
    )
    result["residentes_score"] = _scale_with_normalizer(
        result["ratio_residentes_plaza"],
        bounds.loc["residentes_score", "lower"],
        bounds.loc["residentes_score", "upper"],
    )
    result["cero_score"] = _scale_with_normalizer(
        result["ratio_cero_plaza"],
        bounds.loc["cero_score", "lower"],
        bounds.loc["cero_score", "upper"],
    )
    result["componente_presion_no_observada"] = (
        W_RESIDENTES_IN_ESTRUCTURA * result["residentes_score"]
        + W_CERO_IN_ESTRUCTURA * result["cero_score"]
    )
    result["index_method_id"] = INDEX_METHOD_ID
    result["w_m0"] = W_M0
    result["w_estructura"] = W_ESTRUCTURA
    result["w_residentes_in_estructura"] = W_RESIDENTES_IN_ESTRUCTURA
    result["w_cero_in_estructura"] = W_CERO_IN_ESTRUCTURA
    result["indice_dificultad_ser_ajustado"] = (
        W_M0 * result["m0_score"] + W_ESTRUCTURA * result["componente_presion_no_observada"]
    )
    result["prob_aparcar_proxy"] = 1 - result["indice_dificultad_ser_ajustado"]
    return result


def _build_checks(
    *,
    calendar_checks: list[dict[str, Any]],
    technical: pd.DataFrame,
    capacidad_target: pd.DataFrame,
    ivtm_reference_year: int,
    ivtm_ref_rows: int,
    normalizers: pd.DataFrame,
    normalizers_valid: bool,
    m0_metadata: dict[str, Any],
    scenario_datetime_used: pd.Timestamp,
    expected_n_barrios: int,
    requested_keys: list[str] | None,
    top_n: int | None,
    top_n_clipped: bool,
    residentes_missing_count: int,
    compuestos_activos: int,
) -> pd.DataFrame:
    ratios = technical[["ratio_residentes_plaza", "ratio_cero_plaza"]].to_numpy(dtype="float64")
    score_matrix = technical[["m0_score", "residentes_score", "cero_score"]].to_numpy(dtype="float64")
    component = technical["componente_presion_no_observada"].to_numpy(dtype="float64")
    index_values = technical["indice_dificultad_ser_ajustado"].to_numpy(dtype="float64")
    prob_values = technical["prob_aparcar_proxy"].to_numpy(dtype="float64")
    training_period_end = pd.Timestamp(m0_metadata.get("training_period_end"))
    requested_ok = True
    if requested_keys is not None:
        requested_ok = set(requested_keys) <= set(technical["barrio_key"])

    checks = list(calendar_checks)
    checks.extend(
        [
            _check(
                "scenario_after_training_end",
                scenario_datetime_used > training_period_end,
                f"training_period_end={training_period_end}; scenario_datetime_used={scenario_datetime_used}",
                True,
            ),
            _check(
                "m0_returns_expected_barrios",
                technical["barrio_key"].nunique() == expected_n_barrios and len(technical) == expected_n_barrios,
                f"rows={len(technical)}; barrios={technical['barrio_key'].nunique()}; expected={expected_n_barrios}",
                True,
            ),
            _check("no_m0_nulls", technical["m0_pred"].notna().all(), f"nulls={int(technical['m0_pred'].isna().sum())}", True),
            _check(
                "no_fallback_level_nulls",
                technical["fallback_level"].notna().all(),
                f"nulls={int(technical['fallback_level'].isna().sum())}",
                True,
            ),
            _check(
                "capacidad_expected_barrios_target_year",
                capacidad_target["barrio_key"].nunique() == expected_n_barrios,
                f"barrios={capacidad_target['barrio_key'].nunique()}; expected={expected_n_barrios}",
                True,
            ),
            _check(
                "plazas_positive",
                technical["plazas_barrio_anio"].gt(0).all(),
                f"non_positive={int(technical['plazas_barrio_anio'].le(0).sum())}",
                True,
            ),
            _check(
                "ivtm_reference_year_available",
                ivtm_ref_rows > 0,
                f"ivtm_reference_year={ivtm_reference_year}; filas={ivtm_ref_rows}",
                True,
            ),
            _check(
                "ratios_structurales_finite",
                np.isfinite(ratios).all(),
                f"non_finite={int((~np.isfinite(ratios)).sum())}",
                True,
            ),
            _check("normalizers_valid", normalizers_valid, f"n_normalizers={len(normalizers)}", True),
            _check(
                "scores_in_0_1",
                np.isfinite(score_matrix).all() and ((score_matrix >= 0) & (score_matrix <= 1)).all(),
                f"score_columns=3; rows={len(technical)}",
                True,
            ),
            _check(
                "componente_estructural_in_0_1",
                np.isfinite(component).all() and ((component >= 0) & (component <= 1)).all(),
                "componente_presion_no_observada",
                True,
            ),
            _check(
                "indice_ajustado_in_0_1",
                np.isfinite(index_values).all() and ((index_values >= 0) & (index_values <= 1)).all(),
                f"min={technical['indice_dificultad_ser_ajustado'].min()}; max={technical['indice_dificultad_ser_ajustado'].max()}",
                True,
            ),
            _check(
                "prob_aparcar_proxy_in_0_1",
                np.isfinite(prob_values).all() and ((prob_values >= 0) & (prob_values <= 1)).all(),
                f"min={technical['prob_aparcar_proxy'].min()}; max={technical['prob_aparcar_proxy'].max()}",
                True,
            ),
            _check(
                "prob_aparcar_proxy_inverse_ok",
                np.allclose(
                    technical["prob_aparcar_proxy"],
                    1 - technical["indice_dificultad_ser_ajustado"],
                ),
                "prob_aparcar_proxy = 1 - indice_dificultad_ser_ajustado",
                True,
            ),
            _check(
                "requested_barrio_keys_exist",
                requested_ok,
                f"requested={requested_keys}",
                True,
            ),
            _check(
                "top_n_valid",
                top_n is None or (top_n > 0),
                f"top_n={top_n}",
                True,
            ),
            _check(
                "top_n_not_exceeding_or_clipped",
                top_n is None or not top_n_clipped,
                f"top_n={top_n}; n_barrios={len(technical)}; clipped={top_n_clipped}",
                False,
                warning_when_false=True,
            ),
            _check(
                "residentes_compuestos_no_imputados",
                compuestos_activos == 0,
                f"filas_residentes_activas_no_barrio={compuestos_activos}",
                False,
                warning_when_false=True,
            ),
            _check(
                "residentes_join_direct_missing",
                residentes_missing_count == 0,
                f"barrios_sin_residentes_join_directo={residentes_missing_count}",
                False,
                warning_when_false=True,
            ),
        ]
    )
    return pd.DataFrame(checks).loc[:, ["check_id", "status", "detail", "critical"]]


def _check(
    check_id: str,
    ok: bool,
    detail: str,
    critical: bool,
    warning_when_false: bool = False,
) -> dict[str, Any]:
    if ok:
        status = "OK"
    elif warning_when_false:
        status = "WARNING"
    else:
        status = "FAIL"
    return {
        "check_id": check_id,
        "status": status,
        "detail": detail,
        "critical": bool(critical),
    }


def _select_operational_output(
    technical: pd.DataFrame,
    requested_keys: list[str] | None,
) -> pd.DataFrame:
    output = technical.loc[:, OPERATIONAL_COLUMNS].copy()
    if requested_keys is not None:
        output = output.loc[output["barrio_key"].isin(requested_keys)].copy()
    return output.reset_index(drop=True)


def _build_top_ranking(
    technical: pd.DataFrame,
    top_n: int | None,
    ranking_by: Literal["facilidad", "dificultad"],
) -> tuple[pd.DataFrame | None, bool]:
    if top_n is None:
        return None, False
    if top_n <= 0:
        return pd.DataFrame(), False

    sort_col = "prob_aparcar_proxy" if ranking_by == "facilidad" else "indice_dificultad_ser_ajustado"
    ascending = False
    n = min(top_n, len(technical))
    ranking = (
        technical.sort_values(sort_col, ascending=ascending, kind="mergesort")
        .head(n)
        .reset_index(drop=True)
        .assign(rank=lambda df: np.arange(1, len(df) + 1), ranking_by=ranking_by)
    )
    cols = [
        "rank",
        "ranking_by",
        "barrio_key",
        "barrio_nombre",
        "intervalo_inicio",
        "prob_aparcar_proxy",
        "indice_dificultad_ser_ajustado",
        "m0_score",
        "residentes_score",
        "cero_score",
        "componente_presion_no_observada",
        "residentes_missing_after_join",
        "fallback_level",
    ]
    return ranking.loc[:, cols].copy(), top_n > len(technical)


def _normalize_requested_barrio_keys(
    barrio_key: str | Sequence[str] | None,
) -> list[str] | None:
    if barrio_key is None:
        return None
    if isinstance(barrio_key, str):
        return [barrio_key]
    return list(barrio_key)


def _load_barrio_lookup_from_df(capacidad_ser: pd.DataFrame, target_year: int) -> pd.DataFrame:
    required = {"barrio_key", "barrio", "anio"}
    missing = required - set(capacidad_ser.columns)
    if missing:
        raise ValueError(f"Faltan columnas en capacidad SER: {', '.join(sorted(missing))}.")
    lookup = capacidad_ser.loc[:, ["barrio_key", "barrio", "anio"]].copy()
    lookup["anio"] = pd.to_numeric(lookup["anio"], errors="coerce")
    lookup = lookup.loc[lookup["anio"].eq(target_year)]
    lookup = lookup.sort_values(
        ["barrio_key", "anio", "barrio"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    lookup = lookup.drop_duplicates(subset=["barrio_key"], keep="first")
    lookup = lookup.rename(columns={"barrio": "barrio_nombre"})
    return lookup.loc[:, ["barrio_key", "barrio_nombre"]].reset_index(drop=True)


def _build_metadata(
    *,
    m0_metadata: dict[str, Any],
    scenario_meta: dict[str, Any],
    ivtm_reference_year: int,
    expected_n_barrios: int,
    interval_alignment: str,
) -> dict[str, Any]:
    return {
        "model_id": m0_metadata.get("model_id", "M0_historical_profile"),
        "target_column": m0_metadata.get("target_column"),
        "training_period_start": m0_metadata.get("training_period_start"),
        "training_period_end": m0_metadata.get("training_period_end"),
        "scenario_datetime_requested": _iso(scenario_meta["scenario_datetime_requested"]),
        "scenario_datetime_used": _iso(scenario_meta["scenario_datetime_used"]),
        "interval_alignment": interval_alignment,
        "interval_assignment": "contained_in_30min_interval",
        "intervalo_inicio": _iso(scenario_meta["intervalo_inicio"]),
        "intervalo_fin": _iso(scenario_meta["intervalo_fin"]),
        "intervalo_ajustado_30min": bool(scenario_meta["intervalo_ajustado_30min"]),
        "target_year": int(scenario_meta["target_year"]),
        "ivtm_reference_year": int(ivtm_reference_year),
        "expected_n_barrios": int(expected_n_barrios),
        "dia_semana_num_m0": int(scenario_meta["dia_semana_num_m0"]),
        "dia_semana_num_calendario": int(scenario_meta["dia_semana_num_calendario"]),
        "tipo_dia_calendario": scenario_meta["tipo_dia_calendario"],
        "ventana_ser_validada": scenario_meta["ventana_ser_validada"],
        "weights": {
            "w_m0": W_M0,
            "w_estructura": W_ESTRUCTURA,
            "w_residentes_in_estructura": W_RESIDENTES_IN_ESTRUCTURA,
            "w_cero_in_estructura": W_CERO_IN_ESTRUCTURA,
        },
        "interpretation": INTERPRETATION,
    }


def _base_join_diagnostics(base: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric": "n_filas_tabla_base", "value": len(base)},
            {"metric": "n_barrios_tabla_base", "value": base["barrio_key"].nunique()},
            {
                "metric": "capacidad_missing_after_join",
                "value": int(base["capacidad_missing_after_join"].sum()),
            },
            {
                "metric": "residentes_missing_after_join",
                "value": int(base["residentes_missing_after_join"].sum()),
            },
            {
                "metric": "ivtm_missing_after_join",
                "value": int(base["ivtm_missing_after_join"].sum()),
            },
            {
                "metric": "ratio_residentes_non_finite",
                "value": int((~np.isfinite(base["ratio_residentes_plaza"])).sum()),
            },
            {
                "metric": "ratio_cero_non_finite",
                "value": int((~np.isfinite(base["ratio_cero_plaza"])).sum()),
            },
        ]
    )


def _iso(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    return str(value)
