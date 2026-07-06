from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


EXPECTED_PROFILE_LEVELS = {
    "barrio_dow_interval",
    "barrio_interval",
    "barrio",
    "dow_interval",
    "global_mean",
}

PROFILE_KEY_COLUMNS = {
    "barrio_dow_interval": ["barrio_key", "dia_semana_num", "intervalo_30min_id"],
    "barrio_interval": ["barrio_key", "intervalo_30min_id"],
    "barrio": ["barrio_key"],
    "dow_interval": ["dia_semana_num", "intervalo_30min_id"],
    "global_mean": [],
}

FALLBACK_LEVELS = [
    "barrio_dow_interval",
    "barrio_interval",
    "barrio",
    "dow_interval",
    "global_mean",
]

CRITICAL_PROFILE_COLUMNS = {
    "profile_level",
    "barrio_key",
    "dia_semana_num",
    "intervalo_30min_id",
    "profile_prediction",
    "n_train_obs",
    "fallback_order",
}


def _make_key_index(df: pd.DataFrame, key_columns: list[str]) -> pd.Index | pd.MultiIndex:
    """Construye un índice de claves compatible con perfiles de una o varias columnas."""
    if len(key_columns) == 1:
        return pd.Index(df[key_columns[0]], name=key_columns[0])
    return pd.MultiIndex.from_frame(df[key_columns])


