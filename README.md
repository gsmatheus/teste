# orq-stt — transcrição de voz (pt-BR) na própria VPS

Servidor pequeno que recebe um clipe de áudio e devolve o texto. Roda o `faster-whisper`
(`large-v3-turbo`, int8) **só em CPU**, com filtro de voz (VAD) para não inventar texto no silêncio.
A API é a mesma de transcrição da OpenAI, então qualquer cliente que fale com ela serve.

## Subir no Dokploy

1. Aplicação nova a partir deste repositório, **Build Type: Dockerfile** (`Dockerfile` na raiz, contexto `/`). Nunca Compose.
2. **Porta** do container: `8000`.
3. **Volume** nomeado montado em `/data` (guarda o modelo, ~1,6 GB, para não baixar a cada deploy).
4. **Variáveis de ambiente** (aba Environment):

   | Variável | Exemplo | Para quê |
   |---|---|---|
   | `STT_TOKENS` | `matheus:<token>,ana:<token>` | **Obrigatória.** Uma pessoa por item (`nome:token`), token com 16+ caracteres. Gere com `openssl rand -hex 24`. |
   | `STT_THREADS` | `8` | Núcleos usados pela transcrição. |
   | `STT_MAX_SECONDS` | `90` | Teto de duração do áudio. |
   | `STT_MAX_BYTES` | `8388608` | Teto do arquivo (8 MB). |
   | `STT_MAX_QUEUE` | `2` | Rodando + esperando; acima disso responde 429. |
   | `STT_RATE_PER_MIN` | `10` | Pedidos por minuto, por pessoa. |
   | `STT_LANGUAGE` | `pt` | Idioma padrão quando o cliente não manda. |
   | `STT_MODEL` | `large-v3-turbo` | Trocar por `large-v3` se o turbo errar termos técnicos (mais lento). |

5. **Recursos** (Advanced → Resources): limite de CPU em `8` (a VPS roda outros serviços).
6. **Domínio** com HTTPS (Traefik/Let's Encrypt) apontando para a porta `8000`.

O primeiro start baixa o modelo (alguns minutos); depois ele fica no volume.

## Usar

```bash
curl -s https://SEU-DOMINIO/v1/audio/transcriptions \
  -H "Authorization: Bearer SEU_TOKEN" \
  -F file=@clipe.ogg -F language=pt -F prompt="commit, deploy, nomes de funções"
# {"text":"...","duration":10.61}
```

- `prompt` (opcional, até 600 caracteres): vocabulário que ajuda a grafar termos técnicos.
- `response_format=text` devolve só o texto.
- `GET /healthz` não precisa de token.

## O que ele recusa

| Situação | Resposta |
|---|---|
| Sem token ou token errado | 401 |
| Arquivo maior que o teto, ou áudio mais longo que o teto | 413 |
| Arquivo vazio ou que não é áudio | 400 (sem detalhes do erro) |
| Fila cheia ou muitos pedidos da mesma pessoa | 429 com `Retry-After` |

Uma transcrição por vez. O áudio vive só em `/tmp` durante o pedido e é apagado no fim; texto e áudio
nunca vão para o log.

## Testes

```bash
docker build -t orq-stt .
docker run --rm --user root -v "$PWD":/t -w /t orq-stt sh -c "pip install -q pytest httpx && python -m pytest -q tests"
```

Os testes cobrem token, tetos, fila e limite por pessoa, sem baixar modelo. A transcrição de verdade foi
conferida à parte com áudios reais.

## Notas

- `av` fica abaixo da versão 16: a 19 quebra o `faster-whisper` 1.2.1 (`metadata_errors`).
- O token fica no servidor; guarde o de cada pessoa no cofre do app, nunca na tela.
- O áudio passa por HTTPS até a VPS e não vai para terceiros.
