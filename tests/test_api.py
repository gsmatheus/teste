"""Auth, tetos e fila, sem modelo (a transcrição é trocada por uma falsa)."""
import os
import threading
import time

os.environ["STT_TOKENS"] = "ana:aaaaaaaaaaaaaaaaaaaa,beto:bbbbbbbbbbbbbbbbbbbb"
os.environ["STT_MAX_BYTES"] = "1000"
os.environ["STT_RATE_PER_MIN"] = "3"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

main.load_model = lambda: object()  # não baixa modelo nos testes
client = TestClient(main.app)
A = {"Authorization": "Bearer aaaaaaaaaaaaaaaaaaaa"}


def fake(delay=0.0, text="oi"):
    def _f(path, language, prompt):
        time.sleep(delay)
        return text, 2.0
    return _f


def post(headers=A, data=b"x" * 10, **form):
    return client.post("/v1/audio/transcriptions", headers=headers, files={"file": ("a.ogg", data)}, data=form)


def reset():
    main._hits.clear()
    main._pending = 0


def test_sem_token_ou_token_errado_401():
    reset(); main.transcribe_file = fake()
    assert post(headers={}).status_code == 401
    assert post(headers={"Authorization": "Bearer errado"}).status_code == 401
    assert post(headers={"Authorization": "Basic aaaaaaaaaaaaaaaaaaaa"}).status_code == 401


def test_ok_json_e_text():
    reset(); main.transcribe_file = fake(text="deploy do commit")
    r = post(language="pt", prompt="commit, deploy")
    assert r.status_code == 200 and r.json() == {"text": "deploy do commit", "duration": 2.0}
    r = post(response_format="text")
    assert r.text == "deploy do commit"


def test_arquivo_grande_e_vazio():
    reset(); main.transcribe_file = fake()
    assert post(data=b"x" * 1001).status_code == 413
    assert post(data=b"").status_code == 400


def test_arquivo_apagado_ao_fim():
    reset(); main.transcribe_file = fake()
    antes = {f for f in os.listdir("/tmp") if f.startswith("stt-")}
    post()
    assert {f for f in os.listdir("/tmp") if f.startswith("stt-")} == antes


def test_limite_por_token_e_por_pessoa():
    reset(); main.transcribe_file = fake()
    assert [post().status_code for _ in range(4)] == [200, 200, 200, 429]
    beto = {"Authorization": "Bearer bbbbbbbbbbbbbbbbbbbb"}
    assert post(headers=beto).status_code == 200  # o limite é de cada pessoa


def test_fila_cheia_429():
    reset(); main.MAX_QUEUE = 2; main.transcribe_file = fake(delay=0.6)
    out = []
    ts = [threading.Thread(target=lambda: out.append(post().status_code)) for _ in range(3)]
    main.RATE_PER_MIN = 99
    for t in ts:
        t.start(); time.sleep(0.05)
    for t in ts:
        t.join()
    assert sorted(out) == [200, 200, 429]


def test_erro_de_decodificacao_vira_400():
    reset()
    def boom(path, language, prompt):
        raise RuntimeError("av quebrou com /tmp/x e o texto secreto")
    main.transcribe_file = boom
    r = post()
    assert r.status_code == 400 and "secreto" not in r.text


def test_tokens_curtos_recusados():
    import pytest
    with pytest.raises(SystemExit):
        main.parse_tokens("ana:curto")
