/* Caneleiros Ligui 2026 — front estático, lê data/site.json */
(function () {
  "use strict";

  const ESTADO = { dados: null, rodadaSel: null };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function iniciais(nome) {
    const stop = new Set(["de", "da", "do", "dos", "das", "e", "the"]);
    const parts = String(nome || "")
      .replace(/[^\p{L}\p{N}\s.]/gu, " ")
      .trim()
      .split(/\s+/)
      .filter((w) => w && !stop.has(w.toLowerCase()) && !/^\d+$/.test(w));
    if (!parts.length) return "CL";
    const token = (s) => s.replace(/\./g, "");
    if (parts.length === 1) return token(parts[0]).slice(0, 2).toUpperCase();
    return (token(parts[0])[0] + token(parts[1])[0]).toUpperCase();
  }

  function escudo(url, nome) {
    const ini = esc(iniciais(nome));
    if (!url) {
      return `<span class="escudo-wrap"><span class="escudo-fb" aria-hidden="true">${ini}</span></span>`;
    }
    return `<span class="escudo-wrap"><img class="escudo" src="${esc(url)}" alt="" width="36" height="36" onerror="this.style.display='none';var f=this.nextElementSibling;if(f)f.hidden=false"><span class="escudo-fb" hidden aria-hidden="true">${ini}</span></span>`;
  }

  function fmtPts(n) {
    if (n === null || n === undefined || n === "") return "—";
    return Number(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtRs(n) {
    return Number(n).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function fmtQuando(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const fmt = new Intl.DateTimeFormat("pt-BR", {
      timeZone: "America/Sao_Paulo",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = Object.fromEntries(fmt.formatToParts(d).map((p) => [p.type, p.value]));
    return `${parts.day}/${parts.month}/${parts.year} às ${parts.hour}:${parts.minute}`;
  }

  function fmtIntervaloRodadas(arr) {
    if (!arr || !arr.length) return "—";
    if (arr.length === 1) return String(arr[0]);
    return arr[0] + " a " + arr[arr.length - 1];
  }

  function escudosPorTime(dados) {
    const map = {};
    (dados.classificacao || []).forEach((t) => { if (t.time_id) map[t.time_id] = t.escudo; });
    return map;
  }

  function blocoTime(t, escudos) {
    const url = t.escudo || (escudos && escudos[t.time_id]) || "";
    return `${escudo(url, t.nome)}<span><span class="time-nome">${esc(t.nome)}</span>${t.dono ? `<span class="time-dono"><br>${esc(t.dono)}</span>` : ""}</span>`;
  }

  function parseHash() {
    const h = (location.hash || "#inicio").replace(/^#/, "").toLowerCase();
    if (!h || h === "inicio" || h === "home") return { view: "inicio" };
    if (h === "medalhas") return { view: "medalhas" };
    const m = h.match(/^copa-([1-4])$/);
    if (m) return { view: "copa", n: Number(m[1]) };
    return { view: "inicio" };
  }

  function setNav(rota) {
    document.querySelectorAll(".nav > a").forEach((a) => {
      const on = (rota.view === "inicio" && a.dataset.nav === "inicio") ||
        (rota.view === "medalhas" && a.dataset.nav === "medalhas");
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const wrap = document.querySelector(".nav-copas");
    wrap.dataset.ativa = rota.view === "copa" ? "true" : "false";
    document.querySelectorAll("#menu-copas a").forEach((a) => {
      const n = Number(a.dataset.copa);
      if (rota.view === "copa" && rota.n === n) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function fecharMenu() {
    const menu = $("menu-copas");
    const btn = $("btn-copas");
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  }

  function abrirMenu() {
    const menu = $("menu-copas");
    const btn = $("btn-copas");
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  }

  function renderMeta(dados) {
    const m = dados.meta || {};
    const last = m.ultima_rodada_encerrada;
    const atual = m.rodada_atual;
    const parcial = m.rodada_parcial;
    let rodada;
    if (parcial) rodada = `Parcial da rodada ${parcial}`;
    else if (last && atual && last !== atual) rodada = `Até a rodada ${last} · rodada atual ${atual}`;
    else rodada = `Rodada ${last || atual || "—"}`;
    const status = m.status_mercado_nome ? ` · mercado ${m.status_mercado_nome.replace(/_/g, " ")}` : "";
    const quando = m.atualizado_em ? ` · atualizado em ${fmtQuando(m.atualizado_em)}` : "";
    $("meta-linha").textContent = rodada + status + quando;
  }

  function renderInicio(dados) {
    const escudos = escudosPorTime(dados);
    const tabela = dados.classificacao || [];
    const rec = dados.maior_pontuacao || {};
    const v = (rec.vencedores || [])[0];
    let recorde = "<p>Ainda não há pontuações.</p>";
    if (v) {
      recorde = `
        <div class="recorde card">
          <p class="rotulo">Maior pontuação de uma rodada</p>
          <div class="linha-time">
            ${blocoTime(v, escudos)}
            <div class="pts">${fmtPts(v.pontos)}</div>
          </div>
          <p class="rodada">Rodada ${esc(v.rodada)}${rec.empate ? " · empate no recorde" : ""}</p>
        </div>`;
    }

    const linhas = tabela.map((t) => {
      const zona = t.zona_premiacao ? " zona" : "";
      const premio = t.premio ? `<span class="premio-pos">${fmtRs(t.premio)}</span>` : "";
      return `<li class="${zona.trim()}">
        <span class="pos">${t.posicao}º</span>
        ${escudo(t.escudo, t.nome)}
        <span><span class="time-nome">${esc(t.nome)}</span><span class="time-dono"><br>${esc(t.dono || "")}</span>${premio}</span>
        <span class="pts-cell">${fmtPts(t.pontos)}</span>
      </li>`;
    }).join("");

    const pr = dados.premiacao || {};
    const itens = (pr.itens || []).map((it) => {
      const quem = (it.ganhadores && it.ganhadores.length)
        ? it.ganhadores.map((g) => {
            const extra = g.rodada ? ` · rodada ${g.rodada} (${fmtPts(g.pontos)})` : "";
            return `${esc(g.nome)}${g.dono ? " (" + esc(g.dono) + ")" : ""}${extra}`;
          }).join("; ")
        : "A definir";
      let tag = "";
      if (it.situacao === "campeão") tag = `<span class="tag tag-campeao">campeão</span>`;
      else if (it.situacao === "líder no momento") tag = `<span class="tag tag-lider">líder no momento</span>`;
      return `<li>
        <span>${esc(it.label)}${tag}</span>
        <span class="valor">${fmtRs(it.valor)}</span>
        <span class="quem">${quem}</span>
      </li>`;
    }).join("");

    $("view-inicio").innerHTML = `
      <h2>Classificação geral</h2>
      ${dados.classificacao_parcial || (dados.meta && dados.meta.rodada_parcial) ? `<p class="fase-copa">Inclui pontos parciais da rodada ${esc(dados.meta.rodada_parcial)}.</p>` : ""}
      ${recorde}
      <ol class="lista-classif card">${linhas}</ol>
      <h2>Premiação</h2>
      <div class="card">
        <p class="arrecada">Total arrecadado: 35 × ${fmtRs(pr.taxa || 100)} = <strong>${fmtRs(pr.arrecadado || 3500)}</strong></p>
        <ul class="lista-premios">${itens}</ul>
      </div>`;
  }

  function renderMedalhas(dados) {
    const escudos = escudosPorTime(dados);
    const med = dados.medalhas || {};
    const quadro = med.quadro || [];
    const last = (dados.meta && dados.meta.ultima_rodada_encerrada) || 1;
    if (ESTADO.rodadaSel == null) ESTADO.rodadaSel = last;
    if (ESTADO.rodadaSel > last) ESTADO.rodadaSel = last;
    if (ESTADO.rodadaSel < 1) ESTADO.rodadaSel = 1;

    const rows = quadro.map((t) => {
      const total = (t.ouro || 0) + (t.prata || 0) + (t.bronze || 0);
      return `<tr>
        <td class="time"><div class="time-cell">${escudo(escudos[t.time_id], t.nome)}<span><span class="time-nome">${esc(t.nome)}</span><span class="time-dono"><br>${esc(t.dono || "")}</span></span></div></td>
        <td class="med-o">${t.ouro}</td>
        <td class="med-p">${t.prata}</td>
        <td class="med-b">${t.bronze}</td>
        <td>${total}</td>
      </tr>`;
    }).join("");

    const opts = [];
    for (let r = last; r >= 1; r--) opts.push(`<option value="${r}"${r === ESTADO.rodadaSel ? " selected" : ""}>Rodada ${r}</option>`);

    $("view-medalhas").innerHTML = `
      <h2>Quadro de medalhas</h2>
      <div class="card scroll-x">
        <table class="quadro">
          <thead>
            <tr>
              <th class="time">Time</th>
              <th class="med-o">Ouro</th>
              <th class="med-p">Prata</th>
              <th class="med-b">Bronze</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <h2>Medalhistas da rodada</h2>
      <div class="seletor-rodada">
        <button type="button" id="rod-ant" aria-label="Rodada anterior">‹</button>
        <label class="sr-only" for="sel-rodada">Escolher rodada</label>
        <select id="sel-rodada" aria-label="Rodada">${opts.join("")}</select>
        <button type="button" id="rod-prox" aria-label="Próxima rodada">›</button>
      </div>
      <div id="medalhistas"></div>`;

    renderMedalhistas(dados, ESTADO.rodadaSel, escudos);
    $("sel-rodada").addEventListener("change", (e) => {
      ESTADO.rodadaSel = Number(e.target.value);
      renderMedalhistas(dados, ESTADO.rodadaSel, escudos);
    });
    $("rod-ant").addEventListener("click", () => trocarRodada(dados, escudos, -1, last));
    $("rod-prox").addEventListener("click", () => trocarRodada(dados, escudos, 1, last));
  }

  function trocarRodada(dados, escudos, delta, last) {
    const n = Math.min(last, Math.max(1, ESTADO.rodadaSel + delta));
    ESTADO.rodadaSel = n;
    const sel = $("sel-rodada");
    if (sel) sel.value = String(n);
    renderMedalhistas(dados, n, escudos);
  }

  function renderMedalhistas(dados, rodada, escudos) {
    const bloco = (dados.medalhas && dados.medalhas.por_rodada && dados.medalhas.por_rodada[String(rodada)]) || {};
    const faixas = [
      ["ouro", "Ouro"],
      ["prata", "Prata"],
      ["bronze", "Bronze"],
    ].map(([k, titulo]) => {
      const lista = bloco[k] || [];
      if (!lista.length) {
        return `<div class="faixa-medalha ${k}"><h4>${titulo}</h4><p>Nenhum medalhista.</p></div>`;
      }
      const itens = lista.map((t) =>
        `<div class="item-med">${blocoTime(t, escudos)}<span class="pts-cell">${fmtPts(t.pontos)}</span></div>`
      ).join("");
      return `<div class="faixa-medalha ${k}"><h4>${titulo}${lista.length > 1 ? " · empate" : ""}</h4>${itens}</div>`;
    }).join("");
    const dest = $("medalhistas");
    if (dest) dest.innerHTML = `<div class="medalhistas">${faixas}</div>`;
  }

  function rotuloFase(copa) {
    const map = {
      nao_iniciada: "Não iniciada",
      grupos: "Fase de grupos",
      oitavas: "Oitavas de final",
      quartas: "Quartas de final",
      semifinal: "Semifinal",
      final: "Final",
      encerrada: "Encerrada",
    };
    return map[copa.fase] || copa.fase;
  }

  function renderCopa(dados, n) {
    const escudos = escudosPorTime(dados);
    const copa = (dados.copas || []).find((c) => c.id === n);
    if (!copa) {
      $("view-copa").innerHTML = `<p>Copa ${n} não encontrada.</p>`;
      return;
    }
    const camp = copa.mata_mata && copa.mata_mata.campeao;
    const campeaoHtml = camp
      ? `<div class="campeao-copa">${blocoTime(camp, escudos)}<span class="tag tag-campeao">campeão</span></div>`
      : "";

    const grupos = ["A", "B", "C", "D"].map((g) => {
      const tab = (copa.grupos && copa.grupos[g]) || [];
      const lis = tab.map((t) => {
        const cl = t.classificado ? " classificado" : "";
        const tag = t.classificado ? `<span class="tag-cl">classificado</span>` : "";
        return `<li class="${cl.trim()}">
          <span class="pos">${t.posicao}º</span>
          <span><span class="time-nome">${esc(t.nome)}</span><span class="time-dono"> · ${esc(t.dono || "")}</span> ${tag}</span>
          <span class="pts-cell">${fmtPts(t.pontos)}</span>
        </li>`;
      }).join("");
      return `<div class="grupo card"><h3>Grupo ${g}</h3><ol>${lis}</ol></div>`;
    }).join("");

    const mm = copa.mata_mata || {};
    const fases = [
      { nome: "Oitavas", rodada: copa.oitavas_rodada, jogos: mm.oitavas || [] },
      { nome: "Quartas", rodada: copa.quartas_rodada, jogos: mm.quartas || [] },
      { nome: "Semifinal", rodada: copa.semi_rodada, jogos: mm.semifinais || [] },
      { nome: "Final", rodada: copa.final_rodada, jogos: mm.final ? [mm.final] : [] },
    ];

    const chave = fases.map((f) => {
      const jogos = f.jogos.map((j, i) => renderJogo(j, i, escudos, f.nome)).join("");
      return `<div class="fase-bloco"><h3>${esc(f.nome)} · rodada ${f.rodada}</h3>${jogos || "<p>A definir.</p>"}</div>`;
    }).join("");

    $("view-copa").innerHTML = `
      <h2>${esc(copa.nome)}</h2>
      <p class="fase-copa">Grupos: rodadas ${esc(fmtIntervaloRodadas(copa.rodadas_grupos))} · ${esc(rotuloFase(copa))}</p>
      ${campeaoHtml}
      <h3>Fase de grupos</h3>
      <div class="grupos">${grupos}</div>
      <h3>Mata-mata</h3>
      <div class="chave">${chave}</div>`;
  }

  function renderJogo(j, idx, escudos, faseNome) {
    const lado = (ladoObj, vencedorId) => {
      if (!ladoObj) {
        return `<div class="lado definir"><span class="time-nome">A definir</span><span class="pts-cell">—</span></div>`;
      }
      const win = vencedorId && ladoObj.time_id === vencedorId && !j.pendente;
      const seed = ladoObj.seed ? ` · ${esc(ladoObj.seed)}` : "";
      return `<div class="lado${win ? " vencedor" : ""}">
        <span>${escudo(escudos[ladoObj.time_id], ladoObj.nome)} <span class="time-nome">${esc(ladoObj.nome)}</span><span class="time-dono">${ladoObj.dono ? " · " + esc(ladoObj.dono) : ""}${seed}</span></span>
        <span class="pts-cell">${j.pendente && (ladoObj.pontos === null || ladoObj.pontos === undefined) ? "—" : fmtPts(ladoObj.pontos)}</span>
      </div>`;
    };
    // layout of lado has escudo inline - the CSS expects grid 1fr auto. Let me use a cleaner structure.
    const lado2 = (ladoObj, vencedorId) => {
      if (!ladoObj) {
        return `<div class="lado definir"><span class="time-nome">A definir</span><span class="pts-cell">—</span></div>`;
      }
      const win = vencedorId && ladoObj.time_id === vencedorId && !j.pendente;
      const seed = ladoObj.seed ? ` · ${esc(ladoObj.seed)}` : "";
      return `<div class="lado${win ? " vencedor" : ""}">
        <span class="lado-time">${escudo(escudos[ladoObj.time_id], ladoObj.nome)}<span><span class="time-nome">${esc(ladoObj.nome)}</span><span class="time-dono"><br>${esc(ladoObj.dono || "")}${seed}</span></span></span>
        <span class="pts-cell">${(j.pendente && (ladoObj.pontos === null || ladoObj.pontos === undefined)) ? "—" : fmtPts(ladoObj.pontos)}</span>
      </div>`;
    };
    const nota = j.empate && j.desempate
      ? `<p class="empate-nota">Empate nos pontos. Desempate: ${j.desempate === "classificacao_geral" ? "classificação geral / cartoletas" : esc(j.desempate)}.</p>`
      : "";
    const meta = j.jogo ? `${faseNome} ${j.jogo}` : faseNome;
    return `<article class="jogo"><div class="jogo-meta">${esc(meta)}</div>${lado2(j.a, j.vencedor_id)}${lado2(j.b, j.vencedor_id)}${nota}</article>`;
  }

  function atualizarMenuCopas(dados) {
    (dados.copas || []).forEach((c) => {
      const a = document.querySelector(`#menu-copas a[data-copa="${c.id}"]`);
      if (!a) return;
      const camp = c.mata_mata && c.mata_mata.campeao;
      const extra = camp ? camp.nome : (c.fase === "nao_iniciada" ? "Não iniciada" : rotuloFase(c));
      a.innerHTML = `${esc(c.nome)}<span class="menu-extra">${esc(extra)}</span>`;
    });
  }

  function mostrar(rota) {
    setNav(rota);
    $("view-inicio").hidden = rota.view !== "inicio";
    $("view-medalhas").hidden = rota.view !== "medalhas";
    $("view-copa").hidden = rota.view !== "copa";
    if (rota.view === "inicio") renderInicio(ESTADO.dados);
    if (rota.view === "medalhas") renderMedalhas(ESTADO.dados);
    if (rota.view === "copa") renderCopa(ESTADO.dados, rota.n);
    fecharMenu();
    window.scrollTo(0, 0);
  }

  function onHash() {
    if (!ESTADO.dados) return;
    mostrar(parseHash());
  }

  function ligarNav() {
    $("btn-copas").addEventListener("click", (e) => {
      e.stopPropagation();
      const aberto = !$("menu-copas").hidden;
      if (aberto) fecharMenu();
      else abrirMenu();
    });
    document.addEventListener("click", (e) => {
      if (!$("menu-copas").hidden && !e.target.closest(".nav-copas")) fecharMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") fecharMenu();
    });
    $("menu-copas").addEventListener("click", (e) => {
      if (e.target.closest("a")) fecharMenu();
    });
    window.addEventListener("hashchange", onHash);
  }

  function falha(msg) {
    const el = $("status-msg");
    el.hidden = false;
    el.classList.add("erro");
    el.textContent = msg;
  }

  async function iniciar() {
    ligarNav();
    try {
      const resp = await fetch("data/site.json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      ESTADO.dados = await resp.json();
    } catch (err) {
      falha("Não foi possível carregar os dados da liga.");
      console.error(err);
      return;
    }
    renderMeta(ESTADO.dados);
    atualizarMenuCopas(ESTADO.dados);
    if (!location.hash) location.replace("#inicio");
    else onHash();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
