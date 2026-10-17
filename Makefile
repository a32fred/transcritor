.DEFAULT_GOAL := help
PYTHON ?= python

.PHONY: help install test test-fast cov lint format clean doctor

help: ## Lista os atalhos de desenvolvimento.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Instala as dependências e o pacote em modo editável.
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install -e .

# Dependência de desenvolvimento: python -m pip install pytest
test: ## Executa a suíte sem baixar modelos nem exigir GPU.
	$(PYTHON) -m pytest tests/ -q

test-fast: ## Executa os testes e para na primeira falha.
	$(PYTHON) -m pytest tests/ -x -q

# Dependência opcional: python -m pip install pytest-cov
cov: ## Executa testes com cobertura, quando pytest-cov estiver disponível.
	@if $(PYTHON) -c "import pytest_cov" >/dev/null 2>&1; then \
		$(PYTHON) -m pytest tests/ -q --cov=transcritor --cov-report=term-missing; \
	else \
		echo "pytest-cov não instalado; executando sem cobertura. Instale com: $(PYTHON) -m pip install pytest-cov"; \
		$(PYTHON) -m pytest tests/ -q; \
	fi

# Dependência opcional: python -m pip install ruff
lint: ## Verifica código e testes com Ruff, se instalado.
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then \
		$(PYTHON) -m ruff check transcritor tests; \
	else \
		echo "Ruff não instalado; lint não executado. Instale com: $(PYTHON) -m pip install ruff"; \
	fi

format: ## Formata código e testes com Ruff, se instalado.
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then \
		$(PYTHON) -m ruff format transcritor tests; \
	else \
		echo "Ruff não instalado; formatação não executada. Instale com: $(PYTHON) -m pip install ruff"; \
	fi

clean: ## Remove caches Python e pytest, sem tocar em modelos ou var/.
	find transcritor tests -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf -- {} +
	find transcritor tests -type f -name '*.pyc' -delete
	rm -rf -- .pytest_cache __pycache__

doctor: ## Diagnostica o ambiente local (pode apontar modelo ausente).
	$(PYTHON) -m transcritor doctor
