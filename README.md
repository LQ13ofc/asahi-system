# asahi-system

Fonte versionada da configuração do Fedora Asahi no MacBook Air M1 2020 (Apple M1, 8 GB, `aarch64`, Fedora Asahi Remix 44).

Este repositório é a fonte principal do sistema. A interface Quickshell/Niri está em [LQ13ofc/quickshell-](https://github.com/LQ13ofc/quickshell-) e continua separada. A arquitetura, os limites do baseline e o fluxo Codex Cloud → GitHub → M1 estão em [`docs/architecture-and-workflow.md`](docs/architecture-and-workflow.md).

## Estado atual

O conteúdo atual inclui a captura de referência em [`hardware/mba-m1-8gb/baseline/`](hardware/mba-m1-8gb/baseline/), infraestrutura read-only de benchmark e preparação reversível da sessão Niri. A integração visual Quickshell está sendo revisada em Phase B2, fixada em commit separado e controlada pelo lifecycle da sessão; ainda não foi aplicada no Mac. Gaming Mode exige Gamescope ativo dentro do Niri; jogos no Niri sem Gamescope são uso normal, não Gaming Mode. Plasma permanece como recovery/fallback e não é alvo de otimização.

O desenho e pesquisa de pacotes da sessão Niri estão em [`docs/niri-phase-b.md`](docs/niri-phase-b.md). A fronteira, pin e lifecycle de Quickshell estão em [`docs/quickshell-integration.md`](docs/quickshell-integration.md). A CLI pública é `niri+`; depois de clonar com submodules e preparar a CLI pelo bootstrap, use `niri+ status`, `niri+ install --dry-run` ou `sudo niri+ install`. A instalação tem backup e rollback e ainda não foi executada no Mac.

## Benchmark read-only

O protocolo idle/A-B está em [`docs/benchmark-protocol.md`](docs/benchmark-protocol.md); a classificação observacional dos serviços do snapshot está em [`docs/service-classification.md`](docs/service-classification.md). Após o bootstrap, `niri+ benchmark` é a interface pública para o coletor read-only interno. Ele produz JSON parcial por métrica e não altera serviços, sysctls ou configuração do sistema. Nenhuma captura Cloud x86_64 substitui teste no M1.

## Validação no Cloud

As verificações abaixo usam Python e o parser KDL isolado listado em `requirements-test.txt`; não mudam o sistema:

```bash
python3 scripts/validate_baseline.py
python3 -m unittest discover -s tests -v
```

Instale a dependência de validação em um ambiente virtual antes de rodar os testes: `python3 -m pip install -r requirements-test.txt`.

O GitHub Actions executa as mesmas verificações em PRs e em atualizações de `main`. Elas conferem estrutura e consistência do snapshot; não substituem testes no Fedora Asahi real.

A integração externa do Quickshell, seu pin, pacote ARM64, lifecycle de sessão, dependências e diagnóstico do autostart KDE estão em [`docs/quickshell-integration.md`](docs/quickshell-integration.md).
