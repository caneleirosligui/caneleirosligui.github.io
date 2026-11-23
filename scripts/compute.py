#!/usr/bin/env python3
"""Calcula classificação, medalhas, copas, maior pontuação e premiação da Caneleiros Ligui.

Pode rodar localmente ou num GitHub Action (Python 3 + requests).
Grava os JSONs na pasta data/ do site.

Uso:
  python scripts/compute.py           # usa cache se houver; busca rodadas novas
  python scripts/compute.py --fetch   # consulta a API; o gate decide se grava parciais, consolida ou não faz nada
  python scripts/compute.py --force   # ignora o gate e roda tudo (também vale GITHUB_EVENT_NAME=workflow_dispatch)
  python scripts/compute.py --offline # só cache local, não chama a API
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

# Janela de um jogo: do início até 2h30 depois (partida típica + 30 min).
TZ_SP = timezone(timedelta(hours=-3))
JANELA_APOS_INICIO = timedelta(hours=2, minutes=30)

LIGA_ID = 3978835
LIGA = "CANELEIROS LIGUI"
TEMPORADA = 2026
ZONA_PREMIACAO = 5
CAPITAO_FATOR = 1.5  # regra 2026 (desde 2022); confirmado na API
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

# Fase de grupos (5 rodadas) + mata-mata em jogo único.
COPAS = {
    1: {"nome": "Copa 1", "grupos": [1, 2, 3, 4, 5], "oitavas": 6, "quartas": 7, "semi": 8, "final": 9},
    2: {"nome": "Copa 2", "grupos": [10, 11, 12, 13, 14], "oitavas": 15, "quartas": 16, "semi": 17, "final": 18},
    3: {"nome": "Copa 3", "grupos": [19, 20, 21, 22, 23], "oitavas": 24, "quartas": 25, "semi": 26, "final": 27},
    4: {"nome": "Copa 4", "grupos": [28, 29, 30, 31, 32], "oitavas": 33, "quartas": 34, "semi": 35, "final": 36},
}

# Cruzamento extraído das fórmulas da planilha (COPA1!C27 em diante; igual nas 4 copas).
# Oitavas: 1A×4B, 2A×3B, 3A×2B, 4A×1B, 1C×4D, 2C×3D, 3C×2D, 4C×1D.
# Quartas: vencedores (1×2), (3×4), (5×6), (7×8).
# Semis: vencedores QF (1×2) lado A/B e (3×4) lado C/D.
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
    # America/Sao_Paulo = UTC-3 (sem horário de verão em 2026)
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


def http_get_optional(url: str) -> dict | None:
    """GET que devolve None em 400/404 ou falha de rede, sem esgotar retries."""
    if requests is None:
        raise SystemExit("Este script precisa do pacote 'requests' (pip install requests).")
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
        if resp.status_code in (400, 404):
            return None
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: não obtive {url} ({exc})", file=sys.stderr)
        return None



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
    # (correção oficial tardia).
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
    # Resposta vazia ou de erro, sem pontuação e sem elenco
    if payload.get("pontos") is None and not payload.get("atletas") and payload.get("pontos_campeonato") is None:
        return False
    return True


def fetch_into(slim: dict, teams: list[dict], force_all: bool = False) -> dict:
    """Atualiza o slim. Em falha, levanta FetchError e não devolve um slim esvaziado."""
    try:
        status = fetch_status()
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"mercado/status indisponível: {exc}") from exc
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
        raise FetchError(f"rodada_atual inválida: {rodada_atual}")

    # Trabalha numa cópia; o cache original só é trocado no sucesso.
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
                failed.append(f"{tid}/R{rod} (resposta inválida)")
                continue
            work["times"][str(tid)][str(rod)] = slim_from_payload(payload, rod)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{tid}/R{rod} ({exc})")
        if i == total or i % 35 == 0:
            print(f"  {i}/{total} ok={updated} falhas={len(failed)}")

    # Não grava se a coleta veio vazia ou perdeu times demais
    if total and updated == 0:
        raise FetchError("nenhuma rodada foi atualizada; API parece fora do ar")
    if total and len(failed) > max(3, total // 5):
        raise FetchError(f"muitas falhas na API ({len(failed)}/{total}); cache preservado")
    # Todo time precisa ter ao menos a última rodada encerrada anterior no cache
    missing_teams = [t["nome"] for t in teams if not work["times"].get(str(t["time_id"]))]
    if missing_teams:
        raise FetchError(f"times ausentes após a coleta: {', '.join(missing_teams)}")
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
            raise SystemExit("Modo --offline, mas não há historico.json nem data/raw/slim.json.")
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
            print(f"API indisponível ou incompleta: {exc}", file=sys.stderr)
            if not cached:
                raise SystemExit(f"Sem cache local e a API falhou: {exc}")
            print("Mantendo os dados em cache; nenhum JSON de saída será esvaziado.", file=sys.stderr)
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
            # pontos_campeonato não avança e atletas zerados => rodada ainda não pontuada
            if rod > 1:
                prev = times.get(str(t["time_id"]), {}).get(str(rod - 1))
                if prev and almost_eq(rec.get("pontos_campeonato"), prev.get("pontos_campeonato"), 1e-6):
                    if rec.get("n_atletas_com_pontos", 0) == 0:
                        stale += 1
        if n_with >= max(1, n_present // 4):
            if rod < rodada_atual or n_with == n_present:
                last = rod
            else:
                # alguns times já com pontos na rodada em andamento
                parcial = rod
                last = rod - 1 if last == rod - 1 or last == 0 else last
        elif rod < rodada_atual and n_present:
            # rodada passada deveria estar encerrada; se veio vazia, ainda assim conta se pontos existe
            # (histórico às vezes não traz atletas com pontos, mas traz o campo pontos)
            n_pts = 0
            for t in teams:
                rec = times.get(str(t["time_id"]), {}).get(str(rod))
                if rec and rec.get("pontos") is not None:
                    n_pts += 1
            if n_pts >= n_present * 0.9:
                last = rod
        elif rod == rodada_atual and stale >= n_present * 0.9:
            # rodada atual ainda sem pontuação (campo pontos pode estar sujo com a rodada anterior)
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
    """Chave de desempate da classificação geral no momento da rodada (maior é melhor)."""
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
                print(f"avisos: não li {path}: {exc}", file=sys.stderr)
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
    """Se o ajuste da rodada estiver ativo, substitui ouro/prata/bronze. Senão, regra oficial."""
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
                print(f"ajuste R{rodada}: time não encontrado ({ref})", file=sys.stderr)
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
    # Agrupa por pontuação exata (tolerância pequena para ruído binário)
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
        # Desempate oficial: melhor posição na geral no momento (pts gerais, depois cartoletas)
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
    """Vencedor de um jogo único. Desempate: melhor classificação geral no momento, depois cartoletas."""
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
            "label": f"{pos}º na classificação geral",
            "valor": valor,
            "status": "definido" if last >= rodada_final else "provisorio",
            "situacao": "campeão" if last >= rodada_final else "líder no momento",
            "ganhadores": [{"time_id": row["time_id"], "nome": row["nome"], "dono": row["dono"]}],
        })
    for copa in copas:
        campeao = copa["mata_mata"].get("campeao")
        itens.append({
            "id": f"copa_{copa['id']}",
            "label": copa["nome"],
            "valor": PREMIO_COPA,
            "status": "definido" if campeao else "em_andamento" if copa["fase"] != "nao_iniciada" else "pendente",
            "situacao": "campeão" if campeao else None,
            "ganhadores": [campeao] if campeao else [],
        })
    itens.append({
        "id": "maior_pontuacao",
        "label": "Maior pontuação de uma rodada",
        "valor": PREMIO_MAIOR,
        "status": "provisorio" if last < rodada_final else "definido",
        "situacao": "campeão" if last >= rodada_final else "líder no momento",
        "ganhadores": [
            {"time_id": v["time_id"], "nome": v["nome"], "dono": v["dono"], "rodada": v["rodada"], "pontos": v["pontos"]}
            for v in maior["vencedores"]
        ],
        "observacao": "Há empate na maior pontuação; o regulamento prevê sorteio se nenhum outro critério resolver." if maior.get("empate") else None,
    })
    lideres = [q for q in quadro if q["posicao"] == 1]
    itens.append({
        "id": "quadro_medalhas",
        "label": "Quadro de medalhas",
        "valor": PREMIO_MEDALHAS,
        "status": "provisorio" if last < rodada_final else "definido",
        "situacao": "campeão" if last >= rodada_final else "líder no momento",
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
    """Remove campos que mudam só de relógio, para decidir se vale gravar/commitar."""
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



def resumo_jogos(payload: dict | None) -> dict:
    out = {"encerrados": 0, "em_andamento": 0, "a_jogar": 0, "nao_validos": 0, "total": 0}
    if not payload:
        return out
    partidas = payload.get("partidas") or []
    out["total"] = len(partidas)
    for p in partidas:
        if p.get("valida") is False:
            out["nao_validos"] += 1
            continue
        st = str(p.get("status_transmissao_tr") or "").upper()
        if st == "ENCERRADA":
            out["encerrados"] += 1
        elif st == "EM_ANDAMENTO":
            out["em_andamento"] += 1
        else:
            out["a_jogar"] += 1
    return out


def texto_jogos(jogos: dict) -> str:
    bits = []
    n = jogos.get("encerrados") or 0
    if n:
        bits.append(f"{n} encerrado" + ("s" if n != 1 else ""))
    n = jogos.get("em_andamento") or 0
    if n:
        bits.append(f"{n} em andamento")
    n = jogos.get("a_jogar") or 0
    if n:
        bits.append(f"{n} a jogar")
    n = jogos.get("nao_validos") or 0
    extra = f" {n} jogo" + ("s" if n != 1 else "") + " não válido" + ("s" if n != 1 else "") + " nesta rodada." if n else ""
    if not bits and not extra:
        return ""
    corpo = ", ".join(bits)
    if corpo:
        return "Jogos da rodada: " + corpo + "." + extra
    return extra.strip()


def lista_reservas(payload: dict | None) -> list[dict]:
    """Normaliza o banco (lista ou dict por posição) de /time/id/{id}/{rodada}."""
    if not payload:
        return []
    raw = payload.get("reservas")
    if not raw:
        return []
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict) and a.get("atleta_id") is not None]
    if isinstance(raw, dict):
        out: list[dict] = []
        for v in raw.values():
            if isinstance(v, dict) and v.get("atleta_id") is not None:
                out.append(v)
            elif isinstance(v, list):
                out.extend(a for a in v if isinstance(a, dict) and a.get("atleta_id") is not None)
        return out
    return []


def indice_jogos_por_clube(partidas_payload: dict | None) -> dict[int, dict]:
    """clube_id → status da partida da rodada (decidido = encerrada ou inválida)."""
    out: dict[int, dict] = {}
    if not partidas_payload:
        return out
    for p in partidas_payload.get("partidas") or []:
        st = str(p.get("status_transmissao_tr") or "").upper()
        valida = p.get("valida")
        decidido = (valida is False) or (st == "ENCERRADA")
        info = {
            "status": st,
            "valida": valida,
            "decidido": decidido,
            "timestamp": p.get("timestamp"),
            "partida_data": p.get("partida_data"),
        }
        for cid in (p.get("clube_casa_id"), p.get("clube_visitante_id")):
            if cid is not None:
                out[int(cid)] = info
    return out


def _live_atleta(atleta: dict, pontuados: dict) -> dict:
    """Junta escalação com a pontuação ao vivo de /atletas/pontuados (não usa pontos_num velho)."""
    aid = int(atleta.get("atleta_id"))
    tabela = (pontuados or {}).get("atletas") or {}
    pa = tabela.get(str(aid)) or {}
    entrou = pa.get("entrou_em_campo") is True
    if pa.get("pontuacao") is not None:
        pontos = float(pa.get("pontuacao") or 0)
    else:
        pontos = 0.0
    clube = pa.get("clube_id") if pa.get("clube_id") is not None else atleta.get("clube_id")
    pos = pa.get("posicao_id") if pa.get("posicao_id") is not None else atleta.get("posicao_id")
    return {
        "atleta_id": aid,
        "apelido": pa.get("apelido") or atleta.get("apelido") or str(aid),
        "posicao_id": int(pos) if pos is not None else None,
        "clube_id": int(clube) if clube is not None else None,
        "preco_num": float(atleta.get("preco_num") or pa.get("preco_num") or 0),
        "entrou": entrou,
        "pontos": pontos,
    }


def _resumo_sub(atleta: dict, pontos: float | None = None) -> dict:
    out = {
        "atleta_id": atleta["atleta_id"],
        "apelido": atleta.get("apelido"),
        "posicao_id": atleta.get("posicao_id"),
        "pontos": r2(atleta["pontos"] if pontos is None else pontos),
    }
    return out


def _prioridade_ausente(atleta: dict, capitao_id: int | None, jogos: dict[int, dict]) -> tuple:
    """Quem o banco substitui primeiro: jogo mais cedo, depois capitão, preço, nome."""
    jogo = jogos.get(atleta["clube_id"] or -1) or {}
    ts = jogo.get("timestamp")
    try:
        ts_n = int(ts) if ts is not None and ts != "" else 10**18
    except (TypeError, ValueError):
        ts_n = 10**18
    eh_cap = 0 if (capitao_id is not None and atleta["atleta_id"] == capitao_id) else 1
    return (ts_n, eh_cap, -atleta.get("preco_num", 0), (atleta.get("apelido") or "").lower())


def pontos_do_elenco(
    elenco: list,
    reservas: list,
    capitao_id: int | None,
    reserva_luxo_id: int | None,
    pontuados: dict,
    partidas_payload: dict | None,
) -> tuple[float, int, int, list[dict]]:
    """Parcial com banco e reserva de luxo.

    (a) Titular com jogo já decidido (encerrado ou inválido) que não entrou é
        trocado pelo reserva da mesma posição, se este entrou e pontuou > 0.
        Se o titular era o capitão, o reserva herda a braçadeira (1,5×) —
        regra oficial do Cartola (ge, 2025; o banco em 2026 segue a mesma dinâmica).
    (b) Reserva de luxo: quando todos os titulares da posição já têm jogo
        decidido e entraram em campo, e o luxo não foi usado em (a), ele
        substitui o titular de menor pontuação bruta se pontuou mais.
    """
    jogos = indice_jogos_por_clube(partidas_payload)
    cap = int(capitao_id) if capitao_id is not None else None
    luxo_id = int(reserva_luxo_id) if reserva_luxo_id is not None else None
    titulares = [_live_atleta(a, pontuados) for a in (elenco or []) if isinstance(a, dict) and a.get("atleta_id") is not None]
    banco = [_live_atleta(a, pontuados) for a in (reservas or [])]
    banco_por_pos: dict[int, dict] = {}
    for r in banco:
        if r.get("posicao_id") is not None:
            banco_por_pos[int(r["posicao_id"])] = r

    # Contribuição inicial dos titulares que entraram.
    contrib: dict[int, float] = {}
    capitao_efetivo = cap
    for t in titulares:
        if not t["entrou"]:
            continue
        p = t["pontos"]
        if cap is not None and t["atleta_id"] == cap:
            p *= CAPITAO_FATOR
        contrib[t["atleta_id"]] = p

    substituicoes: list[dict] = []
    luxo_usado = False

    # (a) Reserva comum, uma vaga por posição.
    ausentes_por_pos: dict[int, list[dict]] = {}
    for t in titulares:
        pos = t.get("posicao_id")
        if pos is None or pos == 6:
            continue
        jogo = jogos.get(t["clube_id"] or -1)
        if not jogo or not jogo.get("decidido"):
            continue
        if t["entrou"]:
            continue
        ausentes_por_pos.setdefault(int(pos), []).append(t)

    for pos, ausentes in ausentes_por_pos.items():
        reserva = banco_por_pos.get(pos)
        if not reserva or not reserva["entrou"] or reserva["pontos"] <= 0:
            continue
        ausentes.sort(key=lambda a: _prioridade_ausente(a, cap, jogos))
        sai = ausentes[0]
        herda_cap = cap is not None and sai["atleta_id"] == cap
        pts_entra = reserva["pontos"] * (CAPITAO_FATOR if herda_cap else 1.0)
        contrib.pop(sai["atleta_id"], None)
        contrib[reserva["atleta_id"]] = pts_entra
        if herda_cap:
            capitao_efetivo = reserva["atleta_id"]
        if luxo_id is not None and reserva["atleta_id"] == luxo_id:
            luxo_usado = True
        substituicoes.append({
            "tipo": "reserva",
            "sai": _resumo_sub(sai, 0.0),
            "entra": _resumo_sub(reserva, pts_entra),
            "capitao_herdado": herda_cap,
        })

    # (b) Reserva de luxo, se ainda não entrou por (a).
    luxo = next((r for r in banco if luxo_id is not None and r["atleta_id"] == luxo_id), None)
    if luxo and not luxo_usado and luxo["entrou"] and luxo.get("posicao_id") not in (None, 6):
        pos = int(luxo["posicao_id"])
        mesmos = [t for t in titulares if t.get("posicao_id") == pos]
        ids_ja_trocados = {s["sai"]["atleta_id"] for s in substituicoes}
        efetivos = [t for t in mesmos if t["atleta_id"] not in ids_ja_trocados]
        todos_decididos = all(
            (jogos.get(t["clube_id"] or -1) or {}).get("decidido") for t in efetivos
        ) and all((jogos.get(t["clube_id"] or -1) or {}).get("decidido") for t in mesmos)
        todos_jogaram = all(t["entrou"] for t in efetivos)
        if mesmos and todos_decididos and todos_jogaram:
            # Desempate no menor scout: capitão sai primeiro; depois menor preço, nome.
            def chave_luxo(t: dict) -> tuple:
                eh_cap = 0 if (cap is not None and t["atleta_id"] == cap) else 1
                return (t["pontos"], eh_cap, t.get("preco_num") or 0, (t.get("apelido") or "").lower())
            pior = min(efetivos, key=chave_luxo)
            if luxo["pontos"] > pior["pontos"]:
                # Se o luxo tira o capitão que já jogou, a braçadeira não transfere
                # (o capitão atuou; o 1,5× sai com ele).
                contrib.pop(pior["atleta_id"], None)
                contrib[luxo["atleta_id"]] = luxo["pontos"]
                substituicoes.append({
                    "tipo": "luxo",
                    "sai": _resumo_sub(pior),
                    "entra": _resumo_sub(luxo),
                    "capitao_herdado": False,
                })

    total = sum(contrib.values())
    jogados = len(contrib)
    n = len(titulares) or 12
    return total, jogados, n, substituicoes




def _times_parciais_base(teams: list[dict], oficial: list[dict]) -> list[dict]:
    by_id = {row["time_id"]: row for row in oficial}
    rows = []
    for t in teams:
        of = by_id.get(t["time_id"]) or {}
        rows.append({
            "time_id": t["time_id"],
            "nome": t["nome"],
            "dono": t["dono"],
            "escudo": of.get("escudo") or t.get("escudo"),
            "pontos_parcial": 0.0,
            "atletas_jogados": 0,
            "atletas_total": 12,
            "total_geral_oficial": of.get("pontos") if of.get("pontos") is not None else 0.0,
            "total_geral_parcial": of.get("pontos") if of.get("pontos") is not None else 0.0,
            "posicao_oficial": of.get("posicao"),
            "patrimonio": of.get("patrimonio"),
        })
    return rows


def _fechar_parciais(rows: list[dict]) -> list[dict]:
    por_rodada = sorted(rows, key=lambda x: (-(x["pontos_parcial"] or 0), -(x.get("patrimonio") or 0), x["nome"].lower()))
    for i, row in enumerate(por_rodada, 1):
        row["posicao_rodada"] = i
    por_geral = sorted(rows, key=lambda x: (-(x["total_geral_parcial"] or 0), -(x.get("patrimonio") or 0), x["nome"].lower()))
    out = []
    for i, row in enumerate(por_geral, 1):
        oficial = row.get("posicao_oficial")
        variacao = (oficial - i) if isinstance(oficial, int) else 0
        item = {
            "time_id": row["time_id"],
            "nome": row["nome"],
            "dono": row["dono"],
            "escudo": row.get("escudo"),
            "pontos_parcial": r2(row["pontos_parcial"]) if row["pontos_parcial"] is not None else 0.0,
            "atletas_jogados": row["atletas_jogados"],
            "atletas_total": row["atletas_total"],
            "total_geral_oficial": r2(row["total_geral_oficial"]) if row["total_geral_oficial"] is not None else 0.0,
            "total_geral_parcial": r2(row["total_geral_parcial"]) if row["total_geral_parcial"] is not None else 0.0,
            "posicao_oficial": oficial,
            "posicao_parcial": i,
            "posicao_rodada": row["posicao_rodada"],
            "variacao": variacao,
            "zona_premiacao": i <= ZONA_PREMIACAO,
        }
        if row.get("substituicoes"):
            item["substituicoes"] = row["substituicoes"]
        out.append(item)
    return out


NOTA_PARCIAIS = None


def compute_parciais(
    teams: list[dict],
    slim: dict,
    last: int,
    rodada_atual: int,
    mercado: dict,
    allow_fetch: bool,
) -> dict:
    """Monta data/parciais.json. Não altera classificação oficial, medalhas nem copas."""
    oficial = classify(teams, slim, last) if last else []
    status_id = mercado.get("status_mercado")
    em_andamento = (
        last < (rodada_atual or 0)
        and status_id in (2, 3)
        and not mercado.get("game_over")
    )
    base_meta = {
        "rodada": rodada_atual or last,
        "status_mercado": status_id,
        "status_mercado_nome": STATUS_MERCADO.get(status_id, "desconhecido"),
        "bola_rolando": bool(mercado.get("bola_rolando")),
        "em_andamento": bool(em_andamento),
        "atualizado_em": now_sp(),
        "intervalo_atualizacao_minutos": 10,
        "capitao_fator": CAPITAO_FATOR,
        "ultima_rodada_encerrada": last,
        "nota": NOTA_PARCIAIS,
        "atletas_pontuados": 0,
        "jogos": resumo_jogos(None),
        "texto_jogos": "",
        "mensagem": None,
        "times": [],
    }

    if not em_andamento:
        rod = last or rodada_atual
        rows = _times_parciais_base(teams, oficial)
        for row in rows:
            p = points_of(slim, row["time_id"], rod) if rod else None
            row["pontos_parcial"] = p if p is not None else 0.0
            rec = slim.get("times", {}).get(str(row["time_id"]), {}).get(str(rod), {}) or {}
            row["atletas_total"] = rec.get("n_atletas") or 12
            row["atletas_jogados"] = rec.get("n_atletas_com_pontos") or 0
            row["total_geral_parcial"] = row["total_geral_oficial"]
        if mercado.get("game_over"):
            msg = "A temporada encerrou. Abaixo, o resultado da última rodada."
        elif status_id == 1:
            msg = (
                f"Não há rodada em andamento (mercado aberto). "
                f"Abaixo, o resultado da rodada {rod}."
            )
        else:
            msg = f"Não há rodada em andamento. Abaixo, o resultado da rodada {rod}."
        base_meta.update({
            "rodada": rod,
            "mensagem": msg,
            "times": _fechar_parciais(rows),
        })
        return base_meta

    pontuados = None
    partidas = None
    if allow_fetch:
        pontuados = http_get_optional(f"{API_BASE}/atletas/pontuados")
        partidas = http_get_optional(f"{API_BASE}/partidas/{rodada_atual}")
    jogos = resumo_jogos(partidas)
    atletas_n = 0
    if pontuados:
        atletas_n = int(pontuados.get("total_atletas") or len(pontuados.get("atletas") or {}))
        if pontuados.get("rodada") and int(pontuados["rodada"]) != int(rodada_atual):
            # Pontuados de outra rodada: não usa.
            pontuados = None
            atletas_n = 0

    rows = _times_parciais_base(teams, oficial)
    elencos_ok = 0
    if allow_fetch:
        for i, row in enumerate(rows):
            time.sleep(FETCH_DELAY)
            try:
                payload = http_get(f"{API_BASE}/time/id/{row['time_id']}/{rodada_atual}")
            except Exception as exc:  # noqa: BLE001
                print(f"aviso: elenco {row['nome']} R{rodada_atual}: {exc}", file=sys.stderr)
                continue
            if not payload_ok(payload, row["time_id"]):
                continue
            elenco = payload.get("atletas") or []
            cap = payload.get("capitao_id")
            pts, jogados, n, subs = pontos_do_elenco(
                elenco,
                lista_reservas(payload),
                cap,
                payload.get("reserva_luxo_id"),
                pontuados or {},
                partidas,
            )
            row["pontos_parcial"] = pts
            row["atletas_jogados"] = jogados
            row["atletas_total"] = n or 12
            if subs:
                row["substituicoes"] = subs
            escudo = (payload.get("time") or {}).get("url_escudo_png")
            if escudo:
                row["escudo"] = escudo
            row["total_geral_parcial"] = (row["total_geral_oficial"] or 0) + pts
            elencos_ok += 1
            if (i + 1) % 35 == 0 or (i + 1) == len(rows):
                print(f"  parciais elencos {i+1}/{len(rows)}")
    else:
        base_meta["mensagem"] = (
            f"A rodada {rodada_atual} está em andamento, mas esta execução não consultou a API. "
            "As parciais não foram atualizadas."
        )

    if allow_fetch and elencos_ok == 0:
        base_meta["mensagem"] = (
            f"A rodada {rodada_atual} está em andamento, mas não foi possível obter as escalações."
        )

    msg = base_meta.get("mensagem")
    if not msg:
        if jogos.get("em_andamento") or jogos.get("encerrados"):
            msg = f"Rodada {rodada_atual} em andamento."
        elif status_id == 2:
            msg = f"Mercado fechado. Os jogos da rodada {rodada_atual} ainda não começaram ou as parciais ainda não saíram."
        else:
            msg = f"Rodada {rodada_atual} em atualização."

    base_meta.update({
        "atletas_pontuados": atletas_n,
        "jogos": jogos,
        "texto_jogos": texto_jogos(jogos),
        "mensagem": msg,
        "times": _fechar_parciais(rows),
    })
    return base_meta



def now_sp_dt() -> datetime:
    return datetime.now(TZ_SP)


def parse_partida_inicio(partida: dict) -> datetime | None:
    """Início do jogo em horário de Brasília, a partir de timestamp ou partida_data."""
    ts = partida.get("timestamp")
    if ts is not None and ts != "":
        try:
            return datetime.fromtimestamp(int(ts), TZ_SP)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    raw = partida.get("partida_data")
    if not raw:
        return None
    text = str(raw).strip().replace("T", " ")
    # descarta fuso no fim, se vier
    if text.endswith("Z"):
        text = text[:-1].strip()
    if "+" in text[10:] or text[10:].count("-") >= 1:
        text = text[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=TZ_SP)
        except ValueError:
            continue
    return None


def partida_entra_na_janela(partida: dict) -> bool:
    """Jogos inválidos ficam de fora, salvo se já estiverem em andamento ou encerrados."""
    st = str(partida.get("status_transmissao_tr") or "").upper()
    if partida.get("valida") is False:
        return st in {"EM_ANDAMENTO", "ENCERRADA"}
    return True


def fmt_sp(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.astimezone(TZ_SP).strftime("%Y-%m-%d %H:%M")


def inspecionar_janelas(partidas_payload: dict | None, agora: datetime, bola_rolando: bool = False) -> dict:
    """Define se agora cai na janela de algum jogo da rodada (início até início+2h30)."""
    partidas = (partidas_payload or {}).get("partidas") or []
    ativos: list[dict] = []
    futuras: list[dict] = []
    em_andamento = False
    for p in partidas:
        if not partida_entra_na_janela(p):
            continue
        inicio = parse_partida_inicio(p)
        if inicio is None:
            continue
        fim = inicio + JANELA_APOS_INICIO
        st = str(p.get("status_transmissao_tr") or "").upper()
        item = {"inicio": inicio, "fim": fim, "status": st, "partida": p}
        if st == "EM_ANDAMENTO":
            em_andamento = True
            ativos.append(item)
        elif inicio <= agora <= fim:
            ativos.append(item)
        elif agora < inicio:
            futuras.append(item)
    futuras.sort(key=lambda x: x["inicio"])
    em_janela = bool(ativos) or bool(bola_rolando and not partidas)
    # bola_rolando vale como janela mesmo se o horário já passou de início+2h30
    if bola_rolando and not ativos:
        em_janela = True
    return {
        "em_janela": em_janela,
        "ativos": ativos,
        "futuras": futuras,
        "em_andamento": em_andamento or bool(bola_rolando),
        "bola_rolando": bool(bola_rolando),
    }


def ultima_encerrada_na_api(status: dict) -> int:
    """Melhor estimativa barata da última rodada oficial já fechada na API."""
    rodada = int(status.get("rodada_atual") or 0)
    st = status.get("status_mercado")
    if status.get("game_over"):
        return int(status.get("rodada_final") or rodada or 0)
    if st == 1:
        # Mercado aberto: a rodada_atual é a próxima; a anterior já foi consolidada.
        return max(0, rodada - 1)
    if st == 3 or status.get("mercado_pos_rodada"):
        # Scouts oficiais da rodada_atual estão sendo (ou acabam de ser) fechados.
        return rodada
    return max(0, rodada - 1)


def ultima_encerrada_no_site() -> int:
    for path in (PATHS["site_data"] / "meta.json", PATHS["site_data"] / "site.json"):
        if not path.exists():
            continue
        try:
            data = load_json(path)
        except Exception:  # noqa: BLE001
            continue
        if "ultima_rodada_encerrada" in data:
            return int(data.get("ultima_rodada_encerrada") or 0)
        meta = data.get("meta") or {}
        if "ultima_rodada_encerrada" in meta:
            return int(meta.get("ultima_rodada_encerrada") or 0)
    slim = load_cached_slim()
    return int((slim.get("mercado") or {}).get("rodada_atual") or 0) - 1 if slim.get("mercado") else 0


def precisa_consultar_partidas(status: dict, last_api: int, last_site: int) -> bool:
    if last_api > last_site:
        return False
    if status.get("bola_rolando"):
        return True
    return status.get("status_mercado") in (2, 3, 4)


def descrever_decisao(decisao: dict) -> str:
    partes = [
        f"modo={decisao['modo']}",
        f"rodada={decisao.get('rodada')}",
        f"site=R{decisao.get('last_site')}",
        f"api_encerrada=R{decisao.get('last_api')}",
        "em_janela" if decisao.get("em_janela") else "fora_da_janela",
    ]
    if decisao.get("jogos"):
        partes.append("jogos=[" + "; ".join(decisao["jogos"]) + "]")
    if decisao.get("proxima"):
        partes.append("próxima=" + decisao["proxima"])
    partes.append(decisao.get("motivo") or "")
    return "gate: " + " · ".join(p for p in partes if p)


def _rotulo_jogo(p: dict, clubes: dict | None = None) -> str:
    clubes = clubes or {}
    def nome(cid):
        c = clubes.get(str(cid)) or clubes.get(cid) or {}
        return c.get("abreviacao") or c.get("nome") or str(cid)
    return f"{nome(p.get('clube_casa_id'))} x {nome(p.get('clube_visitante_id'))}"


def avaliar_gate(
    status: dict,
    partidas_payload: dict | None,
    last_site: int,
    agora: datetime | None = None,
    force: bool = False,
) -> dict:
    """Decide se esta execução deve atualizar parciais, consolidar a rodada ou não fazer nada."""
    agora = agora or now_sp_dt()
    rodada = int(status.get("rodada_atual") or 0)
    last_api = ultima_encerrada_na_api(status)
    clubes = (partidas_payload or {}).get("clubes") or {}
    info = inspecionar_janelas(partidas_payload, agora, bola_rolando=bool(status.get("bola_rolando")))
    jogos = []
    for item in info["ativos"]:
        p = item["partida"]
        fim = fmt_sp(item["fim"])
        extra = "em andamento" if item["status"] == "EM_ANDAMENTO" else f"até {fim}"
        jogos.append(f"{_rotulo_jogo(p, clubes)} ({extra})")
    proxima = ""
    if info["futuras"]:
        nxt = info["futuras"][0]
        proxima = f"{_rotulo_jogo(nxt['partida'], clubes)} às {fmt_sp(nxt['inicio'])}"

    base = {
        "rodada": rodada,
        "last_site": last_site,
        "last_api": last_api,
        "em_janela": bool(info["em_janela"]),
        "jogos": jogos,
        "proxima": proxima,
        "agora": fmt_sp(agora),
    }
    if force:
        return {**base, "modo": "completo", "motivo": "disparo manual; ignora o gate"}
    if last_api > last_site:
        return {
            **base,
            "modo": "consolidar",
            "motivo": f"a API já consolidou a rodada {last_api} e o site ainda está na {last_site}",
        }
    if info["em_janela"]:
        motivo = "jogo em andamento" if info["em_andamento"] or status.get("bola_rolando") else "dentro da janela de um jogo (início + 2h30)"
        return {**base, "modo": "parciais", "motivo": motivo}
    return {
        **base,
        "modo": "skip",
        "motivo": "fora da janela de jogos; nada a publicar até o próximo jogo ou a consolidação oficial",
    }


def consultar_gate(force: bool = False, agora: datetime | None = None) -> dict:
    """Consulta só /mercado/status e, se preciso, /partidas. Não grava arquivo."""
    status = fetch_status()
    last_site = ultima_encerrada_no_site()
    last_api = ultima_encerrada_na_api(status)
    partidas = None
    if force or precisa_consultar_partidas(status, last_api, last_site):
        rodada = int(status.get("rodada_atual") or 0)
        if rodada:
            partidas = http_get_optional(f"{API_BASE}/partidas/{rodada}")
    return avaliar_gate(status, partidas, last_site, agora=agora, force=force)


def atualizar_so_parciais(status: dict | None = None) -> None:
    """Recalcula só data/parciais.json, sem mexer na classificação oficial."""
    # offline=True evita o fetch_into completo (também no GitHub Actions).
    teams, _grupos, slim, mercado = load_inputs(fetch=False, offline=True)
    if status:
        mercado = {
            **mercado,
            "rodada_atual": status.get("rodada_atual", mercado.get("rodada_atual")),
            "status_mercado": status.get("status_mercado", mercado.get("status_mercado")),
            "temporada": status.get("temporada", mercado.get("temporada")),
            "bola_rolando": status.get("bola_rolando", mercado.get("bola_rolando")),
            "mercado_pos_rodada": status.get("mercado_pos_rodada", mercado.get("mercado_pos_rodada")),
            "game_over": status.get("game_over", mercado.get("game_over")),
            "nome_rodada": status.get("nome_rodada", mercado.get("nome_rodada")),
            "rodada_final": status.get("rodada_final", mercado.get("rodada_final")),
        }
        slim = dict(slim)
        slim["mercado"] = mercado
    rodada_atual, last, _parcial = scored_rounds(slim, teams, mercado)
    parciais = compute_parciais(
        teams, slim, last, rodada_atual, mercado, allow_fetch=True
    )
    dest = OUT / "parciais.json"
    OUT.mkdir(parents=True, exist_ok=True)
    dump_json(dest, parciais)
    print(f"atualizado somente {dest}  (parciais R{parciais.get('rodada')}, em_andamento={parciais.get('em_andamento')})")
    if parciais.get("times"):
        por_rod = sorted(parciais["times"], key=lambda x: x.get("posicao_rodada") or 99)
        print(
            "parciais:",
            ", ".join(f"{t['posicao_rodada']}.{t['nome']} {t['pontos_parcial']}" for t in por_rod[:5]),
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atualiza os JSONs da Caneleiros Ligui.")
    p.add_argument("--fetch", action="store_true", help="consulta a API (padrão no GitHub Action)")
    p.add_argument("--offline", action="store_true", help="não chama a API; usa só o cache")
    p.add_argument("--fetch-all", action="store_true", help="baixa de novo todas as rodadas")
    p.add_argument("--force", action="store_true", help="ignora o gate e atualiza tudo")
    p.add_argument("--gate", action="store_true", help="só consulta o gate (status/partidas) e encerra")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    offline = bool(args.offline)
    fetch = bool(args.fetch or args.fetch_all or os.environ.get("GITHUB_ACTIONS") == "true")
    if offline:
        fetch = False
    # Local com cache completo: não precisa ir à API, a menos que peçam --fetch.
    if not fetch and not offline and not load_cached_slim().get("times") and not (RAW / "slim.json").exists() and not PATHS["historico"].exists():
        fetch = True
    forced = bool(
        getattr(args, "force", False)
        or getattr(args, "fetch_all", False)
        or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    )
    if getattr(args, "gate", False) or (fetch and not offline):
        try:
            decisao = consultar_gate(force=forced)
        except Exception as exc:  # noqa: BLE001
            if getattr(args, "gate", False):
                raise SystemExit(f"gate: falha ao consultar a API ({exc})")
            print(f"gate: falha ao consultar a API ({exc}); seguindo com a execução normal", file=sys.stderr)
            decisao = None
        else:
            print(descrever_decisao(decisao))
            if getattr(args, "gate", False):
                return
            if not forced:
                if decisao["modo"] == "skip":
                    print("nenhum arquivo alterado")
                    return
                if decisao["modo"] == "parciais":
                    try:
                        status_agora = fetch_status()
                    except Exception:  # noqa: BLE001
                        status_agora = None
                    atualizar_so_parciais(status=status_agora)
                    return
    teams, grupos, slim, mercado = load_inputs(fetch=fetch, offline=offline, force_all=bool(args.fetch_all))
    rodada_atual, last, parcial = scored_rounds(slim, teams, mercado)
    rodada_final = int(mercado.get("rodada_final") or 38)
    # Medalhas, recorde e copas usam só rodadas encerradas (copas vão até a 36).
    # Classificação: se a rodada atual já tem pontos parciais, soma esses pontos e marca parcial.
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

    parciais = None
    try:
        parciais = compute_parciais(
            teams, slim, last, rodada_atual, mercado, allow_fetch=bool(fetch and not offline)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: parciais não geradas: {exc}", file=sys.stderr)

    # Histórico slim (pontos/patrimônio por rodada) versionado junto com o site.
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
        "nota_pontos": "O campo 'pontos' da API já inclui o capitão. O campo 'patrimonio' no endpoint /time/id/{id}/{rodada} é histórico daquela rodada. As rodadas 37 e 38 entram na classificação, nas medalhas e no recorde; as copas terminam na 36.",
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
    if parciais is not None:
        # Com a rodada em andamento, grava sempre para o horário de atualização avançar.
        dest = OUT / "parciais.json"
        if parciais.get("em_andamento") or dump_if_changed(dest, parciais):
            if parciais.get("em_andamento"):
                dump_json(dest, parciais)
            changed.append("parciais.json")
    # Histórico slim: só grava de novo se o conteúdo (sem mercado volátil demais) já foi
    # persistido no fetch; aqui regrava para o layout local do site.
    if PATHS["historico"].parent.exists():
        dump_if_changed(PATHS["historico"], slim)
    if not changed:
        print(f"sem mudanças em {OUT}  (encerrada=R{last}, atual=R{rodada_atual}, parcial={parcial})")
    else:
        print(f"atualizado em {OUT} ({', '.join(changed)})  (encerrada=R{last}, atual=R{rodada_atual}, parcial={parcial})")
    print("líder:", classificacao[0]["nome"], classificacao[0]["pontos"])
    print("top5:", ", ".join(f"{r['posicao']}.{r['nome']}" for r in classificacao[:5]))
    print("medalhas:", ", ".join(f"{q['nome']} {q['ouro']}O/{q['prata']}P/{q['bronze']}B" for q in quadro[:5]))
    if maior["vencedores"]:
        v = maior["vencedores"][0]
        print("maior pontuação:", v["pontos"], "R" + str(v["rodada"]), v["nome"])
    if parciais and parciais.get("times"):
        top = parciais["times"]
        por_rod = sorted(top, key=lambda x: x.get("posicao_rodada") or 99)
        print(
            "parciais:",
            "em_andamento" if parciais.get("em_andamento") else "encerrada",
            "R" + str(parciais.get("rodada")),
            ", ".join(f"{t['posicao_rodada']}.{t['nome']} {t['pontos_parcial']}" for t in por_rod[:5]),
        )
    for c in copas:
        camp = c["mata_mata"].get("campeao")
        print(f"{c['nome']} fase={c['fase']} campeao={camp['nome'] if camp else '-'}")


if __name__ == "__main__":
    main()
