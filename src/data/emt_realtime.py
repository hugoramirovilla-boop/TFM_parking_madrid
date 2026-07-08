from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pandas as pd
import requests


DEFAULT_ENDPOINT_URL = "https://servayto.madrid.es/MTPAR_WSINFO/InfoParking"
SOAP_ACTION = "http://tempuri.org/iInfoParking/GetListParking"
SOURCE_ID = "emt_realtime_api"


@dataclass
class EMTRealtimeResult:
    realtime: pd.DataFrame
    joined: pd.DataFrame
    checks: pd.DataFrame
    diagnostics: dict[str, pd.DataFrame]
    metadata: dict[str, Any]
    outputs: pd.DataFrame


def build_get_list_parking_soap_body(language: str = "ES") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:tem="http://tempuri.org/">
   <soapenv:Header/>
   <soapenv:Body>
      <tem:GetListParking>
         <tem:language>{language}</tem:language>
      </tem:GetListParking>
   </soapenv:Body>
</soapenv:Envelope>"""


def fetch_emt_realtime_xml(
    *,
    endpoint_url: str = DEFAULT_ENDPOINT_URL,
    language: str = "ES",
    timeout: int = 30,
) -> tuple[bytes, dict[str, Any]]:
    query_timestamp = pd.Timestamp.now(tz="UTC")
    body = build_get_list_parking_soap_body(language)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": SOAP_ACTION,
    }
    try:
        response = requests.post(
            endpoint_url,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Error consultando la API EMT realtime: {exc}") from exc

    metadata = {
        "endpoint_url": endpoint_url,
        "language": language,
        "query_timestamp_utc": query_timestamp.isoformat(),
        "http_status": response.status_code,
        "response_bytes": len(response.content or b""),
        "soap_action": SOAP_ACTION,
    }
    if response.status_code != 200:
        detail = response.text[:500] if response.text else ""
        raise RuntimeError(
            f"API EMT realtime devolvió HTTP {response.status_code}. {detail}"
        )
    if not response.content:
        raise RuntimeError("API EMT realtime devolvió una respuesta vacía.")
    return response.content, metadata


def parse_emt_realtime_xml(xml_content: bytes) -> pd.DataFrame:
    root = _parse_xml(xml_content)
    parking_nodes = _find_descendants(root, "lstParking")
    if not parking_nodes:
        nested_root = _parse_nested_xml_payload(root)
        if nested_root is not None:
            parking_nodes = _find_descendants(nested_root, "lstParking")
    if not parking_nodes:
        parking_nodes = _find_descendants(root, "parking")

    records = [_parse_parking_node(node) for node in parking_nodes]
    return pd.DataFrame(records)


def normalize_emt_realtime_parkings(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id_emt",
                "name",
                "nickName",
                "address",
                "areaCode",
                "state",
                "town",
                "latitude",
                "longitude",
                "occ_code",
                "occ_name",
                "free_raw",
                "free_valid",
                "has_free_raw",
                "has_live_free",
                "moment",
                "renewalIndex",
                "query_timestamp",
                "source",
            ]
        )

    _ensure_columns(
        df,
        [
            "id",
            "name",
            "nickName",
            "address",
            "areaCode",
            "state",
            "town",
            "latitude",
            "longitude",
            "occ_code",
            "occ_name",
            "free",
            "moment",
            "renewalIndex",
        ],
    )
    out = df[
        [
            "id",
            "name",
            "nickName",
            "address",
            "areaCode",
            "state",
            "town",
            "latitude",
            "longitude",
            "occ_code",
            "occ_name",
            "free",
            "moment",
            "renewalIndex",
        ]
    ].rename(columns={"id": "id_emt", "free": "free_raw"})
    out["id_emt"] = pd.to_numeric(out["id_emt"], errors="coerce").astype("Int64")
    out["latitude"] = pd.to_numeric(out["latitude"], errors="coerce")
    out["longitude"] = pd.to_numeric(out["longitude"], errors="coerce")
    out["free_raw"] = pd.to_numeric(out["free_raw"], errors="coerce")
    out["free_valid"] = out["free_raw"].where(out["free_raw"].ge(0), pd.NA)
    out["has_free_raw"] = out["free_raw"].notna()
    out["has_live_free"] = out["free_valid"].notna()
    out["moment"] = pd.to_datetime(out["moment"], errors="coerce")
    out["renewalIndex"] = pd.to_numeric(out["renewalIndex"], errors="coerce")
    out["query_timestamp"] = pd.Timestamp.now(tz="UTC")
    out["source"] = SOURCE_ID
    return out


def load_emt_inventory(inventory_path: Path) -> pd.DataFrame:
    if not inventory_path.exists():
        raise FileNotFoundError(f"No existe el inventario EMT: {inventory_path}")
    inventory = pd.read_parquet(inventory_path)
    if "id_emt" not in inventory.columns:
        if "id_emt_referencia" not in inventory.columns:
            raise ValueError(
                "El inventario EMT no contiene id_emt ni id_emt_referencia."
            )
        inventory = inventory.rename(columns={"id_emt_referencia": "id_emt"})
    inventory["id_emt"] = pd.to_numeric(inventory["id_emt"], errors="coerce").astype(
        "Int64"
    )
    return inventory


def join_emt_realtime_to_inventory(
    realtime_df: pd.DataFrame,
    inventory_df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    realtime = realtime_df.copy()
    inventory = inventory_df.copy()
    realtime["id_emt"] = pd.to_numeric(realtime["id_emt"], errors="coerce").astype(
        "Int64"
    )
    inventory["id_emt"] = pd.to_numeric(inventory["id_emt"], errors="coerce").astype(
        "Int64"
    )

    realtime_join_cols = [
        "id_emt",
        "free_raw",
        "free_valid",
        "has_free_raw",
        "has_live_free",
        "moment",
        "query_timestamp",
        "source",
        "name",
        "nickName",
        "address",
        "latitude",
        "longitude",
        "occ_code",
        "occ_name",
        "renewalIndex",
    ]
    realtime_join_cols = [c for c in realtime_join_cols if c in realtime.columns]
    realtime_join = realtime[realtime_join_cols].rename(
        columns={
            "name": "realtime_name",
            "nickName": "realtime_nickName",
            "address": "realtime_address",
            "latitude": "realtime_latitude",
            "longitude": "realtime_longitude",
        }
    )
    joined = inventory.merge(
        realtime_join,
        on="id_emt",
        how="left",
        indicator=True,
        validate="m:1",
    )
    joined["has_realtime_response"] = joined["_merge"].eq("both")
    joined["has_live_free"] = joined["has_live_free"].fillna(False).astype(bool)
    joined["has_free_raw"] = joined["has_free_raw"].fillna(False).astype(bool)
    joined["coverage_status"] = "sin_id_emt"
    joined.loc[
        joined["id_emt"].notna() & ~joined["has_realtime_response"],
        "coverage_status",
    ] = "inventario_sin_realtime"
    joined.loc[
        joined["has_realtime_response"] & ~joined["has_live_free"],
        "coverage_status",
    ] = "realtime_sin_ocupacion_viva"
    joined.loc[joined["has_live_free"], "coverage_status"] = "ocupacion_viva"
    joined = joined.drop(columns=["_merge"])

    matched_ids = set(inventory["id_emt"].dropna().astype(int))
    realtime_ids = set(realtime["id_emt"].dropna().astype(int))
    realtime_only_ids = sorted(realtime_ids - matched_ids)
    inventory_only_ids = sorted(matched_ids - realtime_ids)

    realtime_only = realtime.loc[
        realtime["id_emt"].astype("Int64").isin(realtime_only_ids)
    ].copy()
    realtime_only_live = realtime_only.loc[realtime_only["free_valid"].notna()].copy()
    inventory_only = inventory.loc[
        inventory["id_emt"].astype("Int64").isin(inventory_only_ids)
    ].copy()
    matched_live = joined.loc[joined["has_live_free"]].copy()
    negative_free = realtime.loc[realtime["free_raw"].lt(0).fillna(False)].copy()

    diagnostics = {
        "realtime_only": realtime_only,
        "realtime_only_live": realtime_only_live,
        "inventory_only": inventory_only,
        "matched_live": matched_live,
        "negative_free": negative_free,
    }
    return joined, diagnostics


def build_emt_realtime_from_api(
    *,
    inventory_path: Path,
    raw_xml_output_path: Path | None = None,
    interim_output_path: Path | None = None,
    joined_output_path: Path | None = None,
    endpoint_url: str = DEFAULT_ENDPOINT_URL,
    language: str = "ES",
    timeout: int = 30,
) -> EMTRealtimeResult:
    check_rows: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    xml_content, metadata = fetch_emt_realtime_xml(
        endpoint_url=endpoint_url,
        language=language,
        timeout=timeout,
    )
    _add_check(check_rows, "api_http_200", "OK", "HTTP 200", True)
    _add_check(
        check_rows,
        "api_response_not_empty",
        "OK",
        f"{len(xml_content)} bytes",
        True,
    )
    raw_df = parse_emt_realtime_xml(xml_content)
    _add_check(check_rows, "api_xml_parseable", "OK", "XML parseable", True)

    realtime = normalize_emt_realtime_parkings(raw_df)
    realtime["query_timestamp"] = pd.Timestamp(metadata["query_timestamp_utc"])
    _validate_realtime(realtime, check_rows)

    inventory_exists = inventory_path.exists()
    _add_check(
        check_rows,
        "inventory_file_exists",
        "OK" if inventory_exists else "FAIL",
        str(inventory_path),
        True,
    )
    inventory = load_emt_inventory(inventory_path)
    _validate_inventory(inventory, check_rows)

    joined, join_diagnostics = join_emt_realtime_to_inventory(realtime, inventory)
    diagnostics = _build_diagnostics(realtime, inventory, joined, join_diagnostics)
    _validate_join(diagnostics["coverage_summary"], check_rows)

    if raw_xml_output_path is not None:
        _write_bytes(raw_xml_output_path, xml_content)
        _add_output(outputs, "raw_xml", raw_xml_output_path)
        _add_check(check_rows, "raw_xml_written", "OK", str(raw_xml_output_path), False)
    if interim_output_path is not None:
        _write_parquet(interim_output_path, realtime)
        _add_output(outputs, "interim_realtime", interim_output_path)
        _add_check(check_rows, "interim_written", "OK", str(interim_output_path), False)
    if joined_output_path is not None:
        _write_parquet(joined_output_path, joined)
        _add_output(outputs, "joined_inventory", joined_output_path)
        _add_check(check_rows, "joined_written", "OK", str(joined_output_path), False)

    checks = pd.DataFrame(check_rows, columns=["check_id", "status", "detail", "critical"])
    failures = checks.loc[checks["status"].eq("FAIL") & checks["critical"]]
    if not failures.empty:
        raise ValueError(
            "Fallos críticos en EMT realtime: "
            + "; ".join(failures["check_id"].astype(str).tolist())
        )

    metadata.update(
        {
            "n_realtime_rows": int(len(realtime)),
            "n_joined_rows": int(len(joined)),
            "source": SOURCE_ID,
        }
    )
    outputs_df = pd.DataFrame(outputs, columns=["output", "path", "exists", "size_mb"])
    return EMTRealtimeResult(
        realtime=realtime,
        joined=joined,
        checks=checks,
        diagnostics=diagnostics,
        metadata=metadata,
        outputs=outputs_df,
    )


def _parse_xml(xml_content: bytes | str) -> ET.Element:
    try:
        return ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f"XML EMT realtime no parseable: {exc}") from exc


def _parse_nested_xml_payload(root: ET.Element) -> ET.Element | None:
    for node in root.iter():
        text = (node.text or "").strip()
        if "<" not in text or "lstParking" not in text:
            continue
        try:
            return ET.fromstring(text.encode("utf-8"))
        except ET.ParseError:
            continue
    return None


def _localname(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _find_descendants(root: ET.Element, local_name: str) -> list[ET.Element]:
    target = local_name.lower()
    return [node for node in root.iter() if _localname(node.tag).lower() == target]


def _child_text(node: ET.Element, local_name: str) -> str | None:
    target = local_name.lower()
    for child in list(node):
        if _localname(child.tag).lower() == target:
            text = (child.text or "").strip()
            return text if text else None
    return None


def _parse_parking_node(node: ET.Element) -> dict[str, Any]:
    record = {
        field: _child_text(node, field)
        for field in [
            "id",
            "name",
            "nickName",
            "address",
            "areaCode",
            "state",
            "town",
            "latitude",
            "longitude",
        ]
    }
    occupations = [
        child
        for child in node.iter()
        if _localname(child.tag).lower() == "occupation" and child is not node
    ]
    occupation = _select_occupation(occupations)
    if occupation is not None:
        record.update(
            {
                "occ_code": _child_text(occupation, "code"),
                "occ_name": _child_text(occupation, "name"),
                "free": _child_text(occupation, "free"),
                "moment": _child_text(occupation, "moment"),
                "renewalIndex": _child_text(occupation, "renewalIndex"),
            }
        )
    return record


def _select_occupation(occupations: list[ET.Element]) -> ET.Element | None:
    if not occupations:
        return None
    for occupation in occupations:
        name = (_child_text(occupation, "name") or "").strip().lower()
        if name == "total":
            return occupation
    return occupations[0]


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA


def _add_check(
    rows: list[dict[str, Any]],
    check_id: str,
    status: str,
    detail: str,
    critical: bool,
) -> None:
    rows.append(
        {
            "check_id": check_id,
            "status": status,
            "detail": detail,
            "critical": bool(critical),
        }
    )


def _validate_realtime(realtime: pd.DataFrame, check_rows: list[dict[str, Any]]) -> None:
    _add_check(
        check_rows,
        "realtime_not_empty",
        "OK" if not realtime.empty else "FAIL",
        f"{len(realtime)} filas",
        True,
    )
    id_not_null = realtime["id_emt"].notna().all() if "id_emt" in realtime else False
    _add_check(
        check_rows,
        "realtime_id_not_null",
        "OK" if id_not_null else "FAIL",
        f"nulos={int(realtime['id_emt'].isna().sum()) if 'id_emt' in realtime else 'sin_columna'}",
        True,
    )
    id_unique = realtime["id_emt"].dropna().is_unique if "id_emt" in realtime else False
    _add_check(
        check_rows,
        "realtime_id_unique",
        "OK" if id_unique else "FAIL",
        f"ids_unicos={int(realtime['id_emt'].nunique(dropna=True)) if 'id_emt' in realtime else 0}",
        True,
    )
    coords_parseable = (
        realtime["latitude"].notna().any() and realtime["longitude"].notna().any()
        if {"latitude", "longitude"}.issubset(realtime.columns)
        else False
    )
    _add_check(
        check_rows,
        "realtime_coordinates_parseable",
        "OK" if coords_parseable else "WARNING",
        (
            f"lat_no_nulas={int(realtime['latitude'].notna().sum())}, "
            f"lon_no_nulas={int(realtime['longitude'].notna().sum())}"
        ),
        False,
    )
    free_some = realtime["free_raw"].notna().any() if "free_raw" in realtime else False
    _add_check(
        check_rows,
        "realtime_free_raw_present_some",
        "OK" if free_some else "WARNING",
        f"free_raw_no_nulo={int(realtime['free_raw'].notna().sum()) if 'free_raw' in realtime else 0}",
        False,
    )
    negative_count = (
        int(realtime["free_raw"].lt(0).fillna(False).sum())
        if "free_raw" in realtime
        else 0
    )
    _add_check(
        check_rows,
        "realtime_free_negative_count",
        "WARNING" if negative_count else "OK",
        f"free_raw_negativo={negative_count}",
        False,
    )


def _validate_inventory(inventory: pd.DataFrame, check_rows: list[dict[str, Any]]) -> None:
    _add_check(
        check_rows,
        "inventory_not_empty",
        "OK" if not inventory.empty else "FAIL",
        f"{len(inventory)} filas",
        True,
    )
    has_id = "id_emt" in inventory.columns
    _add_check(
        check_rows,
        "inventory_id_emt_present",
        "OK" if has_id else "FAIL",
        "id_emt normalizado disponible" if has_id else "sin id_emt",
        True,
    )
    coverage = float(inventory["id_emt"].notna().mean()) if has_id and len(inventory) else 0
    _add_check(
        check_rows,
        "inventory_id_emt_coverage",
        "OK" if coverage > 0 else "FAIL",
        f"{coverage:.1%} con id_emt",
        True,
    )


def _validate_join(
    coverage_summary: pd.DataFrame,
    check_rows: list[dict[str, Any]],
) -> None:
    row = coverage_summary.iloc[0].to_dict()
    n_matched = int(row["n_inventory_matched_realtime"])
    n_live = int(row["n_inventory_matched_live_free"])
    n_realtime_only = int(row["n_realtime_only"])
    n_inventory_only = int(row["n_inventory_only"])
    _add_check(
        check_rows,
        "join_inventory_matches_some",
        "OK" if n_matched > 0 else "FAIL",
        f"matches={n_matched}",
        True,
    )
    _add_check(
        check_rows,
        "join_live_matches_some",
        "OK" if n_live > 0 else "WARNING",
        f"matches_con_free_valid={n_live}",
        False,
    )
    if n_live and n_live < 10:
        _add_check(
            check_rows,
            "join_live_matches_low",
            "WARNING",
            f"pocos aparcamientos con ocupación viva: {n_live}",
            False,
        )
    if n_realtime_only:
        _add_check(
            check_rows,
            "join_realtime_only",
            "WARNING",
            f"API sin match inventario={n_realtime_only}",
            False,
        )
    if n_inventory_only:
        _add_check(
            check_rows,
            "join_inventory_only",
            "WARNING",
            f"inventario con id_emt sin realtime={n_inventory_only}",
            False,
        )


def _build_diagnostics(
    realtime: pd.DataFrame,
    inventory: pd.DataFrame,
    joined: pd.DataFrame,
    join_diagnostics: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    realtime_ids = set(realtime["id_emt"].dropna().astype(int))
    inventory_ids = set(inventory["id_emt"].dropna().astype(int))
    n_inventory = len(inventory)
    n_inventory_with_id = int(inventory["id_emt"].notna().sum())
    n_matched = int(joined["has_realtime_response"].sum())
    n_live = int(joined["has_live_free"].sum())
    n_realtime = len(realtime)
    n_realtime_live = int(realtime["has_live_free"].sum())
    n_realtime_matched = len(realtime_ids & inventory_ids)
    coverage_status_summary = (
        joined["coverage_status"]
        .value_counts(dropna=False)
        .rename_axis("coverage_status")
        .reset_index(name="n")
    )
    coverage_summary = pd.DataFrame(
        [
            {
                "n_realtime_total": n_realtime,
                "n_realtime_unique_ids": int(realtime["id_emt"].nunique(dropna=True)),
                "n_realtime_with_free_raw": int(realtime["free_raw"].notna().sum()),
                "n_realtime_with_live_free": int(realtime["has_live_free"].sum()),
                "n_realtime_free_negative": int(
                    realtime["free_raw"].lt(0).fillna(False).sum()
                ),
                "n_inventory_total": n_inventory,
                "n_inventory_with_id_emt": n_inventory_with_id,
                "n_inventory_matched_realtime": n_matched,
                "n_inventory_matched_live_free": n_live,
                "n_realtime_only": len(realtime_ids - inventory_ids),
                "n_inventory_only": len(inventory_ids - realtime_ids),
                "pct_inventory_matched_realtime": (
                    n_matched / n_inventory_with_id if n_inventory_with_id else pd.NA
                ),
                "pct_inventory_matched_live_free": (
                    n_live / n_inventory_with_id if n_inventory_with_id else pd.NA
                ),
                "pct_realtime_matched_inventory": (
                    n_realtime_matched / len(realtime_ids) if realtime_ids else pd.NA
                ),
                "pct_inventory_total_matched_realtime": (
                    n_matched / n_inventory if n_inventory else pd.NA
                ),
                "pct_inventory_total_matched_live_free": (
                    n_live / n_inventory if n_inventory else pd.NA
                ),
                "pct_realtime_live_matched_inventory": (
                    n_live / n_realtime_live if n_realtime_live else pd.NA
                ),
            }
        ]
    )
    diagnostics = {
        "coverage_summary": coverage_summary,
        "coverage_status_summary": coverage_status_summary,
        "raw_columns": pd.DataFrame({"column": list(realtime.columns)}),
    }
    diagnostics.update(join_diagnostics)
    return diagnostics


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _add_output(rows: list[dict[str, Any]], output: str, path: Path) -> None:
    rows.append(
        {
            "output": output,
            "path": str(path),
            "exists": path.exists(),
            "size_mb": round(path.stat().st_size / 1024 / 1024, 3)
            if path.exists()
            else pd.NA,
        }
    )
