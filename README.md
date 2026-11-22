# Caneleiros Ligui 2026

Site estático da liga no Cartola FC. Endereço previsto: https://caneleirosligui.github.io/

O front lê `data/site.json` no navegador. Não há etapa de build.

## Atualização dos dados

A cada rodada, as pontuações vêm da API não oficial do Cartola (`api.cartola.globo.com`). A lista dos 35 times é fixa (`data/teams.json`); não há endpoint público da liga.

O GitHub Action `.github/workflows/atualizar.yml`:

- Roda `python scripts/compute.py --fetch` (Python 3 + `requests`, ver `scripts/requirements.txt`).
- Agenda: a cada 30 minutos de sábado a quarta; uma vez por dia na quinta e na sexta; também pode ser disparado manualmente (`workflow_dispatch`).
- Só faz commit em `data/` se algum JSON tiver mudado (o script não regrava arquivo cujo conteúdo relevante é o mesmo).
- Se a API estiver fora do ar ou devolver dados vazios, o script mantém o cache (`data/historico.json`) e não sobrescreve as saídas com resultado vazio.

As rodadas 37 e 38 entram na classificação geral, no quadro de medalhas e no recorde. As quatro copas terminam na rodada 36.

Ajustes manuais opcionais (por exemplo, medalhas de uma rodada com empate) ficam em `data/ajustes.json`, com `ativo: false` por padrão. Liga o ajuste trocando para `true`; não é preciso alterar o código.

## Publicação

Este diretório é o conteúdo do repositório `caneleirosligui/caneleirosligui.github.io`, branch `main`. O GitHub Pages deve servir a raiz do repositório.
