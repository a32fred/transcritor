"""Empacotamento: o que é publicado tem que bater com o que está no código.

Estes testes não constroem a wheel (isso é papel do CI, no trabalho
``empacotamento``): aqui só se confere, em segundos e sem rede, as combinações
que costumam sair de sincronia sem ninguém notar — versão, dependências e os
comandos que a instalação cria.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:  # tomllib é do Python 3.11+; no 3.10 estes testes são pulados
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

RAIZ = Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.skipif(tomllib is None, reason="tomllib exige Python 3.11+"),
    # A suíte também roda contra o pacote instalado (fora do repositório), para
    # provar que a wheel publicada funciona: ali não existe pyproject.toml.
    pytest.mark.skipif(
        not (RAIZ / "pyproject.toml").exists(),
        reason="testes de layout do repositório",
    ),
]


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


def _nome_do_requisito(texto: str) -> str:
    """'faster-whisper>=1.1.0' -> 'faster-whisper'."""
    return re.split(r"[<>=!~\[;( ]", texto.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _nomes(requisitos) -> set[str]:
    return {_nome_do_requisito(r) for r in requisitos if r.strip()}


# --------------------------------------------------------------------------

def test_versao_do_pyproject_bate_com_o_pacote():
    """Versão em dois lugares: se divergirem, o PyPI publica a errada."""
    from transcritor import __version__

    assert _pyproject()["project"]["version"] == __version__


def test_requirements_espelha_as_dependencias_do_pyproject():
    """Duas listas de dependências: sair de sincronia quebra o CI, não o código."""
    linhas = [
        linha for linha in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith(("#", "-"))
    ]

    assert _nomes(linhas) == _nomes(_pyproject()["project"]["dependencies"])


def test_comandos_instalados_estao_declarados():
    """`transcrever` é o nome que as pessoas digitam; `transcritor` é o pacote."""
    scripts = _pyproject()["project"]["scripts"]
    assert set(scripts) == {"transcrever", "transcritor"}
    assert scripts["transcrever"] == "transcritor.cli:main"


def test_dados_do_pacote_entram_na_wheel():
    """Sem estas linhas o ícone e a página do navegador não são publicados."""
    dados = _pyproject()["tool"]["setuptools"]["package-data"]

    assert any(p.endswith("data/*.desktop") for p in dados["transcritor"])
    assert any(p.endswith("data/*.svg") for p in dados["transcritor"])
    assert any(p.endswith("templates/*.html") for p in dados["transcritor.web"])


def test_licenca_e_leia_do_arquivo_do_repositorio():
    """PEP 639: a licença vai como expressão SPDX, e o texto vem do LICENSE."""
    projeto = _pyproject()["project"]

    assert projeto["license"] == "MIT"
    assert projeto["license-files"] == ["LICENSE"]
    assert (RAIZ / "LICENSE").exists()
    assert "MIT License" in (RAIZ / "LICENSE").read_text(encoding="utf-8")


def test_python_minimo_do_pyproject_e_o_mesmo_anunciado_no_readme():
    """O README promete 3.10+; o pacote tem que aceitar exatamente isso."""
    projeto = _pyproject()["project"]
    assert projeto["requires-python"] == ">=3.10"

    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Python 3.10" in readme
