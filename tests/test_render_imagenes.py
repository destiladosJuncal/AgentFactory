"""El WebView no carga file:// ni rutas relativas al workspace; el puente incrusta
las imágenes locales como data: base64 al renderizar. Acá se valida eso."""

_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da6360000002000154a24f5f0000000049454e44ae426082")


def _puente():
    from webui.puente import Puente
    return Puente.__new__(Puente)   # sin __init__: no registra hooks


def test_imagen_local_se_incrusta(tmp_path):
    (tmp_path / "graf.png").write_bytes(_PNG_1x1)

    class FakeChat:
        workspace_dir = tmp_path
        conversacion_dir = tmp_path

    html = _puente()._render_md("![graf](graf.png)", FakeChat())
    assert "data:image/png;base64," in html
    assert "graf.png" not in html.split("base64,")[0][-60:]  # ya no es la ruta cruda


def test_imagen_remota_no_se_toca(tmp_path):
    class FakeChat:
        workspace_dir = tmp_path
        conversacion_dir = tmp_path
    html = _puente()._render_md("![x](https://ej.com/x.png)", FakeChat())
    assert "https://ej.com/x.png" in html and "data:" not in html


def test_file_url_al_workspace_se_incrusta(tmp_path):
    (tmp_path / "a.png").write_bytes(_PNG_1x1)

    class FakeChat:
        workspace_dir = tmp_path
        conversacion_dir = tmp_path
    url = f"file://{tmp_path}/a.png"
    html = _puente()._render_md(f"![a]({url})", FakeChat())
    assert "data:image/png;base64," in html


def test_imagen_faltante_queda_igual(tmp_path):
    class FakeChat:
        workspace_dir = tmp_path
        conversacion_dir = tmp_path
    html = _puente()._render_md("![no](no_existe.png)", FakeChat())
    assert "no_existe.png" in html and "data:" not in html
