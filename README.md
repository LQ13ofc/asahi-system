# asahi-system

Fonte versionada da configuração do Fedora Asahi no MacBook Air M1 2020 (Apple M1, 8 GB, `aarch64`, Fedora Asahi Remix 44).

Este repositório é a fonte principal do sistema. A interface Quickshell/Niri está em [LQ13ofc/quickshell-](https://github.com/LQ13ofc/quickshell-) e continua separada. A arquitetura, os limites do baseline e o fluxo Codex Cloud → GitHub → M1 estão em [`docs/architecture-and-workflow.md`](docs/architecture-and-workflow.md).

## Estado atual

O conteúdo atual é a captura de referência em [`hardware/mba-m1-8gb/baseline/`](hardware/mba-m1-8gb/baseline/). Ainda não há configuração declarativa de sessões nem implementação das sessões Niri Performance e Gaming. Plasma permanece instalado como recuperação.

## Validação no Cloud

As verificações abaixo usam apenas Python padrão e não mudam o sistema:

```bash
python3 scripts/validate_baseline.py
python3 -m unittest discover -s tests -v
```

O GitHub Actions executa as mesmas verificações em PRs e em atualizações de `main`. Elas conferem estrutura e consistência do snapshot; não substituem testes no Fedora Asahi real.

