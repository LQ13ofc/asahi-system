# asahi-system

Fonte versionada da configuração do Fedora Asahi no MacBook Air M1 2020 (Apple M1, 8 GB, `aarch64`, Fedora Asahi Remix 44).

Este repositório é a fonte principal do sistema. A interface Quickshell/Niri está em [LQ13ofc/quickshell-](https://github.com/LQ13ofc/quickshell-) e continua separada. A arquitetura, os limites do baseline e o fluxo Codex Cloud → GitHub → M1 estão em [`docs/architecture-and-workflow.md`](docs/architecture-and-workflow.md).

## Estado atual

O conteúdo atual inclui a captura de referência em [`hardware/mba-m1-8gb/baseline/`](hardware/mba-m1-8gb/baseline/) e infraestrutura de benchmark read-only. Ainda não há configuração executável da sessão Niri Performance ou de Gaming Mode. Niri é o objetivo da sessão otimizada; Gaming Mode exige Gamescope ativo dentro do Niri. Plasma permanece apenas como recovery/fallback e não é alvo de otimização.

## Benchmark read-only

O protocolo idle/A-B está em [`docs/benchmark-protocol.md`](docs/benchmark-protocol.md); a classificação observacional dos serviços do snapshot está em [`docs/service-classification.md`](docs/service-classification.md). O coletor `scripts/collect-performance-baseline` produz JSON parcial com status por métrica e não altera serviços, sysctls ou arquivos do sistema. Para uma captura comparável, siga o protocolo; nenhuma captura Cloud x86_64 substitui teste no M1.

## Validação no Cloud

As verificações abaixo usam apenas Python padrão e não mudam o sistema:

```bash
python3 scripts/validate_baseline.py
python3 -m unittest discover -s tests -v
```

O GitHub Actions executa as mesmas verificações em PRs e em atualizações de `main`. Elas conferem estrutura e consistência do snapshot; não substituem testes no Fedora Asahi real.
