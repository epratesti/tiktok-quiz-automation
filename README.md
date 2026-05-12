# TikTok Viral Quiz Automation

Sistema Python para gerar automaticamente videos verticais de quiz em estilo TikTok, com perguntas em multiplas fontes, narracao IA, renderizacao 1080x1920, legendas, thumbnails, analytics e publicacao opcional.

## Arquitetura

O projeto funciona como um pipeline modular:

1. `generate_questions.py` busca perguntas em cascata: base local JSON, Open Trivia DB, OpenAI e gerador proprio. O historico em `data/question_history.json` evita repeticoes recentes.
2. `generate_voice.py` monta o roteiro cronometrado e gera narracao por Edge TTS, gTTS ou ElevenLabs. Se a rede ou TTS falhar, o pipeline continua com silencio no trecho afetado.
3. `create_video.py` renderiza video vertical 9:16 com MoviePy, Pillow e Pydub: fundo procedural neon ou assets locais, texto grande, alternativas, contador lateral, barra de progresso, suspense, revelacao e CTA.
4. `subtitles.py` cria legendas `.srt` sincronizadas a partir do roteiro.
5. `hashtags.py` cria legenda e hashtags por categoria.
6. `upload_tiktok.py` faz publicacao opcional com Playwright usando sessao salva. O padrao e `DRY_RUN=true`.
7. `main.py` orquestra tudo, salva artefatos em `output/` e registra analytics em `data/analytics.jsonl`.
8. `.github/workflows/tiktok-quiz-automation.yml` executa 4 vezes por dia e gera 2 videos por execucao.

## Bibliotecas

- Python: linguagem principal.
- MoviePy: composicao e renderizacao do video final.
- FFmpeg: encoder de video/audio usado pelo MoviePy e Pydub.
- Pillow: textos, paines, thumbnails e frames visuais.
- OpenCV: dependencia pronta para expansao com efeitos e assets de video.
- Playwright: upload opcional via navegador autenticado.
- edge-tts, gTTS, ElevenLabs via requests: narracao IA.
- Pydub: mixagem de voz, musica, fade e volume.
- Requests: Open Trivia DB e ElevenLabs.
- python-dotenv: configuracao por `.env`.
- schedule: scheduler local opcional.

## Fluxo completo

Execute `python main.py --videos 2`. O sistema escolhe perguntas novas, cria um CTA, gera audio, monta o video de 60 segundos, cria `.srt`, gera thumbnail e registra tudo no analytics. Se `TIKTOK_UPLOAD_ENABLED=true` e `DRY_RUN=false`, tenta publicar no TikTok com Playwright usando `data/tiktok_state.json`.

Estrutura do video:

- 0s a 5s: abertura chamativa com hook.
- 5s a 45s: pergunta, alternativas, musica, fundo animado, cronometro lateral e barra de progresso.
- 45s a 55s: suspense.
- 55s a 60s: resposta correta e CTA.

## TikTok e seguranca

O publicador foi desenhado com limites conservadores: delays, retries, logs seguros e upload desligado por padrao. Ele nao tenta burlar captcha, deteccao, desafios de login ou limites da plataforma. Para reduzir risco operacional:

- use conta propria e autorizada;
- evite postar conteudo duplicado;
- mantenha `VIDEOS_PER_RUN=2`;
- respeite intervalos reais de publicacao;
- prefira APIs oficiais quando disponiveis para seu caso;
- mantenha credenciais em GitHub Secrets, nunca no codigo.

## GitHub Actions

O workflow roda nos horarios `00, 06, 12 e 18 UTC`, equivalentes a `21, 03, 09 e 15` no horario de Brasilia. Cada execucao instala dependencias, instala FFmpeg e Chromium, restaura a sessao TikTok se existir, executa `python main.py --videos 2` e salva `output/`, `logs/` e `data/analytics.jsonl` como artefatos.

Secrets recomendados:

- `DRY_RUN`: `true` inicialmente.
- `TIKTOK_UPLOAD_ENABLED`: `false` ate testar tudo.
- `TIKTOK_STORAGE_STATE_B64`: conteudo base64 do arquivo de sessao Playwright.
- `OPENAI_API_KEY`, `OPENAI_ENABLED`, `OPENAI_MODEL`.
- `VOICE_PROVIDER`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` se usar ElevenLabs.

## Arquivos

- `main.py`: entrada principal.
- `config.py`: configuracoes, paths e variaveis de ambiente.
- `generate_questions.py`: fontes de perguntas e anti-repeticao.
- `generate_voice.py`: roteiro e narracao IA.
- `create_video.py`: renderizacao do video, audio, SRT e thumbnail.
- `upload_tiktok.py`: upload opcional via Playwright.
- `scheduler.py`: scheduler local.
- `hashtags.py`: captions e hashtags.
- `subtitles.py`: legendas sincronizadas.
- `effects.py`: fundos, textos, cronometro e efeitos visuais.
- `data/questions.json`: base inicial de perguntas.
- `requirements.txt`: dependencias Python.
- `.env` e `.env.example`: configuracao local.
- `.github/workflows/tiktok-quiz-automation.yml`: automacao do GitHub Actions.
- `assets/`, `music/`, `fonts/`, `voices/`, `backgrounds/`, `output/`, `temp/`, `logs/`, `data/`: pastas operacionais.

## Instalacao local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

Instale o FFmpeg e confirme:

```bash
ffmpeg -version
```

No Windows, uma opcao simples e instalar via winget:

```bash
winget install Gyan.FFmpeg
```

## Configuracao

Edite `.env`:

```env
DRY_RUN=true
TIKTOK_UPLOAD_ENABLED=false
VOICE_PROVIDER=edge
OPENAI_ENABLED=false
OPENTRIVIA_ENABLED=true
VIDEOS_PER_RUN=2
```

Para usar OpenAI:

```env
OPENAI_ENABLED=true
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

Para usar ElevenLabs:

```env
VOICE_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

## Como executar

Gerar 2 videos sem upload:

```bash
python main.py --videos 2 --no-upload
```

Gerar com configuracao padrao:

```bash
python main.py
```

Rodar scheduler local:

```bash
python scheduler.py
```

## Assets opcionais

Coloque musicas em `music/` (`.mp3`, `.wav`, `.m4a`, `.ogg`) para trilha aleatoria. Coloque fundos em `backgrounds/` (`.mp4`, `.mov`, `.webm`, `.jpg`, `.png`) para substituir o fundo procedural. Fontes `.ttf` podem ser colocadas em `fonts/`.

## Sessao Playwright do TikTok

O upload automatico requer uma sessao Playwright previamente autenticada em `data/tiktok_state.json`. Crie essa sessao manualmente em ambiente seguro e salve o estado do navegador. No GitHub Actions, converta para base64 e salve em `TIKTOK_STORAGE_STATE_B64`.

Mantenha `DRY_RUN=true` ate validar os videos e a conta.

## Analytics

Cada video gera uma linha JSON em `data/analytics.jsonl` com:

- horario de criacao;
- pergunta e fonte;
- paths dos artefatos;
- caption;
- status do upload.

Esse arquivo pode ser cruzado depois com metricas reais do TikTok para otimizar categorias, hooks e CTAs.