def load_m0_profiles(
    profiles_path: str | Path,
    metadata_path: str | Path | None = None,
    validate: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Carga los perfiles históricos M0 y, si existe, su metadata."""
    profiles_df = pd.read_parquet(Path(profiles_path))

    metadata: dict[str, Any] = {}
    if metadata_path is not None:
        metadata_file = Path(metadata_path)
        if metadata_file.exists():
            with metadata_file.open("r", encoding="utf-8") as file:
                loaded_metadata = json.load(file)
            if not isinstance(loaded_metadata, dict):
                raise ValueError("La metadata M0 debe ser un objeto JSON.")
            metadata = loaded_metadata

    if validate:
        validate_m0_profiles(profiles_df, metadata)

    return profiles_df, metadata


def validate_m0_profiles(
    profiles_df: pd.DataFrame,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Valida la estructura mínima de los perfiles históricos M0."""
    _ = metadata
    missing_columns = CRITICAL_PROFILE_COLUMNS - set(profiles_df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Faltan columnas críticas en perfiles M0: {missing}.")

    levels = set(profiles_df["profile_level"].dropna().unique())
    missing_levels = EXPECTED_PROFILE_LEVELS - levels
    if missing_levels:
        missing = ", ".join(sorted(missing_levels))
        raise ValueError(f"Faltan niveles de perfil M0 esperados: {missing}.")

    global_rows = profiles_df[profiles_df["profile_level"] == "global_mean"]
    if len(global_rows) != 1:
        raise ValueError(
            f"El nivel global_mean debe tener exactamente una fila; tiene {len(global_rows)}."
        )

    predictions = pd.to_numeric(profiles_df["profile_prediction"], errors="coerce")
    if predictions.isna().any() or not np.isfinite(predictions.to_numpy()).all():
        raise ValueError("profile_prediction contiene valores nulos o no finitos.")

    train_obs = pd.to_numeric(profiles_df["n_train_obs"], errors="coerce")
    if train_obs.isna().any() or (train_obs <= 0).any():
        raise ValueError("n_train_obs debe ser positivo en todos los perfiles M0.")

    for level, key_columns in PROFILE_KEY_COLUMNS.items():
        level_df = profiles_df[profiles_df["profile_level"] == level]
        if level == "global_mean":
            continue
        duplicated = level_df.duplicated(subset=key_columns, keep=False)
        if duplicated.any():
            keys = ", ".join(key_columns)
            raise ValueError(
                f"Hay perfiles M0 duplicados en {level} para las claves: {keys}."
            )

    return True


def load_barrio_lookup(
    capacity_path: str | Path,
    year: int | None = None,
) -> pd.DataFrame:
    """Carga nombres de barrio por barrio_key desde la capacidad anual SER."""
    capacity_df = pd.read_parquet(Path(capacity_path))
    required_columns = {"barrio_key", "barrio", "anio"}
    missing_columns = required_columns - set(capacity_df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Faltan columnas en capacidad SER: {missing}.")

    lookup_df = capacity_df.loc[:, ["barrio_key", "barrio", "anio"]].copy()
    lookup_df["anio"] = pd.to_numeric(lookup_df["anio"], errors="coerce")

    if year is not None:
        lookup_df = lookup_df[lookup_df["anio"] == year]

    lookup_df = lookup_df.sort_values(
        ["barrio_key", "anio", "barrio"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    lookup_df = lookup_df.drop_duplicates(subset=["barrio_key"], keep="first")
    lookup_df = lookup_df.rename(columns={"barrio": "barrio_nombre"})

    return lookup_df.loc[:, ["barrio_key", "barrio_nombre"]].reset_index(drop=True)


def add_m0_time_features(
    scenarios_df: pd.DataFrame,
    intervalo_col: str = "intervalo_inicio",
    interval_alignment: str = "floor",
) -> pd.DataFrame:
    """Añade variables temporales M0 y normaliza a intervalos de 30 minutos."""
    if intervalo_col not in scenarios_df.columns:
        raise ValueError(f"Falta la columna temporal '{intervalo_col}'.")

    result_df = scenarios_df.copy()
    consulta = pd.to_datetime(result_df[intervalo_col], errors="coerce")
    if consulta.isna().any():
        raise ValueError(f"La columna '{intervalo_col}' contiene fechas nulas o inválidas.")

    result_df["intervalo_consulta"] = consulta

    if interval_alignment == "floor":
        intervalo_ajustado = consulta.dt.floor("30min")
        result_df[intervalo_col] = intervalo_ajustado
        result_df["intervalo_ajustado_30min"] = consulta.ne(intervalo_ajustado)
    elif interval_alignment == "error":
        is_boundary = (
            consulta.dt.minute.isin([0, 30])
            & consulta.dt.second.eq(0)
            & consulta.dt.microsecond.eq(0)
            & consulta.dt.nanosecond.eq(0)
        )
        if not is_boundary.all():
            raise ValueError(
                "Todas las horas consultadas deben caer exactamente en frontera "
                "de 30 minutos cuando interval_alignment='error'."
            )
        result_df[intervalo_col] = consulta
    else:
        raise ValueError("interval_alignment debe ser 'floor' o 'error'.")

    result_df["dia_semana_num"] = result_df[intervalo_col].dt.dayofweek
    result_df["intervalo_30min_id"] = (
        result_df[intervalo_col].dt.hour * 2
        + result_df[intervalo_col].dt.minute // 30
    )

    return result_df


def make_single_scenario(
    barrio_key: str,
    intervalo_inicio: str | pd.Timestamp,
    barrio_nombre: str | None = None,
) -> pd.DataFrame:
    """Construye un escenario M0 de una fila."""
    return pd.DataFrame(
        {
            "barrio_key": [barrio_key],
            "barrio_nombre": [barrio_nombre],
            "intervalo_inicio": [intervalo_inicio],
        }
    )


def make_map_scenarios(
    intervalo_inicio: str | pd.Timestamp,
    barrio_lookup: pd.DataFrame,
) -> pd.DataFrame:
    """Construye un escenario por barrio para uso posterior en mapas."""
    if "barrio_key" not in barrio_lookup.columns:
        raise ValueError("barrio_lookup debe contener la columna barrio_key.")

    columns = ["barrio_key"]
    if "barrio_nombre" in barrio_lookup.columns:
        columns.append("barrio_nombre")

    scenarios_df = barrio_lookup.loc[:, columns].copy()
    scenarios_df = scenarios_df[scenarios_df["barrio_key"].notna()]
    scenarios_df = scenarios_df.drop_duplicates(subset=["barrio_key"], keep="first")
    if "barrio_nombre" not in scenarios_df.columns:
        scenarios_df["barrio_nombre"] = None

    scenarios_df["intervalo_inicio"] = intervalo_inicio
    return scenarios_df.loc[:, ["barrio_key", "barrio_nombre", "intervalo_inicio"]]


def predict_m0_from_profiles(
    scenarios_df: pd.DataFrame,
    profiles_df: pd.DataFrame,
    barrio_lookup: pd.DataFrame | None = None,
    intervalo_col: str = "intervalo_inicio",
    pred_col: str = "m0_pred",
    interval_alignment: str = "floor",
) -> pd.DataFrame:
    """Aplica perfiles históricos M0 a escenarios usando fallback jerárquico."""
    required_scenario_columns = {"barrio_key", intervalo_col}
    missing_scenario_columns = required_scenario_columns - set(scenarios_df.columns)
    if missing_scenario_columns:
        missing = ", ".join(sorted(missing_scenario_columns))
        raise ValueError(f"Faltan columnas en escenarios M0: {missing}.")

    result_df = scenarios_df.copy()
    result_df["_m0_original_order"] = np.arange(len(result_df))

    if {"dia_semana_num", "intervalo_30min_id"} - set(result_df.columns):
        result_df = add_m0_time_features(
            result_df,
            intervalo_col=intervalo_col,
            interval_alignment=interval_alignment,
        )

    if barrio_lookup is not None:
        if "barrio_key" not in barrio_lookup.columns:
            raise ValueError("barrio_lookup debe contener la columna barrio_key.")
        if "barrio_nombre" in barrio_lookup.columns:
            lookup_columns = ["barrio_key", "barrio_nombre"]
            lookup_df = barrio_lookup.loc[:, lookup_columns].drop_duplicates(
                subset=["barrio_key"],
                keep="first",
            )
            result_df = result_df.merge(
                lookup_df.rename(columns={"barrio_nombre": "_m0_barrio_nombre_lookup"}),
                on="barrio_key",
                how="left",
                sort=False,
            )
            if "barrio_nombre" in result_df.columns:
                result_df["barrio_nombre"] = result_df["barrio_nombre"].fillna(
                    result_df["_m0_barrio_nombre_lookup"]
                )
            else:
                result_df["barrio_nombre"] = result_df["_m0_barrio_nombre_lookup"]
            result_df = result_df.drop(columns=["_m0_barrio_nombre_lookup"])

    result_df[pred_col] = np.nan
    result_df["fallback_level"] = pd.NA
    result_df["fallback_order"] = pd.NA

    for level in FALLBACK_LEVELS:
        missing_prediction = result_df[pred_col].isna()
        if not missing_prediction.any():
            break

        level_profiles = profiles_df[profiles_df["profile_level"] == level].copy()
        if level_profiles.empty:
            continue

        if level == "global_mean":
            if len(level_profiles) != 1:
                raise ValueError(
                    f"El nivel global_mean debe tener exactamente una fila; tiene {len(level_profiles)}."
                )
            row = level_profiles.iloc[0]
            result_df.loc[missing_prediction, pred_col] = row["profile_prediction"]
            result_df.loc[missing_prediction, "fallback_level"] = level
            result_df.loc[missing_prediction, "fallback_order"] = row["fallback_order"]
            continue

        key_columns = PROFILE_KEY_COLUMNS[level]
        missing_profile_columns = set(key_columns + ["profile_prediction", "fallback_order"]) - set(
            level_profiles.columns
        )
        if missing_profile_columns:
            missing = ", ".join(sorted(missing_profile_columns))
            raise ValueError(f"Faltan columnas en perfiles M0 para {level}: {missing}.")

        duplicated = level_profiles.duplicated(subset=key_columns, keep=False)
        if duplicated.any():
            keys = ", ".join(key_columns)
            raise ValueError(
                f"Hay perfiles M0 duplicados en {level} para las claves: {keys}."
            )

        profile_index = level_profiles.set_index(key_columns)
        scenario_index = _make_key_index(result_df.loc[missing_prediction, key_columns], key_columns)
        predictions = profile_index["profile_prediction"].reindex(scenario_index)
        fallback_orders = profile_index["fallback_order"].reindex(scenario_index)
        matched_index = result_df.index[missing_prediction][predictions.notna().to_numpy()]

        if len(matched_index) == 0:
            continue

        matched_positions = predictions.notna().to_numpy()
        result_df.loc[matched_index, pred_col] = predictions.to_numpy()[matched_positions]
        result_df.loc[matched_index, "fallback_level"] = level
        result_df.loc[matched_index, "fallback_order"] = fallback_orders.to_numpy()[matched_positions]

    if result_df[pred_col].isna().any():
        missing_count = int(result_df[pred_col].isna().sum())
        raise ValueError(
            f"{missing_count} filas no consiguieron predicción M0 tras aplicar global_mean."
        )

    result_df = result_df.sort_values("_m0_original_order", kind="mergesort")
    result_df = result_df.drop(columns=["_m0_original_order"])
    result_df = result_df.reset_index(drop=True)

    return result_df
