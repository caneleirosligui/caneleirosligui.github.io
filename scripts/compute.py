#!/usr/bin/env python3
"""Calcula classificaÃ§Ã£o, medalhas, copas, maior pontuaÃ§Ã£o e premiaÃ§Ã£o da Caneleiros Ligui.

Pode rodar localmente ou num GitHub Action (Python 3 + requests).
Grava os JSONs na pasta data/ do site.

Uso:
  python scripts/compute.py           # usa cache se houver; busca rodadas novas
  python scripts/compute.py --fetch   # atualiza na API a rodada atual e as faltantes
  python scripts/compute.py --offline # sÃ³ cache local, nÃ£o chama a API
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


def resolve_paths() -> dict[str, Path]:
    """Layout local: <repo>/scripts + <repo>/site/data.
    Repo do site no GitHub: <repo>/scripts + <repo>/data.
    """
    if (ROOT / "site").is_dir() and not (ROOT / "index.html").exists():
        site_data = ROOT / "site" / "data"
        project_data = ROOT / "data"
        return {
            "root": ROOT,
            "site_data": site_data,
            "teams": _first_existing(project_data / "teams.json", site_data / "teams.json"),
            "raw": project_data / "raw",
            "historico": site_data / "historico.json",
            "validacao_dir": project_data / "out",
        }
    site_data = ROOT / "data"
    return {
        "root": ROOT,
        "site_data": site_data,
        "teams": _first_existing(site_data / "teams.json", SCRIPT_DIR / "teams.json"),
        "raw": site_data / "historico_raw",
        "historico": site_data / "historico.json",
        "validacao_dir": site_data,
    }


def _first_existing(*paths: Path) -> Path:
    for p in paths:
        if p.exists():
            return p
    return paths[0]


PATHS = resolve_paths()
DATA = PATHS["root"] / "data"
RAW = PATHS["raw"]
OUT = PATHS["site_data"]

API_BASE = "https://api.cartola.globo.com"
UA = "Mozilla/5.0 (compatible; CaneleirosLigui/1.0; +https://caneleirosligui.github.io)"
FETCH_DELAY = 0.12
FETCH_RETRIES = 5

LIGA_ID = 3978835
LIGA = "CANELEIROS LIGUI"
TEMPORADA = 2026
ZONA_PREMIACAO = 5
TAXA = 100
INSCRITOS = 35
ARRECADADO = INSCRITOS * TAXA

STATUS_MERCADO = {
    1: "aberto",
    2: "fechado",
    3: "em_atualizacao",
    4: "manutencao",
}

PREMIOS_GERAL = {1: 800, 2: 600, 3: 400, 4: 300, 5: 200}
PREMIO_COPA = 200
PREMIO_MAIOR = 200
PREMIO_MEDALHAS = 200

# Fase de grupos (5 rodadas) + mata-mata em jogo Ãºnico.
COPAS = {
    1: {"nome": "Copa 1", "grupos": [1, 2, 3, 4, 5], "oitavas": 6, "quartas": 7, "semi": 8, "final": 9},
    2: {"nome": "Copa 2", "grupos": [10, 11, 12, 13, 14], "oitavas": 15, "quartas": 16, "semi": 17, "final": 18},
    3: {"nome": "Copa 3", "grupos": [19, 20, 21, 22, 23], "oitavas": 24, "quartas": 25, "semi": 26, "final": 27},
    4: {"nome": "Copa 4", "grupos": [28, 29, 30, 31, 32], "oitavas": 33, "quartas": 34, "semi": 35, "final": 36},
}

# Cruzamento extraÃ­do das fÃ³rmulas da planilha (COPA1!C27 em diante; igual nas 4 copas).
# Oitavas: 1AÃ—4B, 2AÃ—3B, 3AÃ—2B, 4AÃ—1B, 1CÃ—4D, 2CÃ—3D, 3CÃ—2D, 4CÃ—1D.
# Quartas: vencedores (1Ã—2), (3Ã—4), (5Ã—6), (7Ã—8).
# Semis: vencedores QF (1Ã—2) lado A/B e (3Ã—4) lado C/D.
OITAVAS_SEEDS = [
    ("1A", "4B"),
    ("2A", "3B"),
    ("3A", "2B"),
    ("4A", "1B"),
    ("1C", "4D"),
    ("2C", "3D"),
    ("3C", "2D"),
    ("4C", "1D"),
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def r2(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x) + 0.0, 4)


def almost_eq(a: float | None, b: float | None, tol: float = 0.015) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def now_sp() -> str:
    # America/Sao_Paulo = UTC-3 (sem horÃ¡rio de verÃ£o em 2026)
    return datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%dT%H:%M:%S-03:00")



class FetchError(RuntimeError):
    """Falha na API: o chamador deve preservar o cache existente."""


def http_get(url: str) -> dict:
    if requests is None:
        raise SystemExit("Este script precisa do pacote 'requests' (pip install requests).")
    last_err: Exception | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            wait = min(2 ** attempt, 20)
            print(f"  retry {attempt}/{FETCH_RETRIES} {url} ({exc}); sleep {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"falha ao baixar {url}: {last_err}")


def slim_from_payload(payload: dict, requested_rodada: int) -> dict:
    time_info = payload.get("time") or {}
    atletas = payload.get("atletas") or []
    n_com_pontos = sum(
        1
        for a in atletas
        if isinstance(a.get("pontos_num"), (int, float)) and a.get("pontos_num") != 0
    )
    return {
        "requested_rodada": requested_rodada,
        "rodada_atual": payload.get("rodada_atual"),
        "pontos": payload.get("pontos"),
        "pontos_campeonato": payload.get("pontos_campeonato"),
        "patrimonio": payload.get("patrimonio"),
        "variacao_patrimonio": payload.get("variacao_patrimonio"),
        "capitao_id": payload.get("capitao_id"),
        "nome": time_info.get("nome"),
        "nome_cartola": time_info.get("nome_cartola"),
        "url_escudo_png": time_info.get("url_escudo_png"),
        "url_escudo_svg": time_info.get("url_escudo_svg"),
        "time_id": time_info.get("time_id"),
        "n_atletas": len(atletas),
        "n_atletas_com_pontos": n_com_pontos,
    }


def empty_slim() -> dict:
    return {"mercado": {}, "times": {}}


def load_cached_slim() -> dict:
    for path in (PATHS["historico"], RAW / "slim.json"):
        if path.exists():
            data = load_json(path)
            if isinstance(data, dict) and "times" in data:
                return data
    return empty_slim()


def fetch_status() -> dict:
    return http_get(f"{API_BASE}/mercado/status")


def needs_refresh(rec: dict | None, rodada: int, rodada_atual: int) -> bool:
    if not rec:
        return True
    # Sempre reconsulta a rodada atual (pode ganhar pontos parciais) e a anterior
    # (correÃ§Ã£o oficial tardia).
    if rodada >= max(1, rodada_atual - 1):
        return True
    if rec.get("pontos") is None:
        return True
    return False


def payload_ok(payload: dict, tid: int) -> bool:
    if not isinstance(payload, dict):
        return False
    time_info = payload.get("time") or {}
    if time_info.get("time_id") not in (None, tid) and int(time_info.get("time_id") or 0) != int(tid):
        return False
    # Resposta vazia ou de erro, sem pontuaÃ§Ã£o e sem elenco
    if payload.get("pontos") is None and not payload.get("atletas") and payload.get("pontos_campeonato") is None:
        return False
    return True


def fetch_into(slim: dict, teams: list[dict], force_all: bool = False) -> dict:
    """Atualiza o slim. Em falha, levanta FetchError e nÃ£o devolve um slim esvaziado."""
    try:
        status = fetch_status()
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"mercado/status indisponÃ­vel: {exc}") from exc
    if not status or not status.get("rodada_atual"):
        raise FetchError("mercado/status veio vazio ou sem rodada_atual")

    mercado = {
        "rodada_atual": status.get("rodada_atual"),
        "status_mercado": status.get("status_mercado"),
        "temporada": status.get("temporada"),
        "bola_rolando": status.get("bola_rolando"),
        "mercado_pos_rodada": status.get("mercado_pos_rodada"),
        "fechamento": status.get("fechamento"),
        "game_over": status.get("game_over"),
        "nome_rodada": status.get("nome_rodada"),
        "rodada_final": status.get("rodada_final"),
    }
    rodada_atual = int(mercado["rodada_atual"] or 0)
    if rodada_atual < 1:
        raise FetchError(f"rodada_atual invÃ¡lida: {rodada_atual}")

    # Trabalha numa cÃ³pia; o cache original sÃ³ Ã© trocado no sucesso.
    import copy
    work = copy.deepcopy(slim) if slim.get("times") else empty_slim()
    work["mercado"] = mercado
    work.setdefault("times", {})
    rodadas = list(range(1, rodada_atual + 1))
    pending: list[tuple[int, int]] = []
    for t in teams:
        tid = str(t["time_id"])
        work["times"].setdefault(tid, {})
        for rod in rodadas:
            rec = work["times"][tid].get(str(rod))
            if force_all or needs_refresh(rec, rod, rodada_atual):
                pending.append((int(t["time_id"]), rod))
    total = len(pending)
    print(f"API: rodada_atual={rodada_atual} status_mercado={mercado.get('status_mercado')} pedidos={total}")
    failed: list[str] = []
    updated = 0
    for i, (tid, rod) in enumerate(pending, 1):
        time.sleep(FETCH_DELAY)
        try:
            payload = http_get(f"{API_BASE}/time/id/{tid}/{rod}")
            if not payload_ok(payload, tid):
                failed.append(f"{tid}/R{rod} (resposta invÃ¡lida)")
                continue
            work["times"][str(tid)][str(rod)] = slim_from_payload(payload, rod)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{tid}/R{rod} ({exc})")
        if i == total or i % 35 == 0:
            print(f"  {i}/{total} ok={updated} falhas={len(failed)}")

    # NÃ£o grava se a coleta veio vazia ou perdeu times demais
    if total and updated == 0:
        raise FetchError("nenhuma rodada foi atualizada; API parece fora do ar")
    if total and len(failed) > max(3, total // 5):
        raise FetchError(f"muitas falhas na API ({len(failed)}/{total}); cache preservado")
    # Todo time precisa ter ao menos a Ãºltima rodada encerrada anterior no cache
    missing_teams = [t["nome"] for t in teams if not work["times"].get(str(t["time_id"]))]
    if missing_teams:
        raise FetchError(f"times ausentes apÃ³s a coleta: {', '.join(missing_teams)}")
    if failed:
        print(f"avisos de coleta ({len(failed)}), usando cache no que faltou: {failed[:8]}", file=sys.stderr)
    return work


def load_inputs(fetch: bool = False, offline: bool = False, force_all: bool = False) -> tuple[list[dict], dict, dict, dict]:
    teams_path = PATHS["teams"]
    if not teams_path.exists():
        raise SystemExit(f"Falta {teams_path} (lista fixa dos 35 times).")
    meta = load_json(teams_path)
    teams = meta["teams"]
    grupos = meta["grupos"]
    slim = load_cached_slim()
    cached = bool(slim.get("times"))
    if offline:
        if not cached:
            raise SystemExit("Modo --offline, mas nÃ£o hÃ¡ historico.json nem data/raw/slim.json.")
    elif fetch or os.environ.get("GITHUB_ACTIONS") == "true" or not cached:
        try:
            slim = fetch_into(slim, teams, force_all=force_all)
            PATHS["historico"].parent.mkdir(parents=True, exist_ok=True)
            dump_json(PATHS["historico"], slim)
            if RAW.exists() or (PATHS["root"] / "data").exists():
                try:
                    RAW.mkdir(parents=True, exist_ok=True)
                    dump_json(RAW / "slim.json", slim)
                except OSError:
                    pass
        except FetchError as exc:
            print(f"API indisponÃ­vel ou incompleta: {exc}", file=sys.stderr)
            if not cached:
                raise SystemExit(f"Sem cache local e a API falhou: {exc}")
            print("Mantendo os dados em cache; nenhum JSON de saÃ­da serÃ¡ esvaziado.", file=sys.stderr)
    mercado = slim.get("mercado") or {}
    return teams, grupos, slim, mercado




def scored_rounds(slim: dict, teams: list[dict], mercado: dict) -> tuple[int, int, int | None]:
    """Retorna (rodada_atual, ultima_encerrada, rodada_parcial ou None)."""
    rodada_atual = int(mercado.get("rodada_atual") or 0)
    times = slim["times"]
    last = 0
    parcial = None
    for rod in range(1, rodada_atual + 1):
        n_with = 0
        n_present = 0
        stale = 0
        for t in teams:
            rec = times.get(str(t["time_id"]), {}).get(str(rod))
            if not rec:
                continue
            n_present += 1
            if rec.get("n_atletas_com_pontos", 0) > 0:
                n_with += 1
            # pontos_campeonato nÃ£o avanÃ§a e atletas zerados => rodada ainda nÃ£o pontuada
            if rod > 1:
                prev = times.get(str(t["time_id"]), {}).get(str(rod - 1))
                if prev and almost_eq(rec.get("pontos_campeonato"), prev.get("pontos_campeonato"), 1e-6):
                    if rec.get("n_atletas_com_pontos", 0) == 0:
                        stale += 1
        if n_with >= max(1, n_present // 4):
            if rod < rodada_atual or n_with == n_present:
                last = rod
            else:
                # alguns times jÃ¡ com pontos na rodada em andamento
                parcial = rod
                last = rod - 1 if last == rod - 1 or last == 0 else last
        elif rod < rodada_atual and n_present:
            # rodada passada deveria estar encerrada; se veio vazia, ainda assim conta se pontos existe
            # (histÃ³rico Ã s vezes nÃ£o traz atletas com pontos, mas traz o campo pontos)
            n_pts = 0
            for t in teams:
                rec = times.get(str(t["time_id"]), {}).get(str(rod))
                if rec and rec.get("pontos") is not None:
                    n_pts += 1
            if n_pts >= n_present * 0.9:
                last = rod
        elif rod == rodada_atual and stale >= n_present * 0.9:
            # rodada atual ainda sem pontuaÃ§Ã£o (campo pontos pode estar sujo com a rodada anterior)
            pass
    if last == 0 and rodada_atual:
        last = max(0, rodada_atual - 1)
    return rodada_atual, last, parcial


def points_of(slim: dict, time_id: int, rodada: int) -> float | None:
    rec = slim["times"].get(str(time_id), {}).get(str(rodada))
    if not rec:
        return None
    p = rec.get("pontos")
    return None if p is None else float(p)


def patrimonio_of(slim: dict, time_id: int, rodada: int) -> float | None:
    rec = slim["times"].get(str(time_id), {}).get(str(rodada))
    if not rec:
        return None
    p = rec.get("patrimonio")
    return None if p is None else float(p)


def pontos_campeonato_of(slim: dict, time_id: int, rodada: int) -> float | None:
    rec = slim["times"].get(str(time_id), {}).get(str(rodada))
    if not rec:
        return None
    p = rec.get("pontos_campeonato")
    return None if p is None else float(p)


def sum_points(slim: dict, time_id: int, rounds: list[int]) -> float:
    total = 0.0
    for r in rounds:
        p = points_of(slim, time_id, r)
        if p is not None:
            total += p
    return total


def standing_key_at(slim: dict, time_id: int, rodada: int) -> tuple:
    """Chave de desempate da classificaÃ§Ã£o geral no momento da rodada (maior Ã© melhor)."""
    pts = pontos_campeonato_of(slim, time_id, rodada)
    if pts is None:
        pts = sum_points(slim, time_id, list(range(1, rodada + 1)))
    pat = patrimonio_of(slim, time_id, rodada) or 0.0
    return (pts, pat)


def classify(teams: list[dict], slim: dict, upto: int) -> list[dict]:
    rows = []
    for t in teams:
        rec = slim["times"].get(str(t["time_id"]), {}).get(str(upto), {}) or {}
        pts = pontos_campeonato_of(slim, t["time_id"], upto)
        if pts is None:
            pts = sum_points(slim, t["time_id"], list(range(1, upto + 1)))
        pat = patrimonio_of(slim, t["time_id"], upto)
        por = {}
        for r in range(1, upto + 1):
            p = points_of(slim, t["time_id"], r)
            if p is not None:
                por[str(r)] = r2(p)
        escudo = rec.get("url_escudo_png") or t.get("escudo")
        rows.append({
            "time_id": t["time_id"],
            "nome": t["nome"],
            "dono": t["dono"],
            "escudo": escudo,
            "pontos": r2(pts) if pts is not None else 0.0,
            "pontos_raw": pts if pts is not None else 0.0,
            "patrimonio": r2(pat) if pat is not None else None,
            "patrimonio_raw": pat,
            "pontos_por_rodada": por,
        })
    rows.sort(key=lambda x: (-x["pontos_raw"], -(x["patrimonio_raw"] or 0.0), x["nome"].lower()))
    out = []
    for i, row in enumerate(rows, 1):
        item = {
            "posicao": i,
            "time_id": row["time_id"],
            "nome": row["nome"],
            "dono": row["dono"],
            "escudo": row["escudo"],
            "pontos": row["pontos"],
            "patrimonio": row["patrimonio"],
            "zona_premiacao": i <= ZONA_PREMIACAO,
            "pontos_por_rodada": row["pontos_por_rodada"],
        }
        out.append(item)
    return out


def load_ajustes() -> dict:
    """Ajustes manuais opcionais (site/data/ajustes.json ou data/ajustes.json)."""
    candidates = [
        PATHS["site_data"] / "ajustes.json",
        PATHS["root"] / "data" / "ajustes.json",
        SCRIPT_DIR.parent / "data" / "ajustes.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                data = load_json(path)
                if isinstance(data, dict):
                    return data
            except Exception as exc:  # noqa: BLE001
                print(f"avisos: nÃ£o li {path}: {exc}", file=sys.stderr)
    return {}


def _lookup_team(teams: list[dict], ref: Any) -> dict | None:
    if ref is None:
        return None
    if isinstance(ref, dict):
        ref = ref.get("time_id") or ref.get("nome")
    for t in teams:
        if ref == t["time_id"] or str(ref) == str(t["time_id"]) or ref == t["nome"]:
            return t
    return None


def apply_ajuste_medalhas(oficial: dict, teams: list[dict], slim: dict, rodada: int, ajustes: dict) -> dict:
    """Se o ajuste da rodada estiver ativo, substitui ouro/prata/bronze. SenÃ£o, regra oficial."""
    bloco = (ajustes.get("medalhas_por_rodada") or {}).get(str(rodada)) or (ajustes.get("medalhas_por_rodada") or {}).get(rodada)
    if not bloco or not bloco.get("ativo"):
        return oficial
    out = {"ouro": [], "prata": [], "bronze": [], "ajuste_manual": True}
    by_id = {t["time_id"]: t for t in teams}

    def build(refs: list) -> list[dict]:
        itens = []
        for ref in refs or []:
            t = _lookup_team(teams, ref)
            if not t:
                print(f"ajuste R{rodada}: time nÃ£o encontrado ({ref})", file=sys.stderr)
                continue
            p = points_of(slim, t["time_id"], rodada)
            itens.append({"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "pontos": r2(p) if p is not None else None})
        return itens

    out["ouro"] = build(bloco.get("ouro"))
    out["prata"] = build(bloco.get("prata"))
    out["bronze"] = build(bloco.get("bronze"))
    return out


def medals_for_round(teams: list[dict], slim: dict, rodada: int, ajustes: dict | None = None) -> dict:
    scored = []
    for t in teams:
        p = points_of(slim, t["time_id"], rodada)
        if p is None:
            continue
        scored.append({"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "pontos": r2(p), "pontos_raw": p})
    scored.sort(key=lambda x: -x["pontos_raw"])
    # Agrupa por pontuaÃ§Ã£o exata (tolerÃ¢ncia pequena para ruÃ­do binÃ¡rio)
    clusters: list[list[dict]] = []
    for item in scored:
        if not clusters or abs(clusters[-1][0]["pontos_raw"] - item["pontos_raw"]) > 1e-6:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    ouro = [clean_medalist(x) for x in clusters[0]] if len(clusters) > 0 else []
    prata = [clean_medalist(x) for x in clusters[1]] if len(clusters) > 1 else []
    bronze = [clean_medalist(x) for x in clusters[2]] if len(clusters) > 2 else []
    oficial = {"ouro": ouro, "prata": prata, "bronze": bronze}
    return apply_ajuste_medalhas(oficial, teams, slim, rodada, ajustes or {})


def clean_medalist(x: dict) -> dict:
    return {"time_id": x["time_id"], "nome": x["nome"], "dono": x["dono"], "pontos": x["pontos"]}


def medal_table(teams: list[dict], por_rodada: dict, classificacao: list[dict]) -> list[dict]:
    pos = {row["time_id"]: row["posicao"] for row in classificacao}
    pat = {row["time_id"]: row["patrimonio"] or 0 for row in classificacao}
    counts = {t["time_id"]: {"ouro": 0, "prata": 0, "bronze": 0, **{k: t[k] for k in ("time_id", "nome", "dono")}} for t in teams}
    for _rod, med in por_rodada.items():
        for kind in ("ouro", "prata", "bronze"):
            for m in med[kind]:
                counts[m["time_id"]][kind] += 1
    rows = list(counts.values())
    rows.sort(key=lambda x: (-x["ouro"], -x["prata"], -x["bronze"], pos.get(x["time_id"], 999), -(pat.get(x["time_id"]) or 0)))
    out = []
    for i, row in enumerate(rows, 1):
        out.append({
            "posicao": i,
            "time_id": row["time_id"],
            "nome": row["nome"],
            "dono": row["dono"],
            "ouro": row["ouro"],
            "prata": row["prata"],
            "bronze": row["bronze"],
            "campeao": i == 1,
        })
    return out


def maior_pontuacao(teams: list[dict], slim: dict, upto: int) -> dict:
    best = -1.0
    vencedores: list[dict] = []
    historico = []
    for rod in range(1, upto + 1):
        rod_best = -1.0
        rod_times = []
        for t in teams:
            p = points_of(slim, t["time_id"], rod)
            if p is None:
                continue
            if p > rod_best + 1e-9:
                rod_best = p
                rod_times = [{"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "pontos": r2(p)}]
            elif abs(p - rod_best) <= 1e-9:
                rod_times.append({"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "pontos": r2(p)})
        if rod_times:
            historico.append({"rodada": rod, "pontos": r2(rod_best), "times": rod_times})
            if rod_best > best + 1e-9:
                best = rod_best
                vencedores = [{"rodada": rod, **x} for x in rod_times]
            elif abs(rod_best - best) <= 1e-9:
                vencedores.extend({"rodada": rod, **x} for x in rod_times)
    return {
        "pontos": r2(best) if best >= 0 else None,
        "vencedores": vencedores,
        "empate": len(vencedores) > 1,
        "historico_maximos_por_rodada": historico,
    }


def seed_parse(seed: str) -> tuple[str, int]:
    return seed[-1], int(seed[:-1])


def cup_group_tables(teams_by_name: dict, names: dict[str, list[str]], slim: dict, group_rounds: list[int], last: int) -> dict:
    end_round = min(group_rounds[-1], last) if last else 0
    used = [r for r in group_rounds if r <= last]
    grupos_out = {}
    ranked_by_group = {}
    for g, nomes in names.items():
        rows = []
        for nome in nomes:
            t = teams_by_name[nome]
            pts = sum_points(slim, t["time_id"], used) if used else 0.0
            key = standing_key_at(slim, t["time_id"], end_round) if end_round else (0.0, 0.0)
            por = {str(r): r2(points_of(slim, t["time_id"], r)) for r in used}
            rows.append({
                "time_id": t["time_id"],
                "nome": t["nome"],
                "dono": t["dono"],
                "pontos": r2(pts),
                "pontos_raw": pts,
                "classificacao_geral_pts": key[0],
                "patrimonio": r2(key[1]),
                "pontos_por_rodada": por,
            })
        # Desempate oficial: melhor posiÃ§Ã£o na geral no momento (pts gerais, depois cartoletas)
        rows.sort(key=lambda x: (-x["pontos_raw"], -(x["classificacao_geral_pts"] or 0), -(x["patrimonio"] or 0), x["nome"].lower()))
        ranked = []
        for i, row in enumerate(rows, 1):
            ranked.append({
                "posicao": i,
                "time_id": row["time_id"],
                "nome": row["nome"],
                "dono": row["dono"],
                "pontos": row["pontos"],
                "classificado": i <= 4 and len(used) == len(group_rounds),
                "pontos_por_rodada": row["pontos_por_rodada"],
            })
        grupos_out[g] = ranked
        ranked_by_group[g] = ranked
    return grupos_out


def pick_seed(ranked_by_group: dict, seed: str) -> dict | None:
    grupo, pos = seed_parse(seed)
    tabela = ranked_by_group.get(grupo) or []
    if pos - 1 < len(tabela) and tabela[pos - 1].get("classificado"):
        t = tabela[pos - 1]
        return {"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "seed": seed, "posicao_grupo": pos, "grupo": grupo}
    if pos - 1 < len(tabela) and not tabela[pos - 1].get("classificado"):
        # grupos ainda incompletos
        t = tabela[pos - 1]
        return {"time_id": t["time_id"], "nome": t["nome"], "dono": t["dono"], "seed": seed, "posicao_grupo": pos, "grupo": grupo, "provisorio": True}
    return None


def winner_of(a: dict, b: dict, slim: dict, rodada: int) -> tuple[dict, str | None]:
    """Vencedor de um jogo Ãºnico. Desempate: melhor classificaÃ§Ã£o geral no momento, depois cartoletas."""
    pa = points_of(slim, a["time_id"], rodada)
    pb = points_of(slim, b["time_id"], rodada)
    a2 = {**a, "pontos": r2(pa)}
    b2 = {**b, "pontos": r2(pb)}
    if pa is None or pb is None:
        return {"pendente": True, "a": a2, "b": b2}, None
    if pa > pb + 1e-9:
        return {"a": a2, "b": b2, "vencedor_id": a["time_id"], "empate": False, "pendente": False}, None
    if pb > pa + 1e-9:
        return {"a": a2, "b": b2, "vencedor_id": b["time_id"], "empate": False, "pendente": False}, None
    ka = standing_key_at(slim, a["time_id"], rodada)
    kb = standing_key_at(slim, b["time_id"], rodada)
    if ka > kb:
        motivo = "classificacao_geral"
        vid = a["time_id"]
    elif kb > ka:
        motivo = "classificacao_geral"
        vid = b["time_id"]
    else:
        motivo = "sorteio_necessario"
        vid = None
    return {
        "a": a2,
        "b": b2,
        "vencedor_id": vid,
        "empate": True,
        "pendente": vid is None,
        "desempate": motivo,
    }, motivo


def build_knockout(ranked_by_group: dict, slim: dict, cfg: dict, last: int, groups_complete: bool) -> dict:
    def team_from_id(tid: int, side: dict) -> dict:
        return {"time_id": tid, "nome": side["nome"], "dono": side["dono"]}

    oitavas = []
    oitavas_winners: list[dict | None] = []
    for i, (sa, sb) in enumerate(OITAVAS_SEEDS, 1):
        ta = pick_seed(ranked_by_group, sa) if groups_complete else None
        tb = pick_seed(ranked_by_group, sb) if groups_complete else None
        jogo = {
            "jogo": i,
            "lado": "AB" if i <= 4 else "CD",
            "rodada": cfg["oitavas"],
            "seed_a": sa,
            "seed_b": sb,
        }
        if not ta or not tb or cfg["oitavas"] > last:
            jogo.update({"a": ta, "b": tb, "pendente": True})
            oitavas.append(jogo)
            oitavas_winners.append(None)
            continue
        res, _ = winner_of(ta, tb, slim, cfg["oitavas"])
        jogo.update(res)
        oitavas.append(jogo)
        if res.get("vencedor_id"):
            wsrc = ta if res["vencedor_id"] == ta["time_id"] else tb
            oitavas_winners.append(wsrc)
        else:
            oitavas_winners.append(None)

    def pair_round(pairs: list[tuple[int, int]], winners_src: list[dict | None], rodada: int, start_jogo: int, lados: list[str]) -> tuple[list[dict], list[dict | None]]:
        jogos = []
        winners: list[dict | None] = []
        for idx, (i1, i2) in enumerate(pairs):
            jogo = {"jogo": start_jogo + idx, "lado": lados[idx], "rodada": rodada}
            wa, wb = winners_src[i1], winners_src[i2]
            if not wa or not wb or rodada > last:
                jogo.update({"a": wa, "b": wb, "pendente": True})
                jogos.append(jogo)
                winners.append(None)
                continue
            res, _ = winner_of(wa, wb, slim, rodada)
            jogo.update(res)
            jogos.append(jogo)
            if res.get("vencedor_id"):
                winners.append(wa if res["vencedor_id"] == wa["time_id"] else wb)
            else:
                winners.append(None)
        return jogos, winners

    quartas, q_winners = pair_round([(0, 1), (2, 3), (4, 5), (6, 7)], oitavas_winners, cfg["quartas"], 1, ["AB", "AB", "CD", "CD"])
    semis, s_winners = pair_round([(0, 1), (2, 3)], q_winners, cfg["semi"], 1, ["AB", "CD"])

    final = {"rodada": cfg["final"], "lado": "final"}
    campeao = None
    if s_winners[0] and s_winners[1] and cfg["final"] <= last:
        res, _ = winner_of(s_winners[0], s_winners[1], slim, cfg["final"])
        final.update(res)
        if res.get("vencedor_id"):
            campeao = s_winners[0] if res["vencedor_id"] == s_winners[0]["time_id"] else s_winners[1]
            campeao = {"time_id": campeao["time_id"], "nome": campeao["nome"], "dono": campeao["dono"]}
    else:
        final.update({"a": s_winners[0], "b": s_winners[1], "pendente": True})

    return {
        "oitavas": oitavas,
        "quartas": quartas,
        "semifinais": semis,
        "final": final,
        "campeao": campeao,
    }


def build_copa(n: int, grupos_nomes: dict, teams_by_name: dict, slim: dict, last: int) -> dict:
    cfg = COPAS[n]
    grupos = cup_group_tables(teams_by_name, grupos_nomes, slim, cfg["grupos"], last)
    groups_complete = last >= cfg["grupos"][-1]
    mata = build_knockout(grupos, slim, cfg, last, groups_complete)
    fase = "grupos"
    if last >= cfg["final"] and mata.get("campeao"):
        fase = "encerrada"
    elif last >= cfg["final"]:
        fase = "final"
    elif last >= cfg["semi"]:
        fase = "semifinal"
    elif last >= cfg["quartas"]:
        fase = "quartas"
    elif last >= cfg["oitavas"]:
        fase = "oitavas"
    elif last >= cfg["grupos"][0]:
        fase = "grupos"
    else:
        fase = "nao_iniciada"
    return {
        "id": n,
        "nome": cfg["nome"],
        "rodadas_grupos": cfg["grupos"],
        "oitavas_rodada": cfg["oitavas"],
        "quartas_rodada": cfg["quartas"],
        "semi_rodada": cfg["semi"],
        "final_rodada": cfg["final"],
        "fase": fase,
        "grupos": grupos,
        "mata_mata": mata,
    }


def build_premiacao(classificacao: list[dict], copas: list[dict], maior: dict, quadro: list[dict], last: int, rodada_final: int = 38) -> dict:
    itens = []
    for pos, valor in PREMIOS_GERAL.items():
        row = classificacao[pos - 1]
        itens.append({
            "id": f"geral_{pos}",
            "label": f"{pos}Âº na classificaÃ§Ã£o geral",
            "valor": valor,
            "status": "definido" if last >= rodada_final else "provisorio",
            "situacao": "campeÃ£o" if last >= rodada_final else "lÃ­der no momento",
            "ganhadores": [{"time_id": row["time_id"], "nome": row["nome"], "dono": row["dono"]}],
        })
    for copa in copas:
        campeao = copa["mata_mata"].get("campeao")
        itens.append({
            "id": f"copa_{copa['id']}",
            "label": copa["nome"],
            "valor": PREMIO_COPA,
            "status": "definido" if campeao else "em_andamento" if copa["fase"] != "nao_iniciada" else "pendente",
            "situacao": "campeÃ£o" if campeao else None,
            "ganhadores": [campeao] if campeao else [],
        })
    itens.append({
        "id": "maior_pontuacao",
        "label": "Maior pontuaÃ§Ã£o de uma rodada",
        "valor": PREMIO_MAIOR,
        "status": "provisorio" if last < rodada_final else "definido",
        "situacao": "campeÃ£o" if last >= rodada_final else "lÃ­der no momento",
        "ganhadores": [
            {"time_id": v["time_id"], "nome": v["nome"], "dono": v["dono"], "rodada": v["rodada"], "pontos": v["pontos"]}
            for v in maior["vencedores"]
        ],
        "observacao": "HÃ¡ empate na maior pontuaÃ§Ã£o; o regulamento prevÃª sorteio se nenhum outro critÃ©rio resolver." if maior.get("empate") else None,
    })
    lideres = [q for q in quadro if q["posicao"] == 1]
    itens.append({
        "id": "quadro_medalhas",
        "label": "Quadro de medalhas",
        "valor": PREMIO_MEDALHAS,
        "status": "provisorio" if last < rodada_final else "definido",
        "situacao": "campeÃ£o" if last >= rodada_final else "lÃ­der no momento",
        "ganhadores": [{"time_id": q["time_id"], "nome": q["nome"], "dono": q["dono"]} for q in lideres],
    })
    for it in itens:
        if it.get("observacao") is None:
            it.pop("observacao", None)
    return {
        "inscritos": INSCRITOS,
        "taxa": TAXA,
        "arrecadado": ARRECADADO,
        "total_distribuido": sum(PREMIOS_GERAL.values()) + 4 * PREMIO_COPA + PREMIO_MAIOR + PREMIO_MEDALHAS,
        "rodada_referencia": last,
        "itens": itens,
    }


def team_index(teams: list[dict]) -> dict[str, dict]:
    return {t["nome"]: t for t in teams}


def strip_volatile(obj: Any) -> Any:
    """Remove campos que mudam sÃ³ de relÃ³gio, para decidir se vale gravar/commitar."""
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in {"atualizado_em"}}
    if isinstance(obj, list):
        return [strip_volatile(x) for x in obj]
    return obj


def dump_if_changed(path: Path, obj: Any) -> bool:
    if path.exists():
        try:
            old = load_json(path)
            if strip_volatile(old) == strip_volatile(obj):
                return False
        except Exception:  # noqa: BLE001
            pass
    dump_json(path, obj)
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atualiza os JSONs da Caneleiros Ligui.")
    p.add_argument("--fetch", action="store_true", help="consulta a API (padrÃ£o no GitHub Action)")
    p.add_argument("--offline", action="store_true", help="nÃ£o chama a API; usa sÃ³ o cache")
    p.add_argument("--fetch-all", action="store_true", help="baixa de novo todas as rodadas")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    offline = bool(args.offline)
    fetch = bool(args.fetch or args.fetch_all or os.environ.get("GITHUB_ACTIONS") == "true")
    if offline:
        fetch = False
    # Local com cache completo: nÃ£o precisa ir Ã  API, a menos que peÃ§am --fetch.
    if not fetch and not offline and not load_cached_slim().get("times") and not (RAW / "slim.json").exists() and not PATHS["historico"].exists():
        fetch = True
    teams, grupos, slim, mercado = load_inputs(fetch=fetch, offline=offline, force_all=bool(args.fetch_all))
    rodada_atual, last, parcial = scored_rounds(slim, teams, mercado)
    rodada_final = int(mercado.get("rodada_final") or 38)
    # Medalhas, recorde e copas usam sÃ³ rodadas encerradas (copas vÃ£o atÃ© a 36).
    # ClassificaÃ§Ã£o: se a rodada atual jÃ¡ tem pontos parciais, soma esses pontos e marca parcial.
    if parcial and last:
        classificacao = classify(teams, slim, last)
        # Recalcula totais com a rodada em andamento, sem tratar como encerrada.
        for row in classificacao:
            extra = points_of(slim, row["time_id"], parcial)
            if extra is not None:
                row["pontos_raw"] = (row.get("pontos") or 0) + extra  # temp
        # classify already set pontos from pontos_campeonato at `last`. Add partial points.
        for row in classificacao:
            extra = points_of(slim, row["time_id"], parcial)
            base = pontos_campeonato_of(slim, row["time_id"], last)
            if base is None:
                base = row["pontos"] or 0
            total = (base or 0) + (extra or 0)
            row["pontos"] = r2(total)
            pat = patrimonio_of(slim, row["time_id"], parcial)
            if pat is not None:
                row["patrimonio"] = r2(pat)
        classificacao.sort(key=lambda x: (-(x["pontos"] or 0), -(x["patrimonio"] or 0), x["nome"].lower()))
        for i, row in enumerate(classificacao, 1):
            row["posicao"] = i
            row["zona_premiacao"] = i <= ZONA_PREMIACAO
            row.pop("pontos_raw", None)
    else:
        classificacao = classify(teams, slim, last)

    for row in classificacao:
        if row["posicao"] in PREMIOS_GERAL:
            row["premio"] = PREMIOS_GERAL[row["posicao"]]
        else:
            row["premio"] = None

    ajustes = load_ajustes()
    por_rodada = {str(r): medals_for_round(teams, slim, r, ajustes) for r in range(1, last + 1)}
    quadro = medal_table(teams, por_rodada, classificacao)
    maior = maior_pontuacao(teams, slim, last)

    by_name = team_index(teams)
    copas = []
    for n in (1, 2, 3, 4):
        key = f"COPA{n}"
        copas.append(build_copa(n, grupos[key], by_name, slim, last))

    premiacao = build_premiacao(classificacao, copas, maior, quadro, last, rodada_final=rodada_final)

    # HistÃ³rico slim (pontos/patrimÃ´nio por rodada) versionado junto com o site.
    PATHS["historico"].parent.mkdir(parents=True, exist_ok=True)
    dump_json(PATHS["historico"], slim)

    status_id = mercado.get("status_mercado")
    meta = {
        "liga_id": LIGA_ID,
        "liga": LIGA,
        "temporada": TEMPORADA,
        "rodada_atual": rodada_atual,
        "ultima_rodada_encerrada": last,
        "rodada_parcial": parcial,
        "rodada_final": rodada_final,
        "status_mercado": status_id,
        "status_mercado_nome": STATUS_MERCADO.get(status_id, "desconhecido"),
        "bola_rolando": mercado.get("bola_rolando"),
        "atualizado_em": now_sp(),
        "fonte_api": "https://api.cartola.globo.com",
        "nota_pontos": "O campo 'pontos' da API jÃ¡ inclui o capitÃ£o. O campo 'patrimonio' no endpoint /time/id/{id}/{rodada} Ã© histÃ³rico daquela rodada. As rodadas 37 e 38 entram na classificaÃ§Ã£o, nas medalhas e no recorde; as copas terminam na 36.",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    outputs = {
        "meta.json": meta,
        "classificacao.json": {
            "rodada_referencia": last,
            "parcial": bool(parcial),
            "rodada_parcial": parcial,
            "zona_premiacao": ZONA_PREMIACAO,
            "tabela": classificacao,
        },
        "medalhas.json": {
            "rodada_referencia": last,
            "quadro": quadro,
            "por_rodada": por_rodada,
        },
        "maior_pontuacao.json": {"rodada_referencia": last, **maior},
        "copas.json": {"rodada_referencia": last, "copas": copas},
        "premiacao.json": premiacao,
        "site.json": {
            "meta": meta,
            "classificacao": classificacao,
            "medalhas": {"quadro": quadro, "por_rodada": por_rodada},
            "maior_pontuacao": maior,
            "copas": copas,
            "premiacao": premiacao,
        },
    }
    changed = []
    for name, obj in outputs.items():
        if dump_if_changed(OUT / name, obj):
            changed.append(name)
    # HistÃ³rico slim: sÃ³ grava de novo se o conteÃºdo (sem mercado volÃ¡til demais) jÃ¡ foi
    # persistido no fetch; aqui regrava para o layout local do site.
    if PATHS["historico"].parent.exists():
        dump_if_changed(PATHS["historico"], slim)
    if not changed:
        print(f"sem mudanÃ§as em {OUT}  (encerrada=R{last}, atual=R{rodada_atual}, parcial={parcial})")
    else:
        print(f"atualizado em {OUT} ({', '.join(changed)})  (encerrada=R{last}, atual=R{rodada_atual}, parcial={parcial})")
    print("lÃ­der:", classificacao[0]["nome"], classificacao[0]["pontos"])
    print("top5:", ", ".join(f"{r['posicao']}.{r['nome']}" for r in classificacao[:5]))
    print("medalhas:", ", ".join(f"{q['nome']} {q['ouro']}O/{q['prata']}P/{q['bronze']}B" for q in quadro[:5]))
    if maior["vencedores"]:
        v = maior["vencedores"][0]
        print("maior pontuaÃ§Ã£o:", v["pontos"], "R" + str(v["rodada"]), v["nome"])
    for c in copas:
        camp = c["mata_mata"].get("campeao")
        print(f"{c['nome']} fase={c['fase']} campeao={camp['nome'] if camp else '-'}")


if __name__ == "__main__":
    main()
