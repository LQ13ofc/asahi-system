# asahi-system

Fonte versionada da configuração do Fedora Asahi no MacBook Air M1 2020 (Apple M1, 8 GB, `aarch64`, Fedora Asahi Remix 44).

Este repositório é a fonte principal do sistema. A interface Quickshell/Niri está em [LQ13ofc/quickshell-](https://github.com/LQ13ofc/quickshell-) e continua separada. A arquitetura, os limites do baseline e o fluxo Codex Cloud → GitHub → M1 estão em [`docs/architecture-and-workflow.md`](docs/architecture-and-workflow.md).

## Estado atual

O conteúdo inclui a captura de referência em [`hardware/mba-m1-8gb/baseline/`](hardware/mba-m1-8gb/baseline/), infraestrutura read-only de benchmark e a sessão Niri reversível integrada ao Quickshell. O release gate de infraestrutura Niri+ passou no M1 real em 7 de outubro de 2026: readiness e runtime Wayland, PolicyKit, lifecycle Quickshell com uma única instância e zero restarts, isolamento KDE/Plasma, checksums dos managed files, alinhamento do checkout com `main` e snapshot/pin/runtime foram confirmados por `niri+ doctor`. Os bugs observados durante o gate — race de startup Wayland, serviços KDE/PIM vazando no Niri, migração de snapshot 0.1.1, criação do bare Git store e `__pycache__` no snapshot verificado — foram corrigidos. O fluxo normal de instalação é `sudo niri+ install`. Gaming Mode exige Gamescope ativo dentro do Niri; jogos no Niri sem Gamescope são uso normal, não Gaming Mode. Plasma permanece como recovery/fallback e não é alvo de otimização.

O desenho e pesquisa de pacotes da sessão Niri estão em [`docs/niri-phase-b.md`](docs/niri-phase-b.md). A fronteira, pin e lifecycle de Quickshell estão em [`docs/quickshell-integration.md`](docs/quickshell-integration.md). A CLI pública é `niri+`; `status`, `doctor` e `install --dry-run` são read-only. O hardened installer/snapshot e os fixes do gate estão mergeados em `main`; o gate de infraestrutura foi validado no M1. Testes funcionais separados de Fuzzel, áudio, rede, suspend/resume e retorno ao Plasma continuam pendentes onde ainda não houver evidência específica; eles não reabrem o gate de infraestrutura.

## Benchmark read-only

O protocolo A/B/C está em [`docs/benchmark-protocol.md`](docs/benchmark-protocol.md), e a auditoria estática do custo idle do `asahi-system` + Quickshell pinado está em [`docs/idle-performance-static-audit.md`](docs/idle-performance-static-audit.md). A classificação observacional dos serviços do snapshot continua em [`docs/service-classification.md`](docs/service-classification.md). `niri+ benchmark --profile plasma|niri-core|niri-quickshell` usa o collector existente com validação fail-closed e deltas numa janela de observação. A coleta é read-only; somente a preparação explícita de `niri-core` usa um mask **runtime** reversível do unit Quickshell, com restore dedicado e sem editar configuração persistente. Nenhuma captura Cloud x86_64 substitui teste no M1.

## Validação no Cloud

As verificações abaixo usam Python e o parser KDL isolado listado em `requirements-test.txt`; não mudam o sistema:

```bash
python3 scripts/validate_baseline.py
python3 -m unittest discover -s tests -v
```

Instale a dependência de validação em um ambiente virtual antes de rodar os testes: `python3 -m pip install -r requirements-test.txt`.

O GitHub Actions executa as mesmas verificações em PRs e em atualizações de `main`. Elas conferem estrutura e consistência do snapshot; não substituem testes no Fedora Asahi real.

Para navegação local das relações entre fontes Python, há uma integração opcional e pinada do Graphify em [`docs/graphify.md`](docs/graphify.md). Ela não é dependência do sistema instalado.

A integração externa do Quickshell, seu pin, pacote ARM64, lifecycle de sessão, dependências e diagnóstico do autostart KDE estão em [`docs/quickshell-integration.md`](docs/quickshell-integration.md).
