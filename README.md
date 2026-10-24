# Prompt Agent

Painel + API + agente autônomo que gera prompts de vídeo para os seus produtos campeões, em intervalos que você configura. Roda numa VPS com Docker.

## Subir na VPS

```bash
unzip prompt-agent.zip && cd prompt-agent
cp .env.example .env        # edite: ANTHROPIC_API_KEY e API_KEY
docker compose up -d --build
```

O painel fica em `http://127.0.0.1:8000`. Para acessar de fora com HTTPS (obrigatório, porque a API key trafega), use o Caddy:

```
# /etc/caddy/Caddyfile
prompts.seudominio.com.br {
    reverse_proxy 127.0.0.1:8000
}
```

Entre no painel e cole a `API_KEY` do `.env`.

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
- Backup: a pasta `data/` guarda banco e imagens.

## Teste sem gastar IA

`MOCK_LLM=1` no `.env` gera textos falsos só para testar o fluxo.
