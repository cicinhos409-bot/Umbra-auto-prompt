# Prompt Agent

Painel + API + agente autônomo que gera prompts de vídeo para os seus produtos campeões, em intervalos que você configura. Roda na Vercel (Postgres/Neon) ou numa VPS com Docker.

## Publicar na Vercel

1. Importe o repositório na Vercel (framework detectado: FastAPI, entrada `index.py`).
2. **Storage → Neon (Postgres)**: conecte ao projeto. A Vercel cria `DATABASE_URL` sozinha.
3. **Settings → Environment Variables**: `ANTHROPIC_API_KEY`, `API_KEY` (uma senha longa sua) e, opcional, `CRON_SECRET`.
4. Redeploy. Abra o site e entre com a `API_KEY`. Tabelas e os 10 produtos campeões são criados no primeiro acesso.

### Agendamento (importante)
A Vercel não mantém processo ligado: os agentes rodam quando alguém chama `GET/POST /api/cron/tick`. Esse endpoint roda só os agentes que já estão no prazo (intervalo configurado no painel).

- **Plano Pro**: em `vercel.json` troque o cron para `* * * * *` (a cada minuto).
- **Plano Hobby** (cron da Vercel só 1x/dia): use o workflow `.github/workflows/tick.yml` (a cada 5 min, grátis). No GitHub, em *Settings → Secrets → Actions*, crie `TICK_URL` (`https://SEU-APP.vercel.app/api/cron/tick`) e `API_KEY`. Qualquer outro agendador (cron-job.org etc.) também serve: `POST` com header `X-API-Key`.
- Na prática o menor intervalo é o do agendador (5 min no GitHub Actions).

Limites: cada chamada tem até 300 s; imagens ficam no banco (o painel reduz para ≤1600 px antes de enviar, por causa do limite de 4,5 MB da Vercel).

## Alternativa: VPS com Docker

```bash
cp .env.example .env        # edite ANTHROPIC_API_KEY e API_KEY
docker compose up -d --build   # sobe Postgres + app em 127.0.0.1:8000, com agendador interno
```
Para acessar de fora use HTTPS (Caddy/Nginx → `127.0.0.1:8000`).

## Como usar

1. **Produtos**: já vêm 10 campeões das suas 3 contas (`seed.json`). Edite, adicione os outros e **suba 1 a 3 fotos de cada** — a IA olha as imagens ao escrever.
2. **Agentes**: existe um agente "Agente principal" desligado. Ajuste e ligue:
   - intervalo (ex.: 600 s = a cada 10 min), produtos por rodada, prompts por produto;
   - segundos por prompt (ex.: 7);
   - modo **Separados** (3 ideias diferentes de 7 s) ou **Sequência** (3 cenas de um mesmo vídeo = 21 s).
   - Os produtos entram em rodízio (quem está há mais tempo sem prompts vai primeiro) e a IA recebe os últimos prompts do produto para não repetir.
   - `Parar se houver N prompts novos sem uso` evita gastar token enquanto a fila não for consumida.
3. **Prompts**: fila com copiar / usado / descartar.

## Entregar para outros sistemas

Todas as chamadas levam `X-API-Key: SUA_CHAVE`.

| O quê | Chamada |
|---|---|
| Próximo prompt (marca como usado) | `GET /api/next` (`?product_id=`, `?job_id=`, `?peek=true`) |
| Próximo lote inteiro (ex.: 3×7 s) | `GET /api/next-batch` |
| Gerar sob demanda | `POST /api/generate` `{"product_id":1,"count":3,"seconds":7,"mode":"separate"}` |
| Push automático | campo **Webhook** do agente: a cada rodada recebe um POST JSON `prompts.created` |
| Documentação | `/docs` e `/openapi.json` |

Para ChatGPT/Gemini/Claude: o `/openapi.json` serve para criar uma *Action* de GPT personalizado. Para extensão de navegador ou outra plataforma, basta ela chamar `/api/next-batch` com a chave.

## Custo e segurança

- Cada rodada = 1 chamada ao Claude por produto. Rode `max_backlog` e intervalos com cuidado: 10 min × 24 h = 144 rodadas/dia.
- Nunca exponha a porta 8000 sem HTTPS. Troque a `API_KEY` por algo longo.
- Backup: tudo (produtos, imagens, prompts) fica no Postgres.

## Teste sem gastar IA

`MOCK_LLM=1` no `.env` gera textos falsos só para testar o fluxo.
